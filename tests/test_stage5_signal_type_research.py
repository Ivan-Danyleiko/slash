from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import SignalHistory
from app.services.research.signal_type_research import build_signal_type_research_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_signal_type_research_returns_keep_and_remove() -> None:
    db = _session()
    now = datetime.now(UTC)

    # DIVERGENCE: strong positive returns -> KEEP/MODIFY.
    for i in range(20):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=i),
                platform="P",
                market_id=1,
                probability_at_signal=0.45,
                probability_after_6h=0.47,
                divergence=0.08,
                liquidity=0.7,
                volume_24h=1000.0,
            )
        )
    # RULES_RISK: negative returns -> REMOVE.
    for i in range(20):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.RULES_RISK,
                timestamp=now - timedelta(hours=7, minutes=i),
                platform="P",
                market_id=2,
                probability_at_signal=0.55,
                probability_after_6h=0.53,
                divergence=0.03,
                liquidity=0.6,
                volume_24h=800.0,
            )
        )
    db.commit()

    report = build_signal_type_research_report(
        db,
        days=3,
        horizon="6h",
        signal_types="DIVERGENCE,RULES_RISK",
        min_labeled_returns=10,
    )
    decisions = {r["signal_type"]: r["decision"] for r in report["rows"]}
    assert decisions["DIVERGENCE"] in {"KEEP", "MODIFY"}
    assert decisions["RULES_RISK"] == "REMOVE"


def test_signal_type_research_insufficient_data() -> None:
    db = _session()
    report = build_signal_type_research_report(
        db,
        days=3,
        horizon="6h",
        signal_types="DIVERGENCE",
        min_labeled_returns=5,
    )
    assert report["rows"][0]["decision"] == "INSUFFICIENT_DATA"
