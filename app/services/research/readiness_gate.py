from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.services.research.final_report import build_stage5_final_report


def _check(name: str, passed: bool, actual: Any, expected: Any, critical: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "expected": expected,
        "critical": critical,
    }


def build_stage5_readiness_gate(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_labeled_returns: int = 30,
    min_actionable_types: int = 1,
    max_insufficient_types: int = 3,
    require_best_platform: bool = True,
    min_clusters: int = 1,
    min_lifetime_types_ok: int = 1,
    min_liquidity_types_ok: int = 1,
) -> dict[str, Any]:
    report = build_stage5_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    decision_summary = report.get("decision_summary") or {}
    key_findings = report.get("key_findings") or {}
    keep_types = list(decision_summary.get("keep_types") or [])
    modify_types = list(decision_summary.get("modify_types") or [])
    insufficient_types = list(decision_summary.get("insufficient_types") or [])
    excluded_for_insufficient_gate = {SignalType.WEIRD_MARKET.value, SignalType.WATCHLIST.value}
    insufficient_types_core = [t for t in insufficient_types if t not in excluded_for_insufficient_gate]

    lifetime_rows = list(((report.get("sections") or {}).get("signal_lifetime") or {}).get("rows") or [])
    lifetime_types_ok = sum(1 for row in lifetime_rows if row.get("status") == "OK")
    liquidity_rows = list(((report.get("sections") or {}).get("liquidity_safety") or {}).get("rows") or [])
    liquidity_types_ok = sum(1 for row in liquidity_rows if row.get("status") == "OK")

    actionable_types = len(keep_types) + len(modify_types)
    checks = [
        _check(
            "has_actionable_signal_types",
            actionable_types >= min_actionable_types,
            actionable_types,
            f">= {min_actionable_types}",
            critical=True,
        ),
        _check(
            "insufficient_types_within_limit",
            len(insufficient_types_core) <= max_insufficient_types,
            len(insufficient_types_core),
            f"<= {max_insufficient_types}",
        ),
        _check(
            "has_best_ranking_formula",
            bool(key_findings.get("best_ranking_formula")),
            key_findings.get("best_ranking_formula"),
            "non-empty",
            critical=True,
        ),
        _check(
            "has_best_platform" if require_best_platform else "best_platform_optional",
            (bool(key_findings.get("best_platform")) if require_best_platform else True),
            key_findings.get("best_platform"),
            ("non-empty" if require_best_platform else "optional"),
        ),
        _check(
            "clusters_coverage",
            int(key_findings.get("clusters_total") or 0) >= min_clusters,
            int(key_findings.get("clusters_total") or 0),
            f">= {min_clusters}",
        ),
        _check(
            "lifetime_coverage",
            lifetime_types_ok >= min_lifetime_types_ok,
            lifetime_types_ok,
            f">= {min_lifetime_types_ok}",
        ),
        _check(
            "liquidity_coverage",
            liquidity_types_ok >= min_liquidity_types_ok,
            liquidity_types_ok,
            f">= {min_liquidity_types_ok}",
        ),
    ]
    failed_critical = [c for c in checks if c["critical"] and not c["passed"]]
    failed_non_critical = [c for c in checks if (not c["critical"]) and not c["passed"]]
    if failed_critical:
        status = "FAIL"
    elif failed_non_critical:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "status": status,
        "period_days": days,
        "horizon": horizon,
        "min_labeled_returns": min_labeled_returns,
        "summary": {
            "actionable_types": actionable_types,
            "keep_types": len(keep_types),
            "modify_types": len(modify_types),
            "remove_types": len(list(decision_summary.get("remove_types") or [])),
            "insufficient_types": len(insufficient_types),
            "insufficient_types_core": len(insufficient_types_core),
            "insufficient_types_excluded": len(insufficient_types) - len(insufficient_types_core),
            "best_ranking_formula": key_findings.get("best_ranking_formula"),
            "best_platform": key_findings.get("best_platform"),
            "clusters_total": int(key_findings.get("clusters_total") or 0),
            "lifetime_types_ok": lifetime_types_ok,
            "liquidity_types_ok": liquidity_types_ok,
        },
        "checks": checks,
        "failed_critical_checks": [c["name"] for c in failed_critical],
        "failed_non_critical_checks": [c["name"] for c in failed_non_critical],
    }


def extract_stage5_readiness_gate_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = report.get("summary") or {}
    status = str(report.get("status") or "")
    status_score = 0.0
    if status == "PASS":
        status_score = 1.0
    elif status == "WARN":
        status_score = 0.5
    return {
        "readiness_status_score": status_score,
        "readiness_actionable_types": float(summary.get("actionable_types") or 0.0),
        "readiness_keep_types": float(summary.get("keep_types") or 0.0),
        "readiness_insufficient_types": float(summary.get("insufficient_types") or 0.0),
        "readiness_clusters_total": float(summary.get("clusters_total") or 0.0),
    }
