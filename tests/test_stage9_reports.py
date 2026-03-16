from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory, Stage7AgentDecision
from app.services.research.stage9_batch import build_stage9_batch_report
from app.services.research.stage9_final_report import build_stage9_final_report
from app.services.research.stage9_reports import (
    build_stage9_consensus_quality_report,
    build_stage9_directional_labeling_report,
    build_stage9_execution_realism_report,
)


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
        spread_cents=2.5,
        open_interest=120000.0,
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
        execution_analysis={"expected_ev_after_costs_pct": 0.03, "assumptions_version": "v2"},
        signal_direction="YES",
        created_at=now - timedelta(hours=3),
    )
    db.add(sig)
    db.flush()

    for i in range(30):
        up = (i % 2) == 0
        db.add(
            SignalHistory(
                signal_id=sig.id,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=i + 1),
                timestamp_bucket=(now - timedelta(hours=i + 1)).replace(minute=0, second=0, microsecond=0),
                platform="POLYMARKET",
                source_tag="local",
                market_id=market.id,
                probability_at_signal=0.55,
                probability_after_6h=0.58 if up else 0.52,
                resolved_success=up,
                resolved_outcome="YES" if up else "NO",
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
                "external_consensus": {
                    "polymarket_prob": 0.61,
                    "manifold_prob": 0.59,
                    "metaculus_median": 0.64,
                    "consensus_weighted_prob": 0.612,
                }
            },
            created_at=now - timedelta(hours=1),
        )
    )
    db.commit()


def test_stage9_reports_include_expected_metrics() -> None:
    db = _mk_db()
    _seed(db)
    consensus = build_stage9_consensus_quality_report(db, days=14)
    labeling = build_stage9_directional_labeling_report(db, days=30)
    execution = build_stage9_execution_realism_report(db, days=14)

    assert consensus["rows_total"] >= 1
    assert consensus["metaculus_median_fill_rate"] > 0
    assert "consensus_reason_codes" in consensus
    assert "consensus_two_source_mode_share" in consensus
    assert labeling["direction_labeled_share"] > 0
    assert execution["non_zero_edge_share"] > 0
    assert "polymarket_spread_coverage_share" in execution
    assert "brier_skill_score_per_category" in execution
    assert "ece_per_category" in execution
    assert "auprc" in execution


def test_stage9_batch_report_contains_tracking_and_reports() -> None:
    db = _mk_db()
    _seed(db)
    report = build_stage9_batch_report(db, settings=Settings())
    assert "reports" in report
    assert "tracking" in report
    assert "stage9_consensus_quality" in report["reports"]
    assert "stage9_directional_labeling" in report["reports"]
    assert "stage9_execution_realism" in report["reports"]
    assert "stage9_final_report" in report["reports"]
    assert "stage9_consensus_quality" in report["tracking"]


def test_stage9_final_report_shape(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("METACULUS_API_TOKEN", raising=False)
    db = _mk_db()
    _seed(db)
    settings = Settings()
    settings.metaculus_api_token = ""
    report = build_stage9_final_report(db, settings=settings)
    assert report["final_decision"] in {"PASS", "WARN", "DATA_PENDING"}
    assert "checks" in report
    assert "summary" in report
    assert report["summary"]["metaculus_check_required"] is False
    assert report["checks"]["metaculus_median_fill_rate_ge_70pct_or_token_missing"] is True
    assert "stage8_zero_edge_share" in report["summary"]
    assert "stage8_zero_edge_share_le_40pct" in report["checks"]


def test_stage9_final_report_data_pending_on_empty_db() -> None:
    db = _mk_db()
    report = build_stage9_final_report(db, settings=Settings())
    assert report["final_decision"] == "DATA_PENDING"
    assert report["summary"]["data_pending"] is True


def test_stage9_final_report_uses_prebuilt_stage8_shadow(monkeypatch) -> None:  # noqa: ANN001
    db = _mk_db()
    calls = {"shadow": 0, "final": 0}

    def _fake_stage8_shadow(*args, **kwargs):  # noqa: ANN001
        calls["shadow"] += 1
        return {"rows": []}

    def _fake_stage8_final(*args, **kwargs):  # noqa: ANN001
        calls["final"] += 1
        assert kwargs.get("shadow_report") is not None
        return {"final_decision": "NO_GO_DATA_PENDING", "summary": {}, "sections": {"stage8_shadow_ledger": {"rows": []}}}

    monkeypatch.setattr("app.services.research.stage9_final_report.build_stage8_shadow_ledger_report", _fake_stage8_shadow)
    monkeypatch.setattr("app.services.research.stage9_final_report.build_stage8_final_report", _fake_stage8_final)

    report = build_stage9_final_report(db, settings=Settings())
    assert report["final_decision"] in {"WARN", "DATA_PENDING", "PASS"}
    assert calls["shadow"] == 1
    assert calls["final"] == 1
