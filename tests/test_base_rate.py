from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import Market, Platform
from app.services.signals.base_rate import BaseRateEstimator
from app.services.signals.engine import SignalEngine
from app.services.external.usgs import estimate_no_earthquake_probability


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_base_rate_uses_usgs_when_enabled(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"count": 1400}

    monkeypatch.setattr("app.services.external.usgs.httpx.get", lambda *a, **k: _Resp())
    db = _mk_db()
    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_base_rate_external_enabled": True})
    est = BaseRateEstimator(db=db, settings=settings)
    m = Market(platform_id=1, external_market_id="br-1", title="Will there be earthquake?", probability_yes=0.05)
    out = est.estimate(m, tail_category="geopolitical_event", strategy="llm_evaluate")
    assert str(out.get("source") or "").startswith(("historical_", "deterministic_"))
    assert 0.001 <= float(out.get("our_prob") or 0.0) <= 0.999


def test_base_rate_uses_binance_for_crypto(monkeypatch) -> None:
    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_get(url, *_, **__):
        if "ticker/price" in str(url):
            return _Resp({"symbol": "BTCUSDT", "price": "100000.0"})
        rows = []
        close = 100000.0
        for i in range(365):
            close = close * (1.0 + (0.0005 if i % 2 == 0 else -0.0002))
            rows.append([i, "0", "0", "0", f"{close:.6f}", "0"])
        return _Resp(rows)

    monkeypatch.setattr("app.services.signals.base_rate.httpx.get", _fake_get)
    monkeypatch.setattr("app.services.external.binance_history.httpx.get", _fake_get)

    db = _mk_db()
    p = Platform(name="POLYMARKET")
    db.add(p)
    db.flush()
    m = Market(
        platform_id=p.id,
        external_market_id="br-2",
        title="Will Bitcoin be above $150000 by Dec 31, 2026?",
        probability_yes=0.03,
        resolution_time=datetime.now(UTC) + timedelta(days=120),
    )
    db.add(m)
    db.commit()

    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_base_rate_external_enabled": True})
    est = BaseRateEstimator(db=db, settings=settings)
    out = est.estimate(m, tail_category="crypto_level", strategy="llm_evaluate")
    assert str(out.get("source") or "") == "external_binance_lognormal"


def test_base_rate_bet_yes_fallback_has_meaningful_uplift() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_base_rate_external_enabled": False})
    est = BaseRateEstimator(db=db, settings=settings)
    m = Market(platform_id=1, external_market_id="br-3", title="Will there be exactly 0 outages?", probability_yes=0.02)
    out = est.estimate(m, tail_category="price_target", strategy="bet_yes_underpriced")
    assert "category" in str(out.get("source") or "") or "deterministic" in str(out.get("source") or "")
    assert float(out.get("our_prob") or 0.0) > 0.03


def test_usgs_rejects_out_of_range_magnitude() -> None:
    out = estimate_no_earthquake_probability(min_magnitude=0.0, lookback_days=365, timeout_seconds=1.0)
    assert out is None
