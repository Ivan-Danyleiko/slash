from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import SignalHistory
from app.services.research.signal_type_optimization import build_signal_type_optimization_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_signal_type_optimization_finds_keep_candidate() -> None:
    db = _session()
    now = datetime.now(UTC)
    # High-divergence bucket has positive outcome.
    for i in range(40):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=12, minutes=i),
                timestamp_bucket=(now - timedelta(hours=12, minutes=i)).replace(minute=0, second=0, microsecond=0),
                platform="P",
                source_tag="manifold_bets_api",
                market_id=10,
                probability_at_signal=0.40,
                probability_after_6h=0.43,
                divergence=0.12,
                liquidity=0.8,
                volume_24h=1000.0,
            )
        )
    # Low-divergence bucket has weak/negative outcome.
    for i in range(40):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=11, minutes=i),
                timestamp_bucket=(now - timedelta(hours=11, minutes=i)).replace(minute=0, second=0, microsecond=0),
                platform="P",
                source_tag="manifold_bets_api",
                market_id=11,
                probability_at_signal=0.52,
                probability_after_6h=0.51,
                divergence=0.02,
                liquidity=0.2,
                volume_24h=80.0,
            )
        )
    db.commit()

    report = build_signal_type_optimization_report(
        db,
        days=3,
        horizon="6h",
        signal_type="DIVERGENCE",
        source_tags=["all", "manifold_bets_api"],
        divergence_thresholds=[0.0, 0.08],
        liquidity_thresholds=[0.0, 0.5],
        volume_thresholds=[0.0, 100.0],
        min_labeled_returns=20,
        max_candidates=10,
    )
    assert report["decision"] in {"KEEP", "MODIFY"}
    assert report["best_candidate"] is not None
    assert report["best_candidate"]["returns_labeled"] >= 20


def test_signal_type_optimization_reports_problems_when_no_actionable() -> None:
    db = _session()
    now = datetime.now(UTC)
    for i in range(30):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.RULES_RISK,
                timestamp=now - timedelta(hours=10, minutes=i),
                timestamp_bucket=(now - timedelta(hours=10, minutes=i)).replace(minute=0, second=0, microsecond=0),
                platform="P",
                source_tag="manifold_bets_api",
                market_id=20,
                probability_at_signal=0.60,
                probability_after_6h=0.58,
                divergence=0.03,
                liquidity=0.3,
                volume_24h=120.0,
            )
        )
    db.commit()

    report = build_signal_type_optimization_report(
        db,
        days=3,
        horizon="6h",
        signal_type="RULES_RISK",
        source_tags=["all"],
        divergence_thresholds=[0.0],
        liquidity_thresholds=[0.0],
        volume_thresholds=[0.0],
        min_labeled_returns=20,
        max_candidates=10,
    )
    assert report["decision"] == "REMOVE"
    problems = report["problem_summary"]["problems"]
    assert any("No actionable candidate" in p for p in problems)
