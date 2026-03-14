from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, MarketSnapshot, Platform, SignalHistory
from app.tasks.jobs import label_signal_history_15m_job, label_signal_history_30m_job


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_subhour_labeling_jobs_store_probabilities_in_simulated_trade() -> None:
    db = _session()
    p = Platform(name="POLYMARKET", base_url="https://gamma-api.polymarket.com")
    db.add(p)
    db.commit()
    db.refresh(p)

    market = Market(
        platform_id=p.id,
        external_market_id="m1",
        title="M1",
        probability_yes=0.55,
        volume_24h=1000,
        liquidity_value=5000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    now = datetime.now(UTC)
    signal_ts = now - timedelta(hours=1)
    db.add(
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.DIVERGENCE,
            timestamp=signal_ts,
            platform="POLYMARKET",
            market_id=market.id,
            related_market_id=None,
            probability_at_signal=0.5,
            divergence=0.1,
            liquidity=0.7,
            volume_24h=1000,
        )
    )
    db.add_all(
        [
            MarketSnapshot(
                market_id=market.id,
                probability_yes=0.53,
                probability_no=0.47,
                fetched_at=signal_ts + timedelta(minutes=15),
            ),
            MarketSnapshot(
                market_id=market.id,
                probability_yes=0.54,
                probability_no=0.46,
                fetched_at=signal_ts + timedelta(minutes=30),
            ),
        ]
    )
    db.commit()

    r15 = label_signal_history_15m_job(db)
    r30 = label_signal_history_30m_job(db)

    row = db.query(SignalHistory).first()
    payload = row.simulated_trade or {}

    assert r15["status"] == "ok"
    assert r30["status"] == "ok"
    assert payload.get("probability_after_15m") == 0.53
    assert payload.get("probability_after_30m") == 0.54
