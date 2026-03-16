from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, MarketSnapshot, Platform, Signal, SignalHistory
from app.services.research.signal_history_labeler import label_signal_history_from_snapshots


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def _seed(db: Session) -> tuple[SignalHistory, Market]:
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m1",
        title="test",
        probability_yes=0.55,
        fetched_at=now,
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.DIVERGENCE,
        market_id=market.id,
        title="s",
        summary="s",
        created_at=now - timedelta(hours=8),
    )
    db.add(signal)
    db.flush()
    row = SignalHistory(
        signal_id=signal.id,
        signal_type=SignalType.DIVERGENCE,
        timestamp=now - timedelta(hours=8),
        timestamp_bucket=(now - timedelta(hours=8)).replace(minute=0, second=0, microsecond=0),
        platform="POLYMARKET",
        source_tag="local",
        market_id=market.id,
        probability_at_signal=0.5,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, market


def test_labels_from_snapshot_first_after_target() -> None:
    db = _mk_db()
    row, market = _seed(db)
    target = row.timestamp + timedelta(hours=6)
    db.add(
        MarketSnapshot(
            market_id=market.id,
            probability_yes=0.62,
            probability_no=0.38,
            fetched_at=target + timedelta(minutes=10),
        )
    )
    db.commit()

    res = label_signal_history_from_snapshots(db, horizon="6h", batch_size=100)
    assert res["status"] == "ok"
    assert res["updated"] == 1
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.probability_after_6h == 0.62


def test_no_snapshot_skips_with_reason() -> None:
    db = _mk_db()
    row, _ = _seed(db)

    res = label_signal_history_from_snapshots(db, horizon="6h", batch_size=100)
    assert res["status"] == "ok"
    assert res["updated"] == 0
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.probability_after_6h is None
    assert updated.missing_label_reason == "snapshot_6h_missing"


def test_dry_run_does_not_write() -> None:
    db = _mk_db()
    row, market = _seed(db)
    target = row.timestamp + timedelta(hours=6)
    db.add(
        MarketSnapshot(
            market_id=market.id,
            probability_yes=0.7,
            probability_no=0.3,
            fetched_at=target + timedelta(minutes=5),
        )
    )
    db.commit()

    res = label_signal_history_from_snapshots(db, horizon="6h", batch_size=100, dry_run=True)
    assert res["status"] == "ok"
    assert res["updated"] == 1
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.probability_after_6h is None


def test_repeat_run_does_not_overwrite_labeled_value() -> None:
    db = _mk_db()
    row, market = _seed(db)
    target = row.timestamp + timedelta(hours=6)
    db.add(
        MarketSnapshot(
            market_id=market.id,
            probability_yes=0.61,
            probability_no=0.39,
            fetched_at=target + timedelta(minutes=5),
        )
    )
    db.commit()

    first = label_signal_history_from_snapshots(db, horizon="6h", batch_size=100)
    second = label_signal_history_from_snapshots(db, horizon="6h", batch_size=100)
    assert first["updated"] == 1
    assert second["updated"] == 0
    updated = db.get(SignalHistory, row.id)
    assert updated is not None
    assert updated.probability_after_6h == 0.61
