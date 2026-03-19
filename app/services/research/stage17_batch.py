from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage17_tail_report import (
    build_stage17_tail_report,
    extract_stage17_tail_report_metrics,
)
from app.services.research.tracking import record_stage5_experiment
from app.services.stage17.tail_executor import run_stage17_tail_cycle


def build_stage17_batch_report(
    db: Session,
    *,
    settings: Settings,
    days: int = 60,
    cycle_limit: int = 20,
) -> dict[str, Any]:
    cycle = run_stage17_tail_cycle(
        db,
        settings=settings,
        limit=cycle_limit,
    )
    tail_report = build_stage17_tail_report(
        db,
        settings=settings,
        days=days,
        persist=True,
    )

    tracked = {
        "stage17_cycle": record_stage5_experiment(
            run_name="stage17_cycle_batch",
            params={"report_type": "stage17_cycle", "cycle_limit": cycle_limit},
            metrics={
                "stage17_cycle_opened": float(cycle.get("opened") or 0.0),
                "stage17_cycle_closed": float(cycle.get("closed") or 0.0),
                "stage17_cycle_skipped": float(cycle.get("skipped") or 0.0),
                "stage17_cycle_breaker_blocked": 1.0 if bool(cycle.get("breaker_blocked")) else 0.0,
            },
            tags={"stage": "stage17"},
        ),
        "stage17_tail_report": record_stage5_experiment(
            run_name="stage17_tail_report_batch",
            params={"report_type": "stage17_tail_report", "days": days},
            metrics=extract_stage17_tail_report_metrics(tail_report),
            tags={"stage": "stage17", "final_decision": str(tail_report.get("final_decision") or "")},
        ),
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "reports": {
            "stage17_cycle": cycle,
            "stage17_tail_report": tail_report,
        },
        "tracking": tracked,
    }
