from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.services.research.event_cluster_research import build_event_cluster_research_report
from app.services.research.liquidity_safety import build_liquidity_safety_report
from app.services.research.platform_comparison import build_platform_comparison_report
from app.services.research.ranking_research import build_ranking_research_report
from app.services.research.signal_lifetime import build_signal_lifetime_report
from app.services.research.signal_type_optimization import build_signal_type_optimization_report
from app.services.research.signal_type_research import build_signal_type_research_report


def build_stage5_final_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_labeled_returns: int = 30,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    generated_at = datetime.now(UTC).isoformat()

    signal_types = build_signal_type_research_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    ranking = build_ranking_research_report(db, days=days, horizon=horizon, min_samples=min_labeled_returns)
    platforms = build_platform_comparison_report(db, days=days, horizon=horizon, min_samples=min_labeled_returns)
    clusters = build_event_cluster_research_report(db, days=days, horizon=horizon, min_cluster_size=2)
    lifetimes = build_signal_lifetime_report(db, days=days, min_samples=min_labeled_returns)
    liquidity = build_liquidity_safety_report(db, days=days, min_samples=min_labeled_returns)

    optimization: dict[str, Any] = {}
    for st in (SignalType.DIVERGENCE.value, SignalType.RULES_RISK.value):
        optimization[st] = build_signal_type_optimization_report(
            db,
            days=days,
            horizon=horizon,
            signal_type=st,
            source_tags=["all", "manifold_bets_api"],
            divergence_thresholds=[0.0, 0.03, 0.05, 0.08, 0.10, 0.15],
            liquidity_thresholds=[0.0, 0.1, 0.25, 0.5],
            volume_thresholds=[0.0, 50.0, 100.0, 250.0, 500.0],
            min_labeled_returns=min_labeled_returns,
            max_candidates=15,
        )

    effective_rows = list(signal_types.get("rows") or [])
    overrides: list[dict[str, Any]] = []
    by_type_idx = {str(r.get("signal_type")): idx for idx, r in enumerate(effective_rows)}
    for st, opt in optimization.items():
        if "error" in opt:
            continue
        best = opt.get("best_candidate") or {}
        opt_decision = str(opt.get("decision") or "")
        idx = by_type_idx.get(st)
        if idx is None:
            continue
        base_row = dict(effective_rows[idx])
        base_decision = str(base_row.get("decision") or "")
        if base_decision in {"REMOVE", "INSUFFICIENT_DATA"} and opt_decision in {"KEEP", "MODIFY"}:
            base_row["decision"] = opt_decision
            base_row["decision_reason"] = (
                f"Overridden by optimization: source_tag={best.get('source_tag')}, "
                f"min_divergence={best.get('min_divergence')}, "
                f"min_liquidity={best.get('min_liquidity')}, "
                f"min_volume_24h={best.get('min_volume_24h')}."
            )
            base_row["optimization_override"] = {
                "base_decision": base_decision,
                "optimized_decision": opt_decision,
                "best_candidate": best,
                "problem_summary": opt.get("problem_summary"),
            }
            effective_rows[idx] = base_row
            overrides.append(
                {
                    "signal_type": st,
                    "base_decision": base_decision,
                    "optimized_decision": opt_decision,
                }
            )

    keep_types = []
    modify_types = []
    remove_types = []
    insufficient_types = []
    for row in effective_rows:
        decision = str(row.get("decision") or "")
        st = str(row.get("signal_type") or "UNKNOWN")
        if decision == "KEEP":
            keep_types.append(st)
        elif decision == "MODIFY":
            modify_types.append(st)
        elif decision == "REMOVE":
            remove_types.append(st)
        else:
            insufficient_types.append(st)

    readiness = "PARTIAL"
    if keep_types and len(insufficient_types) == 0:
        readiness = "READY_FOR_THRESHOLD_UPDATE"
    elif not keep_types and (modify_types or remove_types):
        readiness = "REQUIRES_STRATEGY_REWORK"

    return {
        "generated_at": generated_at,
        "period_days": days,
        "horizon": horizon,
        "readiness": readiness,
        "decision_summary": {
            "keep_types": keep_types,
            "modify_types": modify_types,
            "remove_types": remove_types,
            "insufficient_types": insufficient_types,
        },
        "key_findings": {
            "best_ranking_formula": ranking.get("best_formula"),
            "best_platform": platforms.get("best_platform"),
            "clusters_total": clusters.get("clusters_total", 0),
            "lifetime_types_covered": len(lifetimes.get("rows") or []),
            "liquidity_types_covered": len(liquidity.get("rows") or []),
            "decision_overrides_total": len(overrides),
        },
        "sections": {
            "signal_types": signal_types,
            "signal_types_effective": {"rows": effective_rows, "overrides": overrides},
            "signal_type_optimization": optimization,
            "ranking_formulas": ranking,
            "platform_comparison": platforms,
            "event_clusters": clusters,
            "signal_lifetime": lifetimes,
            "liquidity_safety": liquidity,
        },
    }


def extract_stage5_final_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    decision_summary = report.get("decision_summary") or {}
    key_findings = report.get("key_findings") or {}
    return {
        "final_keep_types": float(len(decision_summary.get("keep_types") or [])),
        "final_modify_types": float(len(decision_summary.get("modify_types") or [])),
        "final_remove_types": float(len(decision_summary.get("remove_types") or [])),
        "final_insufficient_types": float(len(decision_summary.get("insufficient_types") or [])),
        "final_clusters_total": float(key_findings.get("clusters_total") or 0.0),
    }
