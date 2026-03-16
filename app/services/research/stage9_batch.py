from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage9_final_report import (
    build_stage9_final_report,
    extract_stage9_final_report_metrics,
)
from app.services.research.stage9_reports import (
    build_stage9_consensus_quality_report,
    build_stage9_directional_labeling_report,
    build_stage9_execution_realism_report,
)
from app.services.research.tracking import record_stage5_experiment


def build_stage9_batch_report(
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
    final_report = build_stage9_final_report(
        db,
        settings=settings,
        days_consensus=days_consensus,
        days_labeling=days_labeling,
        days_execution=days_execution,
    )

    tracked = {
        "stage9_consensus_quality": record_stage5_experiment(
            run_name="stage9_consensus_quality_batch",
            params={"report_type": "stage9_consensus_quality", "days": days_consensus},
            metrics={
                "metaculus_median_fill_rate": float(consensus.get("metaculus_median_fill_rate") or 0.0),
                "consensus_2source_share": float(consensus.get("consensus_2source_share") or 0.0),
                "consensus_3source_share": float(consensus.get("consensus_3source_share") or 0.0),
            },
            tags={"stage": "stage9"},
        ),
        "stage9_directional_labeling": record_stage5_experiment(
            run_name="stage9_directional_labeling_batch",
            params={"report_type": "stage9_directional_labeling", "days": days_labeling},
            metrics={
                "direction_labeled_share": float(labeling.get("direction_labeled_share") or 0.0),
                "direction_missing_label_share": float(labeling.get("direction_missing_label_share") or 0.0),
                "void_outcome_share": float(labeling.get("void_outcome_share") or 0.0),
            },
            tags={"stage": "stage9"},
        ),
        "stage9_execution_realism": record_stage5_experiment(
            run_name="stage9_execution_realism_batch",
            params={"report_type": "stage9_execution_realism", "days": days_execution},
            metrics={
                "non_zero_edge_share": float(execution.get("non_zero_edge_share") or 0.0),
                "spread_coverage_share": float(execution.get("spread_coverage_share") or 0.0),
                "open_interest_coverage_share": float(execution.get("open_interest_coverage_share") or 0.0),
                "brier_skill_score": float(execution.get("brier_skill_score") or 0.0),
                "ece": float(execution.get("ece") or 0.0),
                "longshot_bias_error_0_15pct": float(execution.get("longshot_bias_error_0_15pct") or 0.0),
                "precision_at_25": float(execution.get("precision_at_25") or 0.0),
                "auprc": float(execution.get("auprc") or 0.0),
            },
            tags={"stage": "stage9"},
        ),
        "stage9_final_report": record_stage5_experiment(
            run_name="stage9_final_report_batch",
            params={"report_type": "stage9_final_report"},
            metrics=extract_stage9_final_report_metrics(final_report),
            tags={
                "stage": "stage9",
                "final_decision": str(final_report.get("final_decision") or "WARN"),
            },
        ),
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "reports": {
            "stage9_consensus_quality": consensus,
            "stage9_directional_labeling": labeling,
            "stage9_execution_realism": execution,
            "stage9_final_report": final_report,
        },
        "tracking": tracked,
    }
