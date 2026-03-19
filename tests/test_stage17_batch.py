from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal
from app.services.research.stage17_batch import build_stage17_batch_report


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage17_batch_runs_cycle_and_tail_report() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="batch-tail-1",
        title="Will Bitcoin reach $150000 by Dec 31, 2026?",
        probability_yes=0.05,
        status="active",
        volume_24h=50_000.0,
        liquidity_value=100_000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        title="Tail event candidate",
        summary="tail candidate",
        signal_mode="tail_stability",
        signal_direction="YES",
        metadata_json={
            "tail_category": "price_target",
            "tail_mispricing_ratio": 2.5,
            "tail_our_prob": 0.12,
            "tail_market_prob": 0.05,
            "reason_codes": ["tail_category:price_target"],
        },
    )
    db.add(signal)
    db.commit()

    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_reference_balance_usd": 1000.0,
            "signal_tail_notional_pct": 0.005,
        }
    )
    report = build_stage17_batch_report(db, settings=settings, days=60, cycle_limit=5)
    reports = dict(report.get("reports") or {})
    assert "stage17_cycle" in reports
    assert "stage17_tail_report" in reports
    assert int((reports["stage17_cycle"] or {}).get("opened") or 0) >= 1
from datetime import UTC, datetime, timedelta
