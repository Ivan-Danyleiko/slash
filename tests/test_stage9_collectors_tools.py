from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import Market, Platform
from app.services.agent_stage7.tools import get_cross_platform_consensus
from app.services.collectors.kalshi import KalshiCollector


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_kalshi_collector_adds_historical_reason_codes(monkeypatch) -> None:
    def _fake_get(url, **kwargs):  # noqa: ANN001
        if str(url).endswith("/cutoff"):
            return _FakeResponse(500, {})
        if str(url).endswith("/markets") and kwargs.get("params", {}).get("status") == "open":
            return _FakeResponse(
                200,
                {
                    "markets": [
                        {
                            "ticker": "KX-TEST-1",
                            "title": "Will CPI exceed 3.0?",
                            "status": "open",
                            "yes_bid_dollars": 0.48,
                            "yes_ask_dollars": 0.50,
                            "last_price_dollars": 0.49,
                            "volume_24h_fp": 1000,
                            "liquidity": 5000,
                        }
                    ]
                },
            )
        if str(url).endswith("/markets") and kwargs.get("params", {}).get("status") == "settled":
            return _FakeResponse(500, {})
        return _FakeResponse(200, {})

    monkeypatch.setattr("app.services.collectors.kalshi.httpx.get", _fake_get)
    collector = KalshiCollector()
    rows = collector.fetch_markets()
    assert len(rows) == 1
    assert float(rows[0].probability_yes or 0.0) > 0.0
    assert float(rows[0].volume_24h or 0.0) > 0.0
    payload = rows[0].source_payload or {}
    reasons = list(payload.get("historical_reason_codes") or [])
    assert "kalshi_historical_cutoff_unavailable" in reasons
    assert "kalshi_historical_markets_unavailable" in reasons


def test_consensus_returns_two_source_reason_code(monkeypatch) -> None:
    monkeypatch.delenv("METACULUS_API_TOKEN", raising=False)
    db = _mk_db()
    now = datetime.now(UTC)
    poly = Platform(name="POLYMARKET", base_url="https://poly")
    manifold = Platform(name="MANIFOLD", base_url="https://manifold")
    db.add_all([poly, manifold])
    db.flush()
    db.add_all(
        [
            Market(
                platform_id=poly.id,
                external_market_id="p1",
                title="Will BTC hit 120k in 2026?",
                probability_yes=0.7,
                volume_24h=90000,
                fetched_at=now,
            ),
            Market(
                platform_id=manifold.id,
                external_market_id="m1",
                title="Will bitcoin hit 120k by end of 2026?",
                probability_yes=0.3,
                volume_24h=200,
                fetched_at=now,
            ),
        ]
    )
    db.commit()
    result = get_cross_platform_consensus(db, "Will BTC hit 120k by end 2026?")
    reasons = list(result.get("consensus_reason_codes") or [])
    assert "consensus_two_source_mode" in reasons
    assert ("metaculus_token_missing" in reasons) or ("metaculus_search_no_match" in reasons)
