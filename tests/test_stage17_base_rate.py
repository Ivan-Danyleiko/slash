from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import Market, Platform
from app.services.signals.base_rate import BaseRateEstimator
from app.services.signals.engine import SignalEngine


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage17_crypto_base_rate_uses_binance_when_enabled(monkeypatch) -> None:
    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_get(url, *_, **__):
        if "ticker/price" in str(url):
            return _Resp({"symbol": "BTCUSDT", "price": "102000.0"})
        rows = []
        # Binance-like klines: index 4 = close
        close = 100000.0
        for i in range(365):
            close = close * (1.0 + (0.0005 if i % 2 == 0 else -0.0002))
            rows.append([i, "0", "0", "0", f"{close:.6f}", "0"])
        return _Resp(rows)

    monkeypatch.setattr("app.services.signals.base_rate.httpx.get", _fake_get)

    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m-crypto-1",
        title="Will Bitcoin be above $200000 by Dec 31, 2026?",
        probability_yes=0.03,
        resolution_time=datetime.now(UTC) + timedelta(days=120),
    )
    db.add(market)
    db.commit()

    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_base_rate_external_enabled": True})
    est = BaseRateEstimator(db=db, settings=settings)
    out = est.estimate(market, tail_category="crypto_level", strategy="llm_evaluate")
    assert str(out.get("source") or "") == "external_binance_lognormal"
    assert 0.001 <= float(out.get("our_prob") or 0.0) <= 0.999
    assert float(out.get("confidence") or 0.0) > 0.0


def test_stage17_crypto_base_rate_fallback_when_external_disabled() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m-crypto-2",
        title="Will BTC be above $150000 in 2026?",
        probability_yes=0.04,
    )
    db.add(market)
    db.commit()

    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_base_rate_external_enabled": False})
    est = BaseRateEstimator(db=db, settings=settings)
    out = est.estimate(market, tail_category="crypto_level", strategy="llm_evaluate")
    assert str(out.get("source") or "").startswith("deterministic_")
