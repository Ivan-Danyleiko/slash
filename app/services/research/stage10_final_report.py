from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage10_module_audit import build_stage10_module_audit_report
from app.services.research.stage10_replay import build_stage10_replay_report
from app.services.research.stage10_timeline_quality import build_stage10_timeline_quality_report
from app.services.research.walkforward import build_walkforward_report


def build_stage10_final_report(
    db: Session,
    *,
    settings: Settings,
    days: int = 365,
    limit: int = 5000,
    event_target: int = 100,
    replay_report: dict[str, Any] | None = None,
    module_audit_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    replay = replay_report or build_stage10_replay_report(
        db,
        settings=settings,
        days=days,
        limit=limit,
        event_target=event_target,
        persist_rows=True,
    )
    timeline = build_stage10_timeline_quality_report(db, days=days)
    audit = module_audit_report or build_stage10_module_audit_report(db, settings=settings)

    replay_summary = dict(replay.get("summary") or {})
    audit_summary = dict(audit.get("summary") or {})
    sweeps = dict(replay.get("scenario_sweeps") or {})
    try:
        walkforward = build_walkforward_report(
            db,
            days=min(180, max(30, int(days))),
            horizon="6h",
            train_days=30,
            test_days=14,
            step_days=14,
            embargo_hours=24,
            min_samples_per_window=10,
            bootstrap_sims=500,
        )
    except OperationalError:
        walkforward = {"rows": [], "summary": {"compat_fallback": True}}
    windows = []
    for row in list(walkforward.get("rows") or []):
        windows.extend(list(row.get("windows") or []))
    negative_windows = 0
    evaluated_windows = 0
    for w in windows:
        test = dict(w.get("test") or {})
        if int(test.get("n") or 0) <= 0:
            continue
        evaluated_windows += 1
        if float(test.get("avg_return") or 0.0) < 0.0:
            negative_windows += 1
    walkforward_negative_window_share = (negative_windows / evaluated_windows) if evaluated_windows else 1.0

    checks = {
        "events_total_ge_target": bool(replay_summary.get("event_target_reached")),
        "leakage_violations_count_eq_0": int(replay_summary.get("leakage_violations_count") or 0) == 0,
        "data_insufficient_timeline_share_le_20pct": float(replay_summary.get("data_insufficient_timeline_share") or 1.0)
        <= 0.20,
        "post_cost_ev_ci_low_80_gt_0": float(replay_summary.get("post_cost_ev_ci_low_80") or 0.0) > 0.0,
        "core_category_positive_ev_candidate_ge_1": int(replay_summary.get("core_category_positive_ev_candidates") or 0) >= 1,
        "scenario_sweeps_positive_ge_12": int(sweeps.get("positive_scenarios") or 0) >= 12,
        "reason_code_stability_ge_90pct": float(replay_summary.get("reason_code_stability") or 0.0) >= 0.90,
        "walkforward_negative_window_share_le_30pct": walkforward_negative_window_share <= 0.30,
        "core_categories_each_ge_20": bool(replay_summary.get("core_categories_each_ge_20")),
        "module_security_pass_count_ge_1": int(audit_summary.get("security_pass_count") or 0) >= 1,
        "llm_mode_not_hard_cutoff": str(audit_summary.get("stage10_llm_mode") or "normal") != "hard_cutoff",
    }

    failed_checks = [k for k, v in checks.items() if not bool(v)]

    rows_total = int(replay_summary.get("rows_total") or 0)
    data_pending = rows_total < 100

    if data_pending:
        final_decision = "DATA_PENDING"
        action = "collect_more_replay_rows"
    elif all(checks.values()):
        final_decision = "PASS"
        action = "stage10_complete_ready_for_stage11"
    else:
        final_decision = "WARN"
        action = "fix_stage10_failed_checks"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "final_decision": final_decision,
        "recommended_action": action,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": {
            "data_pending": data_pending,
            "rows_total": rows_total,
            "events_total": int(replay_summary.get("events_total") or 0),
            "event_target": int(replay_summary.get("event_target") or event_target),
            "leakage_violations_count": int(replay_summary.get("leakage_violations_count") or 0),
            "data_insufficient_timeline_share": float(replay_summary.get("data_insufficient_timeline_share") or 1.0),
            "post_cost_ev_ci_low_80": float(replay_summary.get("post_cost_ev_ci_low_80") or 0.0),
            "core_category_positive_ev_candidates": int(replay_summary.get("core_category_positive_ev_candidates") or 0),
            "core_category_ev_ci_low_80": dict(replay_summary.get("core_category_ev_ci_low_80") or {}),
            "scenario_sweeps_positive": int(sweeps.get("positive_scenarios") or 0),
            "reason_code_stability": float(replay_summary.get("reason_code_stability") or 0.0),
            "brier_score": float(replay_summary.get("brier_score") or 0.0),
            "brier_skill_score": float(replay_summary.get("brier_skill_score") or 0.0),
            "ece": float(replay_summary.get("ece") or 0.0),
            "longshot_bias_error_0_15pct": float(replay_summary.get("longshot_bias_error_0_15pct") or 0.0),
            "walkforward_windows_evaluated": evaluated_windows,
            "walkforward_negative_window_share": walkforward_negative_window_share,
            "module_security_pass_count": int(audit_summary.get("security_pass_count") or 0),
            "module_security_fail_count": int(audit_summary.get("security_fail_count") or 0),
            "stage10_llm_mode": str(audit_summary.get("stage10_llm_mode") or "normal"),
        },
        "sections": {
            "stage10_replay": replay,
            "stage10_timeline_quality": timeline,
            "stage10_module_audit": audit,
            "walkforward": walkforward,
        },
    }


def extract_stage10_final_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    decision = str(report.get("final_decision") or "WARN")
    score = 0.0
    if decision == "PASS":
        score = 1.0
    elif decision == "WARN":
        score = 0.5
    summary = dict(report.get("summary") or {})
    return {
        "stage10_final_decision_score": score,
        "stage10_data_pending": 1.0 if bool(summary.get("data_pending")) else 0.0,
        "stage10_events_total": float(summary.get("events_total") or 0.0),
        "stage10_leakage_violations_count": float(summary.get("leakage_violations_count") or 0.0),
        "stage10_data_insufficient_timeline_share": float(summary.get("data_insufficient_timeline_share") or 1.0),
        "stage10_post_cost_ev_ci_low_80": float(summary.get("post_cost_ev_ci_low_80") or 0.0),
        "stage10_core_category_positive_ev_candidates": float(summary.get("core_category_positive_ev_candidates") or 0.0),
        "stage10_scenario_sweeps_positive": float(summary.get("scenario_sweeps_positive") or 0.0),
        "stage10_reason_code_stability": float(summary.get("reason_code_stability") or 0.0),
        "stage10_brier_score": float(summary.get("brier_score") or 0.0),
        "stage10_brier_skill_score": float(summary.get("brier_skill_score") or 0.0),
        "stage10_ece": float(summary.get("ece") or 0.0),
        "stage10_longshot_bias_error_0_15pct": float(summary.get("longshot_bias_error_0_15pct") or 0.0),
        "stage10_walkforward_negative_window_share": float(summary.get("walkforward_negative_window_share") or 1.0),
        "stage10_module_security_pass_count": float(summary.get("module_security_pass_count") or 0.0),
        "stage10_module_security_fail_count": float(summary.get("module_security_fail_count") or 0.0),
    }
