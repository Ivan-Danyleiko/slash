from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.research.final_report import build_stage5_final_report
from app.services.research.tracking import read_stage5_experiments


def build_stage5_export_package(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_labeled_returns: int = 30,
    experiments_limit: int = 200,
) -> dict[str, Any]:
    final_report = build_stage5_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    experiments = read_stage5_experiments(limit=experiments_limit)
    keep = final_report.get("decision_summary", {}).get("keep_types", []) or []
    modify = final_report.get("decision_summary", {}).get("modify_types", []) or []
    remove = final_report.get("decision_summary", {}).get("remove_types", []) or []
    insufficient = final_report.get("decision_summary", {}).get("insufficient_types", []) or []

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_name": "stage5_export_package",
        "params": {
            "days": days,
            "horizon": horizon,
            "min_labeled_returns": min_labeled_returns,
            "experiments_limit": experiments_limit,
        },
        "summary": {
            "readiness": final_report.get("readiness"),
            "keep_count": len(keep),
            "modify_count": len(modify),
            "remove_count": len(remove),
            "insufficient_count": len(insufficient),
            "best_ranking_formula": final_report.get("key_findings", {}).get("best_ranking_formula"),
            "best_platform": final_report.get("key_findings", {}).get("best_platform"),
            "clusters_total": final_report.get("key_findings", {}).get("clusters_total", 0),
            "experiments_count": experiments.get("count", 0),
        },
        "final_report": final_report,
        "experiments_registry": experiments,
    }


def build_stage5_export_decision_rows(package: dict[str, Any]) -> list[dict[str, Any]]:
    report = package.get("final_report") or {}
    rows = list((report.get("sections") or {}).get("signal_types", {}).get("rows") or [])
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "signal_type": row.get("signal_type"),
                "decision": row.get("decision"),
                "returns_labeled": row.get("returns_labeled"),
                "avg_return": row.get("avg_return"),
                "hit_rate": row.get("hit_rate"),
                "sharpe_like": row.get("sharpe_like"),
                "risk_of_ruin": row.get("risk_of_ruin"),
                "reason": row.get("reason"),
            }
        )
    return out
