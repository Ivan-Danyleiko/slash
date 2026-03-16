from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform
from app.services.agent_stage7.tools import get_cross_platform_consensus
from app.services.signals.execution import ExecutionSimulatorV2


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_kalshi_fee_formula_applied_in_execution_costs() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    kalshi = Platform(name="KALSHI", base_url="https://kalshi")
    db.add(kalshi)
    db.flush()
    market = Market(
        platform_id=kalshi.id,
        external_market_id="k1",
        title="Will CPI YoY be above 3.0?",
        probability_yes=0.5,
        volume_24h=1_000_000.0,
        liquidity_value=500_000.0,
        spread_cents=0.5,
        fetched_at=now,
        created_at=now - timedelta(days=1),
        resolution_time=now + timedelta(days=10),
        category="finance",
    )
    db.add(market)
    db.commit()

    sim = ExecutionSimulatorV2(db=db, settings=Settings())
    result = sim.simulate(
        market=market,
        confidence_score=0.6,
        liquidity_score=0.8,
        recent_move=0.05,
        signal_type=SignalType.DIVERGENCE,
    )
    # taker fee at p=0.5 is 0.0175; full costs include spread/slippage.
    assert float(result.get("expected_costs_pct") or 0.0) >= 0.0175


def test_cross_platform_consensus_is_volume_weighted() -> None:
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
                probability_yes=0.70,
                volume_24h=100000.0,
                open_interest=50000.0,
                fetched_at=now,
            ),
            Market(
                platform_id=manifold.id,
                external_market_id="m1",
                title="Will bitcoin hit 120k by end of 2026?",
                probability_yes=0.20,
                volume_24h=100.0,
                open_interest=50.0,
                fetched_at=now,
            ),
        ]
    )
    db.commit()
    result = get_cross_platform_consensus(db, "Will BTC hit 120k by end 2026?")
    weighted = float(result.get("consensus_weighted_prob") or 0.0)
    # Weighted consensus should be much closer to high-liquidity Polymarket probability.
    assert weighted > 0.60
