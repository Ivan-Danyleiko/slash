from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


_WEIGHTS = {
    "integration_fit": 0.15,
    "tooling_control": 0.15,
    "observability": 0.15,
    "governance_fit": 0.20,
    "security": 0.15,
    "reliability_latency": 0.10,
    "cost": 0.05,
    "vendor_risk": 0.05,
}

_STACK_PRIORS: list[dict[str, Any]] = [
    {
        "stack": "langgraph",
        "scores": {
            "integration_fit": 5,
            "tooling_control": 5,
            "observability": 4,
            "governance_fit": 5,
            "security": 4,
            "reliability_latency": 4,
            "cost": 4,
            "vendor_risk": 3,
        },
        "recommendation": "adopt",
        "role": "primary_candidate",
    },
    {
        "stack": "llamaindex_workflows",
        "scores": {
            "integration_fit": 4,
            "tooling_control": 4,
            "observability": 3,
            "governance_fit": 4,
            "security": 4,
            "reliability_latency": 3,
            "cost": 4,
            "vendor_risk": 3,
        },
        "recommendation": "pilot",
        "role": "secondary_candidate",
    },
    {
        "stack": "plain_llm_api",
        "scores": {
            "integration_fit": 5,
            "tooling_control": 4,
            "observability": 4,
            "governance_fit": 5,
            "security": 4,
            "reliability_latency": 4,
            "cost": 3,
            "vendor_risk": 3,
        },
        "recommendation": "pilot",
        "role": "secondary_fallback",
    },
    {
        "stack": "crewai",
        "scores": {
            "integration_fit": 4,
            "tooling_control": 3,
            "observability": 3,
            "governance_fit": 3,
            "security": 3,
            "reliability_latency": 3,
            "cost": 3,
            "vendor_risk": 3,
        },
        "recommendation": "pilot",
        "role": "restricted_pilot",
    },
    {
        "stack": "autogen",
        "scores": {
            "integration_fit": 4,
            "tooling_control": 4,
            "observability": 3,
            "governance_fit": 3,
            "security": 3,
            "reliability_latency": 3,
            "cost": 3,
            "vendor_risk": 2,
        },
        "recommendation": "pilot",
        "role": "vendor_risk_watch",
    },
    {
        "stack": "n8n",
        "scores": {
            "integration_fit": 3,
            "tooling_control": 3,
            "observability": 4,
            "governance_fit": 2,
            "security": 4,
            "reliability_latency": 3,
            "cost": 4,
            "vendor_risk": 3,
        },
        "recommendation": "adopt_for_orchestration_only",
        "role": "ops_orchestration",
    },
]


def _weighted_score(scores: dict[str, int]) -> float:
    total = 0.0
    for key, weight in _WEIGHTS.items():
        total += float(scores.get(key) or 0.0) * weight
    return round(total, 4)


def _empirical_adjustments(
    *,
    stack: str,
    scores: dict[str, int],
    harness_by_stack: dict[str, dict[str, Any]] | None,
) -> dict[str, int]:
    if not harness_by_stack:
        return scores
    item = harness_by_stack.get(stack)
    if not item:
        return scores
    out = dict(scores)
    pass_rate = float(item.get("pass_rate") or 0.0)
    idem_rate = float(item.get("idempotency_pass_rate") or 0.0)
    latency_ok = bool(item.get("latency_within_budget"))
    if pass_rate >= 0.90:
        out["governance_fit"] = min(5, int(out["governance_fit"]) + 1)
    elif pass_rate < 0.80:
        out["governance_fit"] = max(1, int(out["governance_fit"]) - 1)
    if idem_rate < 0.90:
        out["tooling_control"] = max(1, int(out["tooling_control"]) - 1)
    if not latency_ok:
        out["reliability_latency"] = max(1, int(out["reliability_latency"]) - 1)
    return out


def build_stage7_stack_scorecard_report(
    *,
    harness_by_stack: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in _STACK_PRIORS:
        base_scores = dict(item["scores"])
        scores = _empirical_adjustments(
            stack=str(item["stack"]),
            scores=base_scores,
            harness_by_stack=harness_by_stack,
        )
        rows.append(
            {
                "stack": item["stack"],
                "scores": dict(scores),
                "base_scores": base_scores,
                "weighted_score": _weighted_score(scores),
                "recommendation": item["recommendation"],
                "role": item["role"],
                "harness_metrics": (harness_by_stack or {}).get(str(item["stack"])),
            }
        )

    rows.sort(key=lambda x: float(x.get("weighted_score") or 0.0), reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "weights": dict(_WEIGHTS),
        "rows": rows,
        "summary": {
            "total_candidates": len(rows),
            "top_stack": rows[0]["stack"] if rows else None,
            "top_weighted_score": rows[0]["weighted_score"] if rows else 0.0,
        },
    }


def extract_stage7_stack_scorecard_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = report.get("summary") or {}
    rows = list(report.get("rows") or [])
    adopted = sum(1 for r in rows if str(r.get("recommendation")) == "adopt")
    return {
        "stage7_stack_candidates": float(summary.get("total_candidates") or 0.0),
        "stage7_stack_top_score": float(summary.get("top_weighted_score") or 0.0),
        "stage7_stack_adopt_count": float(adopted),
    }
