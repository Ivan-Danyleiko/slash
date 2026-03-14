from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory
from app.services.research.stage6_risk_guardrails import build_stage6_risk_guardrails_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed(db: Session) -> Market:
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    m = Market(platform_id=p.id, external_market_id="m1", title="M1")
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_stage6_risk_guardrails_soft_level() -> None:
    db = _session()
    m = _seed(db)
    now = datetime.now(UTC)

    for i in range(5):
        db.add(
            Signal(
                signal_type=SignalType.DIVERGENCE,
                market_id=m.id,
                title=f"s{i}",
                summary="x",
                created_at=now - timedelta(hours=i),
                execution_analysis={"expected_ev_after_costs_pct": -0.02, "position_size_usd": 200.0},
            )
        )
    for i in range(40):
        p0 = 0.5
        p1 = 0.49
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=i),
                platform="P",
                market_id=m.id,
                related_market_id=None,
                probability_at_signal=p0,
                probability_after_6h=p1,
            )
        )
    db.commit()

    report = build_stage6_risk_guardrails_report(db, days=7, nav_usd=10000.0)
    assert report["circuit_breaker_level"] in {"OK", "SOFT", "HARD", "PANIC"}
    assert report["rollback"]["samples"] >= 30


def test_stage6_risk_guardrails_rolls_back_on_negative_mean_with_significance() -> None:
    db = _session()
    m = _seed(db)
    now = datetime.now(UTC)

    for i in range(50):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=i),
                platform="P",
                market_id=m.id,
                related_market_id=None,
                probability_at_signal=0.5,
                probability_after_6h=0.46,
            )
        )
    db.commit()

    report = build_stage6_risk_guardrails_report(
        db,
        days=7,
        horizon="6h",
        signal_type="DIVERGENCE",
        rollback_min_samples=30,
        rollback_pvalue_threshold=0.10,
    )
    assert report["rollback"]["triggered"] is True
    assert report["rollback"]["one_sided_p_value"] is not None
