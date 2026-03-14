from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import SignalHistory
from app.services.research.signal_lifetime import build_signal_lifetime_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_signal_lifetime_report_detects_1h_and_6h_closures() -> None:
    db = _session()
    now = datetime.now(UTC)

    # Gap closes in 1h.
    db.add(
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.DIVERGENCE,
            timestamp=now - timedelta(hours=8),
            platform="P",
            market_id=1,
            related_market_id=2,
            probability_at_signal=0.40,
            related_market_probability=0.60,
            probability_after_1h=0.51,
            probability_after_6h=0.52,
            probability_after_24h=0.55,
            divergence=0.20,
            liquidity=0.7,
            volume_24h=1000.0,
        )
    )
    # Gap closes in 6h.
    db.add(
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.DIVERGENCE,
            timestamp=now - timedelta(hours=7),
            platform="P",
            market_id=3,
            related_market_id=4,
            probability_at_signal=0.30,
            related_market_probability=0.60,
            probability_after_1h=0.35,
            probability_after_6h=0.48,
            probability_after_24h=0.55,
            divergence=0.30,
            liquidity=0.7,
            volume_24h=1000.0,
        )
    )
    db.commit()

    report = build_signal_lifetime_report(
        db,
        days=3,
        signal_type="DIVERGENCE",
        close_ratio_threshold=0.5,
        min_initial_divergence=0.02,
        min_samples=1,
    )
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["status"] == "OK"
    assert row["close_rate_1h"] >= 0.5
    assert row["close_rate_6h"] >= row["close_rate_1h"]
    assert row["median_lifetime_hours"] in {1.0, 3.5, 6.0}


def test_signal_lifetime_report_unsupported_signal_type() -> None:
    db = _session()
    report = build_signal_lifetime_report(db, signal_type="UNKNOWN")
    assert "error" in report
