from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.research.stage8_final_report import (
    build_stage8_final_report,
    extract_stage8_final_report_metrics,
)
from app.services.research.stage8_shadow_ledger import (
    build_stage8_shadow_ledger_report,
    extract_stage8_shadow_ledger_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def build_stage8_batch_report(
    db: Session,
    *,
    settings: Settings,
    lookback_days: int = 14,
    limit: int = 300,
) -> dict[str, Any]:
    shadow = build_stage8_shadow_ledger_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
    )
    final_report = build_stage8_final_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
        shadow_report=shadow,
    )
    shadow_tracking = record_stage5_experiment(
        run_name="stage8_shadow_ledger_batch",
        params={"report_type": "stage8_shadow_ledger", "lookback_days": lookback_days, "limit": limit},
        metrics=extract_stage8_shadow_ledger_metrics(shadow),
        tags={"policy_profile": settings.stage8_policy_profile},
    )
    final_tracking = record_stage5_experiment(
        run_name="stage8_final_report_batch",
        params={"report_type": "stage8_final_report", "lookback_days": lookback_days, "limit": limit},
        metrics=extract_stage8_final_report_metrics(final_report),
        tags={"final_decision": str(final_report.get("final_decision") or "")},
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lookback_days": lookback_days,
        "limit": limit,
        "reports": {
            "stage8_shadow_ledger": shadow,
            "stage8_final_report": final_report,
        },
        "tracking": {
            "stage8_shadow_ledger": shadow_tracking,
            "stage8_final_report": final_tracking,
        },
    }
