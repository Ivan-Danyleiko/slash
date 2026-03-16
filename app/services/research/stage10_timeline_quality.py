from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Stage10ReplayRow


def build_stage10_timeline_quality_report(db: Session, *, days: int = 365) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    rows = list(
        db.scalars(
            select(Stage10ReplayRow)
            .where(Stage10ReplayRow.replay_timestamp >= cutoff)
            .order_by(Stage10ReplayRow.replay_timestamp.desc())
            .limit(50000)
        )
    )

    total = len(rows)
    by_source: dict[str, int] = {}
    by_platform_insufficient: dict[str, int] = {}
    by_category_insufficient: dict[str, int] = {}
    insufficient = 0
    for row in rows:
        source = str((row.features_snapshot or {}).get("timeline_source") or "none")
        by_source[source] = by_source.get(source, 0) + 1
        if bool(row.leakage_violation) and any(
            str(code).startswith("data_insufficient_timeline") for code in (row.leakage_reason_codes or [])
        ):
            insufficient += 1
            p = str(row.platform or "unknown")
            c = str(row.category or "other")
            by_platform_insufficient[p] = by_platform_insufficient.get(p, 0) + 1
            by_category_insufficient[c] = by_category_insufficient.get(c, 0) + 1

    source_share = {k: (v / total) if total else 0.0 for k, v in by_source.items()}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": int(days),
        "rows_total": total,
        "timeline_source_counts": by_source,
        "timeline_source_share": source_share,
        "data_insufficient_timeline_count": insufficient,
        "data_insufficient_timeline_share": (insufficient / total) if total else 1.0,
        "insufficient_by_platform": by_platform_insufficient,
        "insufficient_by_category": by_category_insufficient,
    }


def extract_stage10_timeline_quality_metrics(report: dict[str, Any]) -> dict[str, float]:
    return {
        "stage10_timeline_rows_total": float(report.get("rows_total") or 0.0),
        "stage10_timeline_insufficient_share": float(report.get("data_insufficient_timeline_share") or 1.0),
    }
