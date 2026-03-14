from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory
from app.services.research.stage6_type35 import build_stage6_type35_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed_market(db: Session) -> Market:
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    m = Market(platform_id=p.id, external_market_id="m1", title="M1")
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_stage6_type35_reports_insufficient_architecture_when_subhour_low() -> None:
    db = _session()
    m = _seed_market(db)
    now = datetime.now(UTC)

    for i in range(40):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.LIQUIDITY_RISK,
                timestamp=now - timedelta(hours=i),
                platform="P",
                market_id=m.id,
                related_market_id=None,
                probability_at_signal=0.5,
                probability_after_6h=0.51,
            )
        )
    db.commit()

    report = build_stage6_type35_report(
        db,
        days=30,
        horizon="6h",
        min_labeled_returns=30,
        min_subhour_coverage=0.5,
    )
    row = next(r for r in report["rows"] if r["signal_type"] == SignalType.LIQUIDITY_RISK.value)
    assert row["decision"] == "INSUFFICIENT_ARCHITECTURE"


def test_stage6_type35_can_return_business_decision_when_subhour_available() -> None:
    db = _session()
    m = _seed_market(db)
    now = datetime.now(UTC)

    for i in range(40):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.WEIRD_MARKET,
                timestamp=now - timedelta(hours=i),
                platform="P",
                market_id=m.id,
                related_market_id=None,
                probability_at_signal=0.5,
                probability_after_6h=0.53,
                simulated_trade={"probability_after_15m": 0.51},
            )
        )
    db.commit()

    report = build_stage6_type35_report(
        db,
        days=30,
        horizon="6h",
        min_labeled_returns=30,
        min_subhour_coverage=0.2,
    )
    row = next(r for r in report["rows"] if r["signal_type"] == SignalType.WEIRD_MARKET.value)
    assert row["decision"] in {"KEEP", "MODIFY", "REMOVE"}
    assert row["returns_labeled"] >= 30
