from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.models import SignalHistory


def _check_range(
    rows: list[SignalHistory],
    *,
    name: str,
    getter: Callable[[SignalHistory], float | None],
    lower: float,
    upper: float,
    max_examples: int = 5,
) -> dict:
    bad_examples: list[dict] = []
    bad = 0
    total = 0
    for row in rows:
        value = getter(row)
        if value is None:
            continue
        total += 1
        if not (lower <= float(value) <= upper):
            bad += 1
            if len(bad_examples) < max_examples:
                bad_examples.append({"id": row.id, "value": float(value)})
    return {
        "name": name,
        "success": bad == 0,
        "checked_values": total,
        "unexpected_count": bad,
        "unexpected_examples": bad_examples,
    }


def _check_labeled_at_consistency(rows: list[SignalHistory], *, max_examples: int = 5) -> dict:
    bad_examples: list[dict] = []
    bad = 0
    for row in rows:
        has_horizon_label = (
            row.probability_after_1h is not None
            or row.probability_after_6h is not None
            or row.probability_after_24h is not None
        )
        if has_horizon_label and row.labeled_at is None:
            bad += 1
            if len(bad_examples) < max_examples:
                bad_examples.append({"id": row.id, "issue": "labeled_probs_without_labeled_at"})
    return {
        "name": "labeled_at_consistency",
        "success": bad == 0,
        "checked_values": len(rows),
        "unexpected_count": bad,
        "unexpected_examples": bad_examples,
    }


def _check_resolution_consistency(rows: list[SignalHistory], *, max_examples: int = 5) -> dict:
    bad_examples: list[dict] = []
    bad = 0
    for row in rows:
        if row.resolved_success is not None and row.resolved_probability is None:
            bad += 1
            if len(bad_examples) < max_examples:
                bad_examples.append({"id": row.id, "issue": "resolved_success_without_probability"})
    return {
        "name": "resolution_consistency",
        "success": bad == 0,
        "checked_values": len(rows),
        "unexpected_count": bad,
        "unexpected_examples": bad_examples,
    }


def _check_future_timestamps(rows: list[SignalHistory], *, max_examples: int = 5) -> dict:
    bad_examples: list[dict] = []
    bad = 0
    now = datetime.now(UTC)
    for row in rows:
        ts = row.timestamp
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts > now:
            bad += 1
            if len(bad_examples) < max_examples:
                bad_examples.append({"id": row.id, "timestamp": ts.isoformat()})
    return {
        "name": "no_future_timestamps",
        "success": bad == 0,
        "checked_values": len(rows),
        "unexpected_count": bad,
        "unexpected_examples": bad_examples,
    }


def _check_idempotent_key_collisions(rows: list[SignalHistory], *, max_examples: int = 5) -> dict:
    seen: dict[tuple[str, int, int | None, str, str], int] = {}
    collisions: list[dict] = []
    duplicate_count = 0
    for row in rows:
        bucket = row.timestamp_bucket or row.timestamp
        if bucket is None:
            continue
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=UTC)
        key = (
            str(row.platform or ""),
            int(row.market_id),
            int(row.related_market_id) if row.related_market_id is not None else None,
            str(row.signal_type.value),
            bucket.isoformat(),
        )
        if key in seen:
            duplicate_count += 1
            if len(collisions) < max_examples:
                collisions.append({"existing_id": seen[key], "duplicate_id": row.id, "key": key})
        else:
            seen[key] = int(row.id)
    return {
        "name": "idempotent_key_collisions",
        "success": duplicate_count == 0,
        "checked_values": len(rows),
        "unexpected_count": duplicate_count,
        "unexpected_examples": collisions,
    }


def _run_great_expectations_stub(enabled: bool) -> dict:
    if not enabled:
        return {"enabled": False, "status": "skipped"}
    if find_spec("great_expectations") is None:
        return {
            "enabled": True,
            "status": "not_installed",
            "reason": "Install great_expectations to run GE checks.",
        }
    return {
        "enabled": True,
        "status": "available_not_executed",
        "reason": "GE package found; direct runtime integration deferred to notebook/pipeline job.",
    }


def build_signal_history_data_quality_report(
    db: Session,
    *,
    days: int = 30,
    limit: int = 10000,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 100000))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    rows = list(
        db.scalars(
            select(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff)
            .order_by(SignalHistory.timestamp.desc())
            .limit(limit)
        )
    )

    checks = [
        _check_range(
            rows,
            name="probability_at_signal_in_[0,1]",
            getter=lambda r: r.probability_at_signal,
            lower=0.0,
            upper=1.0,
        ),
        _check_range(
            rows,
            name="probability_after_1h_in_[0,1]",
            getter=lambda r: r.probability_after_1h,
            lower=0.0,
            upper=1.0,
        ),
        _check_range(
            rows,
            name="probability_after_6h_in_[0,1]",
            getter=lambda r: r.probability_after_6h,
            lower=0.0,
            upper=1.0,
        ),
        _check_range(
            rows,
            name="probability_after_24h_in_[0,1]",
            getter=lambda r: r.probability_after_24h,
            lower=0.0,
            upper=1.0,
        ),
        _check_range(
            rows,
            name="resolved_probability_in_[0,1]",
            getter=lambda r: r.resolved_probability,
            lower=0.0,
            upper=1.0,
        ),
        _check_range(
            rows,
            name="divergence_in_[0,1]",
            getter=lambda r: r.divergence,
            lower=0.0,
            upper=1.0,
        ),
        _check_labeled_at_consistency(rows),
        _check_resolution_consistency(rows),
        _check_future_timestamps(rows),
        _check_idempotent_key_collisions(rows),
    ]

    failed = [c for c in checks if not c["success"]]
    ge = _run_great_expectations_stub(settings.research_great_expectations_enabled)
    return {
        "period_days": days,
        "rows_scanned": len(rows),
        "rows_total": len(rows),
        "limit": limit,
        "passed": len(failed) == 0,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
        "great_expectations": ge,
    }


def extract_data_quality_metrics(report: dict[str, Any]) -> dict[str, float]:
    checks = list(report.get("checks") or [])
    checks_total = max(1, len(checks))
    failed = sum(1 for c in checks if not bool(c.get("success")))
    total_unexpected = sum(int(c.get("unexpected_count") or 0) for c in checks)
    rows_scanned = int(report.get("rows_scanned") or 0)
    return {
        "dq_passed": 1.0 if bool(report.get("passed")) else 0.0,
        "dq_checks_failed": float(failed),
        "dq_failure_ratio": float(failed / checks_total),
        "dq_unexpected_total": float(total_unexpected),
        "dq_rows_scanned": float(rows_scanned),
    }
