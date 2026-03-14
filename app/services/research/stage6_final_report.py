from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.research.stage6_governance import build_stage6_governance_report
from app.services.research.stage6_risk_guardrails import build_stage6_risk_guardrails_report
from app.services.research.stage6_type35 import build_stage6_type35_report


def _resolve_final_decision(*, governance_decision: str, guardrail_level: str, rollback_triggered: bool) -> str:
    if guardrail_level == "PANIC" or rollback_triggered:
        return "NO_GO"
    if governance_decision == "GO" and guardrail_level in {"OK", "SOFT"}:
        return "GO"
    if governance_decision in {"GO", "LIMITED_GO"} and guardrail_level in {"OK", "SOFT", "HARD"}:
        return "LIMITED_GO"
    return "NO_GO"


def build_stage6_final_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_labeled_returns: int = 30,
) -> dict[str, Any]:
    governance = build_stage6_governance_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    guardrails = build_stage6_risk_guardrails_report(
        db,
        days=min(30, max(7, days)),
        horizon=horizon,
    )
    type35 = build_stage6_type35_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )

    governance_decision = str(governance.get("decision") or "NO_GO")
    guardrail_level = str(guardrails.get("circuit_breaker_level") or "OK")
    rollback_triggered = bool((guardrails.get("rollback") or {}).get("triggered"))
    final_decision = _resolve_final_decision(
        governance_decision=governance_decision,
        guardrail_level=guardrail_level,
        rollback_triggered=rollback_triggered,
    )

    checks = {
        "governance_not_no_go": governance_decision in {"GO", "LIMITED_GO"},
        "guardrail_not_panic": guardrail_level != "PANIC",
        "rollback_not_triggered": not rollback_triggered,
    }
    if final_decision == "GO":
        action = "proceed_rollout"
    elif final_decision == "LIMITED_GO":
        action = "limit_rollout_to_20pct_and_monitor"
    else:
        action = "block_rollout_and_research"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "period_days": days,
        "horizon": horizon,
        "final_decision": final_decision,
        "recommended_action": action,
        "checks": checks,
        "summary": {
            "governance_decision": governance_decision,
            "guardrail_level": guardrail_level,
            "rollback_triggered": rollback_triggered,
            "type35_decision_counts": type35.get("decision_counts") or {},
            "keep_types": (governance.get("summary") or {}).get("keep_types", 0),
            "executable_signals_per_day": (governance.get("summary") or {}).get("executable_signals_per_day", 0.0),
        },
        "sections": {
            "governance": governance,
            "risk_guardrails": guardrails,
            "type35": type35,
        },
    }


def extract_stage6_final_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    decision = str(report.get("final_decision") or "NO_GO")
    score = 0.0
    if decision == "GO":
        score = 1.0
    elif decision == "LIMITED_GO":
        score = 0.5
    summary = report.get("summary") or {}
    return {
        "stage6_final_decision_score": score,
        "stage6_final_keep_types": float(summary.get("keep_types") or 0.0),
        "stage6_final_executable_signals_per_day": float(summary.get("executable_signals_per_day") or 0.0),
        "stage6_final_rollback_triggered": 1.0 if bool(summary.get("rollback_triggered")) else 0.0,
    }
