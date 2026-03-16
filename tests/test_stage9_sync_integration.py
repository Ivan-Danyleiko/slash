from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import Market
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.sync_service import CollectorSyncService


class _FakeCollector:
    platform_name = "KALSHI"

    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        return [
            NormalizedMarketDTO(
                platform="KALSHI",
                external_market_id="KX-INT-1",
                title="Will CPI be above 3.0 by June?",
                status="open",
                probability_yes=0.53,
                probability_no=0.47,
                volume_24h=1234.0,
                liquidity_value=6789.0,
                created_at=datetime.now(UTC),
                source_payload={
                    "execution_source": "kalshi_api",
                    "spread_cents": 1.5,
                    "best_bid_yes": 0.52,
                    "best_ask_yes": 0.535,
                    "open_interest_fp": 555.0,
                    "notional_value_dollars": 10000.0,
                    "previous_yes_bid_dollars": 0.50,
                    "historical_reason_codes": ["kalshi_historical_rows_empty"],
                },
            )
        ]


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_sync_service_persists_stage9_market_fields() -> None:
    db = _mk_db()
    svc = CollectorSyncService(db)
    svc.collectors = [_FakeCollector()]
    svc.collector_map = {"kalshi": svc.collectors[0]}

    result = svc.sync_all(platform="kalshi")
    assert "KALSHI" in result
    market = db.query(Market).filter(Market.external_market_id == "KX-INT-1").first()
    assert market is not None
    assert market.execution_source == "kalshi_api"
    assert float(market.spread_cents or 0.0) > 0.0
    assert float(market.open_interest or 0.0) > 0.0
    assert float(market.previous_yes_bid or 0.0) > 0.0

