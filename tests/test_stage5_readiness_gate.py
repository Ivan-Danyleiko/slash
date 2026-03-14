from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory
from app.services.research.readiness_gate import build_stage5_readiness_gate


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_readiness_gate_fails_without_data() -> None:
    db = _session()
    report = build_stage5_readiness_gate(db, days=7, min_labeled_returns=5)
    assert report["status"] == "FAIL"
    assert "has_actionable_signal_types" in report["failed_critical_checks"]


def test_readiness_gate_pass_with_relaxed_thresholds() -> None:
    db = _session()
    now = datetime.now(UTC)
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    m = Market(platform_id=p.id, external_market_id="m1", title="Bitcoin price above 100000 in 2026?")
    db.add(m)
    db.commit()
    db.refresh(m)
    signal = Signal(
        signal_type=SignalType.DIVERGENCE,
        market_id=m.id,
        title="S1",
        summary="ok",
        confidence_score=0.6,
        liquidity_score=0.7,
        score_breakdown_json={"edge": 0.6, "liquidity": 0.7, "freshness": 0.8, "score_total": 0.7},
        created_at=now - timedelta(hours=10),
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    db.add(
        SignalHistory(
            signal_id=signal.id,
            signal_type=SignalType.DIVERGENCE,
            timestamp=now - timedelta(hours=8),
            platform="POLYMARKET",
            market_id=m.id,
            related_market_id=None,
            probability_at_signal=0.45,
            probability_after_6h=0.47,
            divergence=0.08,
            liquidity=0.8,
            volume_24h=2000.0,
            simulated_trade={"capacity_usd": 500.0},
        )
    )
    db.commit()

    report = build_stage5_readiness_gate(
        db,
        days=7,
        min_labeled_returns=1,
        min_actionable_types=1,
        max_insufficient_types=10,
        require_best_platform=False,
        min_clusters=0,
        min_lifetime_types_ok=0,
        min_liquidity_types_ok=0,
    )
    assert report["status"] in {"PASS", "WARN"}
