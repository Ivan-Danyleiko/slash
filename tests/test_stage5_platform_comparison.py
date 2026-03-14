from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import SignalHistory
from app.services.research.platform_comparison import build_platform_comparison_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_platform_comparison_report_ranks_platforms_by_returns() -> None:
    db = _session()
    now = datetime.now(UTC)

    # Better platform returns.
    for i in range(12):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=i),
                platform="POLYMARKET",
                market_id=1,
                probability_at_signal=0.45,
                probability_after_6h=0.48,
                divergence=0.08,
                liquidity=0.7,
                volume_24h=1000.0,
            )
        )
    # Worse platform returns.
    for i in range(12):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=7, minutes=i),
                platform="MANIFOLD",
                market_id=2,
                probability_at_signal=0.55,
                probability_after_6h=0.53,
                divergence=0.08,
                liquidity=0.7,
                volume_24h=1000.0,
            )
        )
    db.commit()

    report = build_platform_comparison_report(
        db,
        days=3,
        horizon="6h",
        signal_type="DIVERGENCE",
        min_samples=10,
    )
    assert report["platforms_total"] == 2
    assert report["best_platform"] == "POLYMARKET"
    assert report["rows"][0]["avg_return"] > report["rows"][1]["avg_return"]


def test_platform_comparison_report_rejects_unsupported_signal_type() -> None:
    db = _session()
    report = build_platform_comparison_report(db, signal_type="UNKNOWN")
    assert "error" in report
