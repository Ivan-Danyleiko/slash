from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import (
    Market,
    MarketSnapshot,
    Platform,
    Signal,
    SignalHistory,
    Stage10ReplayRow,
    Stage7AgentDecision,
    Stage8Decision,
)
from app.services.research.stage10_batch import build_stage10_batch_report
from app.services.research.stage10_final_report import build_stage10_final_report
from app.services.research.stage10_module_audit import build_stage10_module_audit_report
from app.services.research.stage10_replay import build_stage10_replay_report


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def _seed(db: Session) -> None:
    now = datetime.now(UTC)
    poly = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(poly)
    db.flush()
    market = Market(
        platform_id=poly.id,
        external_market_id="poly-1",
        title="Will BTC be above 100k?",
        category="crypto",
        probability_yes=0.62,
        volume_24h=50000.0,
        liquidity_value=40000.0,
        created_at=now - timedelta(days=2),
        fetched_at=now,
        resolution_time=now + timedelta(days=5),
    )
    db.add(market)
    db.flush()
    sig = Signal(
        signal_type=SignalType.DIVERGENCE,
        market_id=market.id,
        title=market.title,
        summary="test",
        confidence_score=0.7,
        liquidity_score=0.8,
        divergence_score=0.1,
        execution_analysis={"expected_ev_after_costs_pct": 0.03, "expected_costs_pct": 0.01, "assumptions_version": "v2"},
        signal_direction="YES",
        created_at=now - timedelta(hours=3),
    )
    db.add(sig)
    db.flush()

    db.add(
        SignalHistory(
            signal_id=sig.id,
            signal_type=SignalType.DIVERGENCE,
            timestamp=now - timedelta(hours=2),
            timestamp_bucket=(now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0),
            platform="POLYMARKET",
            source_tag="local",
            market_id=market.id,
            probability_at_signal=0.55,
            probability_after_6h=0.58,
            resolved_success=True,
            resolved_outcome="YES",
            signal_direction="YES",
        )
    )
    db.add(
        Stage7AgentDecision(
            signal_id=sig.id,
            input_hash="h1",
            base_decision="KEEP",
            decision="KEEP",
            evidence_bundle={
                "trace_id": "t1",
                "external_consensus": {
                    "polymarket_prob": 0.61,
                    "manifold_prob": 0.59,
                },
            },
            model_version="v1",
            created_at=now - timedelta(hours=1),
        )
    )
    db.add(
        Stage8Decision(
            signal_id=sig.id,
            category="crypto",
            category_confidence=0.9,
            policy_version="stage8_bootstrap_v1",
            rules_ambiguity_score=0.1,
            resolution_source_confidence=0.8,
            dispute_risk_flag=False,
            edge_after_costs=0.03,
            base_decision="KEEP",
            decision="KEEP",
            execution_action="EXECUTE_ALLOWED",
            reason_codes=["ok"],
        )
    )
    db.commit()


def test_stage10_replay_persists_rows() -> None:
    db = _mk_db()
    _seed(db)
    settings = Settings()
    settings.stage10_replay_embargo_seconds = 0
    report = build_stage10_replay_report(db, settings=settings, days=30, limit=1000, event_target=1, persist_rows=True)
    assert report["summary"]["rows_total"] >= 1
    assert report["summary"]["events_total"] >= 1
    count = int(db.scalar(select(func.count()).select_from(Stage10ReplayRow)) or 0)
    assert count >= 1



def test_stage10_module_audit_and_final_report() -> None:
    db = _mk_db()
    _seed(db)
    settings = Settings()
    settings.stage10_replay_embargo_seconds = 0
    audit = build_stage10_module_audit_report(db, settings=settings)
    assert "summary" in audit
    assert "rows" in audit
    final = build_stage10_final_report(db, settings=settings, days=30, limit=1000, event_target=1)
    assert final["final_decision"] in {"PASS", "WARN", "DATA_PENDING"}
    assert "checks" in final
    assert "post_cost_ev_ci_low_80" in final["summary"]
    assert "core_category_positive_ev_candidates" in final["summary"]
    assert "reason_code_stability" in final["summary"]
    assert "walkforward_negative_window_share" in final["summary"]
    assert "brier_score" in final["summary"]
    assert "ece" in final["summary"]



def test_stage10_batch_contains_sections() -> None:
    db = _mk_db()
    _seed(db)
    settings = Settings()
    settings.stage10_replay_embargo_seconds = 0
    report = build_stage10_batch_report(db, settings=settings, days=30, limit=1000, event_target=1)
    assert "reports" in report
    assert "tracking" in report
    assert "stage10_replay" in report["reports"]
    assert "stage10_timeline_quality" in report["reports"]
    assert "stage10_timeline_backfill_plan" in report["reports"]
    assert "stage10_module_audit" in report["reports"]
    assert "stage10_final_report" in report["reports"]


def test_stage10_timeline_sufficiency_snapshot_vs_fallback() -> None:
    db = _mk_db()
    _seed(db)
    settings = Settings()
    settings.stage10_replay_embargo_seconds = 0

    # No snapshot yet -> fallback path should mark timeline insufficient.
    replay_no_snap = build_stage10_replay_report(db, settings=settings, days=30, limit=1000, event_target=1, persist_rows=False)
    assert float(replay_no_snap["summary"]["data_insufficient_timeline_share"]) >= 1.0

    # Add snapshot before replay timestamp -> timeline should become sufficient.
    hist = db.scalar(select(SignalHistory).order_by(SignalHistory.id.desc()).limit(1))
    assert hist is not None
    db.add(
        MarketSnapshot(
            market_id=int(hist.market_id),
            probability_yes=0.57,
            probability_no=0.43,
            volume_24h=1234.0,
            liquidity_value=4321.0,
            fetched_at=(hist.timestamp - timedelta(minutes=5)),
        )
    )
    db.commit()
    replay_with_snap = build_stage10_replay_report(db, settings=settings, days=30, limit=1000, event_target=1, persist_rows=False)
    assert float(replay_with_snap["summary"]["data_insufficient_timeline_share"]) <= 0.0
    assert int((replay_with_snap["summary"]["timeline_source_counts"] or {}).get("snapshot", 0)) >= 1
