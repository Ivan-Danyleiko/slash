from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import JobRun
from app.services.research.stage8_final_report import build_stage8_final_report
from app.services.research.stage8_shadow_ledger import build_stage8_shadow_ledger_report
from app.services.research.stage9_reports import (
    build_stage9_consensus_quality_report,
    build_stage9_directional_labeling_report,
    build_stage9_execution_realism_report,
)


def build_stage9_final_report(
    db: Session,
    *,
    settings: Settings,
    days_consensus: int = 14,
    days_labeling: int = 30,
    days_execution: int = 14,
) -> dict[str, Any]:
    consensus = build_stage9_consensus_quality_report(db, days=days_consensus)
    labeling = build_stage9_directional_labeling_report(db, days=days_labeling)
    execution = build_stage9_execution_realism_report(db, days=days_execution)
    stage8_shadow = build_stage8_shadow_ledger_report(db, settings=settings, lookback_days=14, limit=300)
    stage8 = build_stage8_final_report(
        db,
        settings=settings,
        lookback_days=14,
        limit=300,
        shadow_report=stage8_shadow,
    )
    stage8_rows = list((stage8_shadow or {}).get("rows") or [])
    if stage8_rows:
        zero_edge_count = sum(1 for row in stage8_rows if abs(float(row.get("edge_after_costs") or 0.0)) <= 1e-9)
        stage8_zero_edge_share = zero_edge_count / len(stage8_rows)
    else:
        stage8_zero_edge_share = 1.0

    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    analyze_total = int(
        db.scalar(
            select(func.count()).select_from(JobRun).where(JobRun.job_name == "analyze_markets", JobRun.started_at >= day_ago)
        )
        or 0
    )
    analyze_failed = int(
        db.scalar(
            select(func.count()).select_from(JobRun).where(
                JobRun.job_name == "analyze_markets",
                JobRun.started_at >= day_ago,
                JobRun.status != "SUCCESS",
            )
        )
        or 0
    )
    analyze_ok_24h = analyze_total > 0 and analyze_failed == 0

    rows_total = int(consensus.get("rows_total") or 0)
    signals_total = int(execution.get("signals_total") or 0)
    data_pending = rows_total < 10 or signals_total < 10

    metaculus_fill = float(consensus.get("metaculus_median_fill_rate") or 0.0)
    consensus_3source = float(consensus.get("consensus_3source_share") or 0.0)
    consensus_2source = float(consensus.get("consensus_2source_share") or 0.0)
    direction_share = float(labeling.get("direction_labeled_share") or 0.0)
    non_zero_edge = float(execution.get("non_zero_edge_share") or 0.0)
    spread_coverage = float(execution.get("spread_coverage_share") or 0.0)
    polymarket_spread_coverage = float(execution.get("polymarket_spread_coverage_share") or 0.0)
    precision25 = float(execution.get("precision_at_25") or 0.0)
    stage8_baseline_precision25 = float((stage8.get("summary") or {}).get("precision_at_keep") or 0.0)
    metaculus_required = bool(str(settings.metaculus_api_token or "").strip())
    metaculus_check = (metaculus_fill >= 0.70) if metaculus_required else True

    consensus_gate = consensus_3source >= 0.50 or consensus_2source >= 0.50
    two_source_mode = consensus_3source < 0.50 and consensus_2source >= 0.50

    checks = {
        "analyze_markets_success_24h": analyze_ok_24h,
        "metaculus_median_fill_rate_ge_70pct_or_token_missing": metaculus_check,
        "direction_labeled_share_ge_95pct": direction_share >= 0.95,
        "non_zero_edge_share_ge_60pct": non_zero_edge >= 0.60,
        "clob_spread_coverage_ge_60pct_if_enabled": (not bool(settings.polymarket_clob_enabled))
        or polymarket_spread_coverage >= 0.60,
        "consensus_3source_ge_50pct_or_two_source_mode": consensus_gate,
        "precision_at_25_ge_stage8_baseline": precision25 >= stage8_baseline_precision25,
        "stage8_zero_edge_share_le_40pct": stage8_zero_edge_share <= 0.40,
        "stage8_not_no_go_data_pending": str(stage8.get("final_decision") or "") != "NO_GO_DATA_PENDING",
    }

    hard_keys = (
        "analyze_markets_success_24h",
        "direction_labeled_share_ge_95pct",
        "non_zero_edge_share_ge_60pct",
        "consensus_3source_ge_50pct_or_two_source_mode",
    )
    hard_ok = all(bool(checks.get(k)) for k in hard_keys)
    all_ok = all(bool(v) for v in checks.values())

    if data_pending:
        final_decision = "DATA_PENDING"
        action = "collect_more_live_data_and_continue_shadow"
    elif all_ok:
        final_decision = "PASS"
        action = "stage9_complete_ready_for_stage10"
    elif hard_ok:
        final_decision = "WARN"
        action = "continue_stage9_tuning_and_monitoring"
    else:
        final_decision = "WARN"
        action = "fix_stage9_blockers_before_stage10"

    failed_checks = [k for k, v in checks.items() if not bool(v)]

    return {
        "generated_at": now.isoformat(),
        "final_decision": final_decision,
        "recommended_action": action,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": {
            "data_pending": data_pending,
            "rows_total": rows_total,
            "signals_total": signals_total,
            "analyze_markets_runs_24h": analyze_total,
            "analyze_markets_failed_24h": analyze_failed,
            "metaculus_median_fill_rate": metaculus_fill,
            "metaculus_check_required": metaculus_required,
            "consensus_2source_share": consensus_2source,
            "consensus_3source_share": consensus_3source,
            "two_source_mode": two_source_mode,
            "direction_labeled_share": direction_share,
            "non_zero_edge_share": non_zero_edge,
            "spread_coverage_share": spread_coverage,
            "polymarket_spread_coverage_share": polymarket_spread_coverage,
            "precision_at_25": precision25,
            "stage8_baseline_precision_at_25": stage8_baseline_precision25,
            "stage8_zero_edge_share": stage8_zero_edge_share,
            "stage8_final_decision": str(stage8.get("final_decision") or ""),
        },
        "sections": {
            "stage9_consensus_quality": consensus,
            "stage9_directional_labeling": labeling,
            "stage9_execution_realism": execution,
            "stage8_final_report": stage8,
        },
    }


def extract_stage9_final_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    decision = str(report.get("final_decision") or "WARN")
    score = 0.0
    if decision == "PASS":
        score = 1.0
    elif decision == "WARN":
        score = 0.5
    summary = dict(report.get("summary") or {})
    return {
        "stage9_final_decision_score": score,
        "stage9_data_pending": 1.0 if bool(summary.get("data_pending")) else 0.0,
        "stage9_metaculus_fill_rate": float(summary.get("metaculus_median_fill_rate") or 0.0),
        "stage9_direction_labeled_share": float(summary.get("direction_labeled_share") or 0.0),
        "stage9_non_zero_edge_share": float(summary.get("non_zero_edge_share") or 0.0),
        "stage9_stage8_zero_edge_share": float(summary.get("stage8_zero_edge_share") or 1.0),
        "stage9_precision_at_25": float(summary.get("precision_at_25") or 0.0),
    }
