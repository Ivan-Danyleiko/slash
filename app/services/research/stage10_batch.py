from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage10_final_report import (
    build_stage10_final_report,
    extract_stage10_final_report_metrics,
)
from app.services.research.stage10_module_audit import (
    build_stage10_module_audit_report,
    extract_stage10_module_audit_metrics,
)
from app.services.research.stage10_replay import (
    build_stage10_replay_report,
    extract_stage10_replay_metrics,
)
from app.services.research.stage10_timeline_quality import (
    build_stage10_timeline_quality_report,
    extract_stage10_timeline_quality_metrics,
)
from app.services.research.stage10_timeline_backfill import (
    build_stage10_timeline_backfill_plan,
    extract_stage10_timeline_backfill_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def build_stage10_batch_report(
    db: Session,
    *,
    settings: Settings,
    days: int = 365,
    limit: int = 5000,
    event_target: int = 100,
) -> dict[str, Any]:
    replay = build_stage10_replay_report(
        db,
        settings=settings,
        days=days,
        limit=limit,
        event_target=event_target,
        persist_rows=True,
    )
    timeline = build_stage10_timeline_quality_report(db, days=days)
    backfill = build_stage10_timeline_backfill_plan(db, days=days, limit=limit)
    audit = build_stage10_module_audit_report(db, settings=settings)
    final = build_stage10_final_report(
        db,
        settings=settings,
        days=days,
        limit=limit,
        event_target=event_target,
        replay_report=replay,
        module_audit_report=audit,
    )

    tracked = {
        "stage10_replay": record_stage5_experiment(
            run_name="stage10_replay_batch",
            params={"report_type": "stage10_replay", "days": days, "limit": limit, "event_target": event_target},
            metrics=extract_stage10_replay_metrics(replay),
            tags={"stage": "stage10"},
        ),
        "stage10_module_audit": record_stage5_experiment(
            run_name="stage10_module_audit_batch",
            params={"report_type": "stage10_module_audit"},
            metrics=extract_stage10_module_audit_metrics(audit),
            tags={"stage": "stage10"},
        ),
        "stage10_timeline_quality": record_stage5_experiment(
            run_name="stage10_timeline_quality_batch",
            params={"report_type": "stage10_timeline_quality", "days": days},
            metrics=extract_stage10_timeline_quality_metrics(timeline),
            tags={"stage": "stage10"},
        ),
        "stage10_timeline_backfill_plan": record_stage5_experiment(
            run_name="stage10_timeline_backfill_plan_batch",
            params={"report_type": "stage10_timeline_backfill_plan", "days": days, "limit": limit},
            metrics=extract_stage10_timeline_backfill_metrics(backfill),
            tags={"stage": "stage10"},
        ),
        "stage10_final_report": record_stage5_experiment(
            run_name="stage10_final_report_batch",
            params={"report_type": "stage10_final_report", "days": days, "limit": limit, "event_target": event_target},
            metrics=extract_stage10_final_report_metrics(final),
            tags={"stage": "stage10", "final_decision": str(final.get("final_decision") or "WARN")},
        ),
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "reports": {
            "stage10_replay": replay,
            "stage10_timeline_quality": timeline,
            "stage10_timeline_backfill_plan": backfill,
            "stage10_module_audit": audit,
            "stage10_final_report": final,
        },
        "tracking": tracked,
    }
