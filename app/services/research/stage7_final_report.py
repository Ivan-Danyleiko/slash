from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage6_final_report import build_stage6_final_report
from app.services.research.stage7_harness import build_stage7_harness_report
from app.services.research.stage7_shadow import build_stage7_shadow_report
from app.services.research.stage7_stack_scorecard import build_stage7_stack_scorecard_report


def _resolve_stage7_decision(
    *,
    stage6_final_decision: str,
    shadow_days: int,
    delta_keep_rate: float,
    baseline_precision: float,
    post_hoc_precision: float,
    reason_code_stability: float,
    latency_p95_ms: float,
    max_latency_ms: int,
    cost_mode: str,
    sweeps_pass_12_of_18: bool,
    ci_lower_bound_positive_80: bool,
    walkforward_negative_window_share_ok: bool,
    data_sufficient_for_acceptance: bool,
) -> str:
    if cost_mode == "hard_cutoff":
        return "NO_GO"
    if not data_sufficient_for_acceptance:
        return "NO_GO_DATA_PENDING"
    if shadow_days < 14:
        return "NO_GO"
    if delta_keep_rate > 0.15:
        return "NO_GO"
    if post_hoc_precision < baseline_precision:
        return "NO_GO"
    if reason_code_stability < 0.90:
        return "NO_GO"
    if latency_p95_ms > float(max_latency_ms):
        return "NO_GO"
    if not sweeps_pass_12_of_18:
        return "NO_GO"
    if not ci_lower_bound_positive_80:
        return "NO_GO"
    if not walkforward_negative_window_share_ok:
        return "NO_GO"
    if stage6_final_decision in {"GO", "LIMITED_GO"}:
        return "GO"
    return "LIMITED_GO"


def build_stage7_final_report(
    db: Session,
    *,
    settings: Settings,
    lookback_days: int = 14,
    limit: int = 300,
    stage6_days: int = 30,
    stage6_horizon: str = "6h",
    stage6_min_labeled_returns: int = 30,
) -> dict[str, Any]:
    harness = build_stage7_harness_report(max_latency_ms=int(settings.stage7_agent_max_latency_ms), settings=settings)
    scorecard = build_stage7_stack_scorecard_report(harness_by_stack=harness.get("by_stack"))
    shadow = build_stage7_shadow_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
    )
    stage6 = build_stage6_final_report(
        db,
        days=stage6_days,
        horizon=stage6_horizon,
        min_labeled_returns=stage6_min_labeled_returns,
    )
    metrics = shadow.get("metrics") or {}
    stage6_decision = str(stage6.get("final_decision") or "NO_GO")

    final_decision = _resolve_stage7_decision(
        stage6_final_decision=stage6_decision,
        shadow_days=lookback_days,
        delta_keep_rate=float(metrics.get("delta_keep_rate") or 0.0),
        baseline_precision=float(metrics.get("baseline_post_hoc_precision") or 0.0),
        post_hoc_precision=float(metrics.get("post_hoc_precision") or 0.0),
        reason_code_stability=float(metrics.get("reason_code_stability") or 0.0),
        latency_p95_ms=float(metrics.get("latency_p95_ms") or 0.0),
        max_latency_ms=int(settings.stage7_agent_max_latency_ms),
        cost_mode=str((shadow.get("cost_control") or {}).get("mode") or "normal"),
        sweeps_pass_12_of_18=bool((shadow.get("scenario_sweeps") or {}).get("passes_12_of_18")),
        ci_lower_bound_positive_80=bool(metrics.get("bootstrap_ci_lower_bound_positive_80")),
        walkforward_negative_window_share_ok=bool(metrics.get("walkforward_negative_window_share_ok")),
        data_sufficient_for_acceptance=bool(metrics.get("data_sufficient_for_acceptance")),
    )

    if final_decision == "GO":
        action = "enable_stage7_rollout_full_with_guardrails"
    elif final_decision == "LIMITED_GO":
        action = "enable_stage7_shadow_to_20pct_rollout"
    elif final_decision == "NO_GO_DATA_PENDING":
        action = "continue_shadow_collect_labels_no_rollout"
    else:
        action = "keep_stage6_baseline_and_continue_research"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "final_decision": final_decision,
        "recommended_action": action,
        "checks": {
            "stage6_not_no_go": stage6_decision != "NO_GO",
            "shadow_min_14_days": lookback_days >= 14,
            "delta_keep_rate_le_15pct": float(metrics.get("delta_keep_rate") or 0.0) <= 0.15,
            "post_hoc_precision_ge_baseline": float(metrics.get("post_hoc_precision") or 0.0)
            >= float(metrics.get("baseline_post_hoc_precision") or 0.0),
            "reason_code_stability_ge_90pct": float(metrics.get("reason_code_stability") or 0.0) >= 0.90,
            "latency_p95_within_budget": float(metrics.get("latency_p95_ms") or 0.0)
            <= float(settings.stage7_agent_max_latency_ms),
            "sweeps_positive_in_12_of_18": bool((shadow.get("scenario_sweeps") or {}).get("passes_12_of_18")),
            "bootstrap_ci_lower_bound_positive_80": bool(metrics.get("bootstrap_ci_lower_bound_positive_80")),
            "walkforward_negative_window_share_le_30pct": bool(metrics.get("walkforward_negative_window_share_ok")),
            "data_sufficient_for_acceptance": bool(metrics.get("data_sufficient_for_acceptance")),
            "cost_mode_not_hard_cutoff": str((shadow.get("cost_control") or {}).get("mode") or "normal")
            != "hard_cutoff",
        },
        "summary": {
            "stage6_final_decision": stage6_decision,
            "agent_decision_coverage": float(shadow.get("agent_decision_coverage") or 0.0),
            "delta_keep_rate": float(metrics.get("delta_keep_rate") or 0.0),
            "baseline_post_hoc_precision": float(metrics.get("baseline_post_hoc_precision") or 0.0),
            "post_hoc_precision": float(metrics.get("post_hoc_precision") or 0.0),
            "reason_code_stability": float(metrics.get("reason_code_stability") or 0.0),
            "latency_p95_ms": float(metrics.get("latency_p95_ms") or 0.0),
            "brier_score": float(metrics.get("brier_score") or 0.0),
            "deflated_sharpe_proxy": float(metrics.get("deflated_sharpe_proxy") or 0.0),
            "bootstrap_ci_low_80": float(metrics.get("bootstrap_ci_low_80") or 0.0),
            "bootstrap_ci_high_80": float(metrics.get("bootstrap_ci_high_80") or 0.0),
            "bootstrap_ci_lower_bound_positive_80": bool(metrics.get("bootstrap_ci_lower_bound_positive_80")),
            "walkforward_negative_window_share": float(metrics.get("walkforward_negative_window_share") or 0.0),
            "data_sufficient_for_acceptance": bool(metrics.get("data_sufficient_for_acceptance")),
            "data_sufficiency": dict(shadow.get("data_sufficiency") or {}),
            "cost_mode": str((shadow.get("cost_control") or {}).get("mode") or "normal"),
            "monthly_spend_usd": float((shadow.get("cost_control") or {}).get("monthly_spend_usd") or 0.0),
            "monthly_budget_used_ratio": float(
                (shadow.get("cost_control") or {}).get("monthly_budget_used_ratio") or 0.0
            ),
            "top_stack": (scorecard.get("summary") or {}).get("top_stack"),
        },
        "sections": {
            "harness": harness,
            "stack_scorecard": scorecard,
            "shadow": shadow,
            "stage6_final_report": stage6,
        },
    }


def extract_stage7_final_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    decision = str(report.get("final_decision") or "NO_GO")
    score = 0.0
    if decision == "GO":
        score = 1.0
    elif decision == "LIMITED_GO":
        score = 0.5
    elif decision == "NO_GO_DATA_PENDING":
        score = 0.25
    summary = report.get("summary") or {}
    return {
        "stage7_final_decision_score": score,
        "stage7_agent_decision_coverage": float(summary.get("agent_decision_coverage") or 0.0),
        "stage7_delta_keep_rate": float(summary.get("delta_keep_rate") or 0.0),
        "stage7_post_hoc_precision": float(summary.get("post_hoc_precision") or 0.0),
        "stage7_reason_code_stability": float(summary.get("reason_code_stability") or 0.0),
    }
