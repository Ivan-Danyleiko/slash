from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage7_final_report import build_stage7_final_report
from app.services.research.stage8_shadow_ledger import build_stage8_shadow_ledger_report


def _resolve_stage8_decision(
    *,
    data_sufficient_for_acceptance: bool,
    coverage: float,
    execute_allowed_rate: float,
    core_category_limited_go: bool,
    sweeps_pass_12_of_18: bool,
    sweeps_reliable: bool,
    ci_lower_bound_positive_80: bool,
    walkforward_negative_window_share_ok: bool,
    precision_at_keep: float,
    baseline_precision: float,
    stage7_final_decision: str,
) -> str:
    if not data_sufficient_for_acceptance:
        return "NO_GO_DATA_PENDING"
    if coverage < 0.90:
        return "NO_GO"
    if execute_allowed_rate <= 0.0:
        return "NO_GO"
    if not core_category_limited_go:
        return "NO_GO"
    if not sweeps_pass_12_of_18:
        return "NO_GO"
    if not sweeps_reliable:
        return "NO_GO"
    if not ci_lower_bound_positive_80:
        return "NO_GO"
    if not walkforward_negative_window_share_ok:
        return "NO_GO"
    if precision_at_keep < baseline_precision:
        return "NO_GO"
    if stage7_final_decision == "GO":
        return "GO"
    return "LIMITED_GO"


def build_stage8_final_report(
    db: Session,
    *,
    settings: Settings,
    lookback_days: int = 14,
    limit: int = 300,
    stage7_stage6_days: int = 30,
    stage7_stage6_horizon: str = "6h",
    stage7_stage6_min_labeled_returns: int = 30,
    shadow_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shadow = shadow_report or build_stage8_shadow_ledger_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
    )
    stage7 = build_stage7_final_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
        stage6_days=stage7_stage6_days,
        stage6_horizon=stage7_stage6_horizon,
        stage6_min_labeled_returns=stage7_stage6_min_labeled_returns,
    )
    action_counts = dict(shadow.get("execution_action_counts") or {})
    total = max(1, int(shadow.get("rows_total") or 1))
    execute_allowed_rate = float(action_counts.get("EXECUTE_ALLOWED") or 0.0) / total
    metrics = dict(shadow.get("metrics") or {})
    per_category = dict(shadow.get("per_category") or {})
    core_categories = ("crypto", "finance", "sports")
    core_category_limited_go = any(
        float((per_category.get(cat) or {}).get("execute_allowed_count") or 0.0) > 0
        and float((per_category.get(cat) or {}).get("edge_after_costs_mean") or 0.0) > 0.0
        for cat in core_categories
    )
    final_decision = _resolve_stage8_decision(
        data_sufficient_for_acceptance=bool(shadow.get("data_sufficient_for_acceptance")),
        coverage=float(shadow.get("coverage") or 0.0),
        execute_allowed_rate=execute_allowed_rate,
        core_category_limited_go=core_category_limited_go,
        sweeps_pass_12_of_18=bool(metrics.get("scenario_sweeps_pass_12_of_18")),
        sweeps_reliable=bool(metrics.get("scenario_sweeps_reliable")),
        ci_lower_bound_positive_80=bool(metrics.get("bootstrap_ci_lower_bound_positive_80")),
        walkforward_negative_window_share_ok=bool(metrics.get("walkforward_negative_window_share_ok")),
        precision_at_keep=float(metrics.get("precision_at_keep") or 0.0),
        baseline_precision=float((stage7.get("summary") or {}).get("baseline_post_hoc_precision") or 0.0),
        stage7_final_decision=str(stage7.get("final_decision") or "NO_GO"),
    )
    if final_decision == "GO":
        action = "enable_stage8_rollout_full"
    elif final_decision == "LIMITED_GO":
        action = "enable_stage8_limited_rollout"
    elif final_decision == "NO_GO_DATA_PENDING":
        action = "continue_stage8_shadow_collect_live_data"
    else:
        action = "keep_stage7_baseline_and_tune_stage8"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "final_decision": final_decision,
        "recommended_action": action,
        "checks": {
            "data_sufficient_for_acceptance": bool(shadow.get("data_sufficient_for_acceptance")),
            "coverage_ge_90pct": float(shadow.get("coverage") or 0.0) >= 0.90,
            "execute_allowed_rate_positive": execute_allowed_rate > 0.0,
            "core_category_limited_go": core_category_limited_go,
            "sweeps_positive_in_12_of_18": bool(metrics.get("scenario_sweeps_pass_12_of_18")),
            "sweeps_reliable_realized_sample_share_ge_20pct": bool(metrics.get("scenario_sweeps_reliable")),
            "bootstrap_ci_lower_bound_positive_80": bool(metrics.get("bootstrap_ci_lower_bound_positive_80")),
            "walkforward_negative_window_share_le_30pct": bool(metrics.get("walkforward_negative_window_share_ok")),
            "precision_at_keep_ge_baseline": float(metrics.get("precision_at_keep") or 0.0)
            >= float((stage7.get("summary") or {}).get("baseline_post_hoc_precision") or 0.0),
        },
        "summary": {
            "coverage": float(shadow.get("coverage") or 0.0),
            "rows_total": int(shadow.get("rows_total") or 0),
            "signals_total": int(shadow.get("signals_total") or 0),
            "execute_allowed_rate": round(execute_allowed_rate, 6),
            "core_category_limited_go": core_category_limited_go,
            "stage7_final_decision": str(stage7.get("final_decision") or "NO_GO"),
            "baseline_precision": float((stage7.get("summary") or {}).get("baseline_post_hoc_precision") or 0.0),
            "precision_at_keep": float(metrics.get("precision_at_keep") or 0.0),
            "false_keep_rate": float(metrics.get("false_keep_rate") or 0.0),
            "bootstrap_ci_low_80": float(metrics.get("bootstrap_ci_low_80") or 0.0),
            "bootstrap_ci_high_80": float(metrics.get("bootstrap_ci_high_80") or 0.0),
            "bootstrap_ci_lower_bound_positive_80": bool(metrics.get("bootstrap_ci_lower_bound_positive_80")),
            "walkforward_negative_window_share": float(metrics.get("walkforward_negative_window_share") or 1.0),
            "scenario_sweeps_positive": int(metrics.get("scenario_sweeps_positive") or 0),
            "scenario_sweeps_realized_sample_share": float(metrics.get("scenario_sweeps_realized_sample_share") or 0.0),
            "scenario_sweeps_reliable": bool(metrics.get("scenario_sweeps_reliable")),
            "policy_profile": str(shadow.get("profile") or ""),
        },
        "sections": {
            "stage8_shadow_ledger": shadow,
            "stage7_final_report": stage7,
        },
    }


def extract_stage8_final_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    decision = str(report.get("final_decision") or "NO_GO")
    decision_score = 0.0
    if decision == "GO":
        decision_score = 1.0
    elif decision == "LIMITED_GO":
        decision_score = 0.5
    elif decision == "NO_GO_DATA_PENDING":
        decision_score = 0.25
    summary = dict(report.get("summary") or {})
    return {
        "stage8_final_decision_score": decision_score,
        "stage8_coverage": float(summary.get("coverage") or 0.0),
        "stage8_execute_allowed_rate": float(summary.get("execute_allowed_rate") or 0.0),
        "stage8_core_category_limited_go": 1.0 if summary.get("core_category_limited_go") else 0.0,
    }
