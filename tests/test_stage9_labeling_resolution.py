from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory
from app.tasks.jobs import label_signal_history_resolution_job


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def _seed_base(db: Session, *, status: str = "resolved", payload: dict | None = None) -> tuple[SignalHistory, Market]:
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id=f"m-{status}",
        title="Test market",
        status=status,
        probability_yes=0.4,
        source_payload=payload or {},
        resolution_time=now - timedelta(hours=1),
        fetched_at=now,
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.DIVERGENCE,
        market_id=market.id,
        title="s",
        summary="s",
        signal_direction="NO",
        created_at=now - timedelta(hours=2),
    )
    db.add(signal)
    db.flush()
    row = SignalHistory(
        signal_id=signal.id,
        signal_type=SignalType.DIVERGENCE,
        timestamp=now - timedelta(hours=2),
        timestamp_bucket=(now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0),
        platform="POLYMARKET",
        source_tag="local",
        market_id=market.id,
        probability_at_signal=0.6,
        signal_direction="NO",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    db.refresh(market)
    return row, market


def test_direction_aware_resolution_success_for_no_direction() -> None:
    db = _mk_db()
    row, _ = _seed_base(db, payload={"result": "NO"})
    out = label_signal_history_resolution_job(db)
    assert out["status"] == "ok"
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.resolved_outcome == "NO"
    assert updated.resolved_success is True


def test_void_resolution_sets_missing_label_reason() -> None:
    db = _mk_db()
    row, _ = _seed_base(db, payload={"isVoid": True})
    out = label_signal_history_resolution_job(db)
    assert out["status"] == "ok"
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.resolved_outcome == "VOID"
    assert updated.resolved_success is None
    assert updated.missing_label_reason == "void_resolution"


def test_disputed_resolution_is_excluded() -> None:
    db = _mk_db()
    row, _ = _seed_base(db, payload={"result": "YES", "disputed": True})
    out = label_signal_history_resolution_job(db)
    assert out["status"] == "ok"
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.resolved_success is None
    assert updated.missing_label_reason == "oracle_dispute_risk"


def test_closed_with_settlement_timer_and_no_outcome_is_not_resolved() -> None:
    db = _mk_db()
    row, _ = _seed_base(
        db,
        status="closed",
        payload={"settlement_timer_seconds": 3600},
    )
    out = label_signal_history_resolution_job(db)
    assert out["status"] == "ok"
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.resolution_checked_at is None
    # Row remains pending until explicit outcome appears.
    assert updated.resolved_outcome is None
