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
        title="Will there be a hurricane in Florida this week?",
        probability_yes=0.03,
        status="active",
        volume_24h=50_000.0,
        liquidity_value=100_000.0,
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        title="Tail event candidate",
        summary="tail candidate",
        signal_mode="tail_stability",
        signal_direction="NO",
        metadata_json={
            "tail_category": "natural_disaster",
            "tail_mispricing_ratio": 2.5,
            "tail_our_prob": 0.01,
            "tail_market_prob": 0.03,
            "reason_codes": ["tail_category:natural_disaster"],
        },
    )
    db.add(signal)
    db.commit()

    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_reference_balance_usd": 100.0,
            "signal_tail_notional_pct": 0.005,
        }
    )
    report = build_stage17_batch_report(db, settings=settings, days=60, cycle_limit=5)
    reports = dict(report.get("reports") or {})
    assert "stage17_cycle" in reports
    assert "stage17_tail_report" in reports
    assert int((reports["stage17_cycle"] or {}).get("opened") or 0) >= 1
