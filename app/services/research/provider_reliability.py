from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import JobRun

_KNOWN_PLATFORMS = ("MANIFOLD", "METACULUS", "POLYMARKET")


def _normalize_sync_details(details: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(details, dict):
        return {}
    if "result" in details and isinstance(details["result"], dict):
        base = details["result"]
    else:
        base = details
    out: dict[str, dict[str, Any]] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            out[str(key).upper()] = value
    return out


def _duration_seconds(job: JobRun) -> float | None:
    if job.started_at is None or job.finished_at is None:
        return None
    started = job.started_at
    finished = job.finished_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    diff = (finished - started).total_seconds()
    return max(0.0, diff)


def _is_rate_limit_error(error_text: str) -> bool:
    text = (error_text or "").lower()
    return ("429" in text) or ("rate limit" in text) or ("too many requests" in text)


def build_provider_reliability_report(
    db: Session,
    *,
    days: int = 7,
    limit_runs: int = 1000,
) -> dict:
    days = max(1, min(int(days), 365))
    limit_runs = max(1, min(int(limit_runs), 10000))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        db.scalars(
            select(JobRun)
            .where(JobRun.job_name == "sync_all_platforms", JobRun.started_at >= cutoff)
            .order_by(JobRun.started_at.desc())
            .limit(limit_runs)
        )
    )

    per_platform: dict[str, dict[str, float]] = {
        p: {
            "runs": 0.0,
            "success_runs": 0.0,
            "error_runs": 0.0,
            "fetched_total": 0.0,
            "inserted_total": 0.0,
            "updated_total": 0.0,
            "empty_runs": 0.0,
            "rate_limit_errors": 0.0,
            "duration_sum_seconds": 0.0,
            "duration_count": 0.0,
        }
        for p in _KNOWN_PLATFORMS
    }

    unknown_platforms: dict[str, dict[str, float]] = {}

    def _bucket(name: str) -> dict[str, float]:
        key = name.upper()
        if key in per_platform:
            return per_platform[key]
        if key not in unknown_platforms:
            unknown_platforms[key] = {
                "runs": 0.0,
                "success_runs": 0.0,
                "error_runs": 0.0,
                "fetched_total": 0.0,
                "inserted_total": 0.0,
                "updated_total": 0.0,
                "empty_runs": 0.0,
                "rate_limit_errors": 0.0,
                "duration_sum_seconds": 0.0,
                "duration_count": 0.0,
            }
        return unknown_platforms[key]

    for job in rows:
        duration = _duration_seconds(job)
        parsed = _normalize_sync_details(job.details)
        if not parsed:
            continue
        for platform_name, stats in parsed.items():
            b = _bucket(platform_name)
            b["runs"] += 1.0
            fetched = float(stats.get("fetched", 0) or 0)
            inserted = float(stats.get("inserted", 0) or 0)
            updated = float(stats.get("updated", 0) or 0)
            errors = float(stats.get("errors", 0) or 0)
            error_text = str(stats.get("error") or "")
            b["fetched_total"] += fetched
            b["inserted_total"] += inserted
            b["updated_total"] += updated
            if fetched <= 0:
                b["empty_runs"] += 1.0
            if errors > 0:
                b["error_runs"] += 1.0
            else:
                b["success_runs"] += 1.0
            if _is_rate_limit_error(error_text):
                b["rate_limit_errors"] += 1.0
            if duration is not None:
                b["duration_sum_seconds"] += duration
                b["duration_count"] += 1.0

    def _finalize(name: str, b: dict[str, float]) -> dict[str, float | str]:
        runs = max(0.0, b["runs"])
        return {
            "platform": name,
            "runs": int(runs),
            "success_runs": int(b["success_runs"]),
            "error_runs": int(b["error_runs"]),
            "error_rate": round((b["error_runs"] / runs), 4) if runs > 0 else 0.0,
            "availability": round((b["success_runs"] / runs), 4) if runs > 0 else 0.0,
            "rate_limit_errors": int(b["rate_limit_errors"]),
            "fetched_total": int(b["fetched_total"]),
            "inserted_total": int(b["inserted_total"]),
            "updated_total": int(b["updated_total"]),
            "empty_runs": int(b["empty_runs"]),
            "avg_duration_seconds": round((b["duration_sum_seconds"] / b["duration_count"]), 3)
            if b["duration_count"] > 0
            else 0.0,
        }

    rows_out = [_finalize(name, b) for name, b in per_platform.items() if b["runs"] > 0]
    rows_out.extend(_finalize(name, b) for name, b in unknown_platforms.items() if b["runs"] > 0)
    rows_out.sort(key=lambda x: (float(x["error_rate"]), -int(x["runs"])))

    total_runs = sum(int(r["runs"]) for r in rows_out)
    total_errors = sum(int(r["error_runs"]) for r in rows_out)
    total_success = sum(int(r["success_runs"]) for r in rows_out)
    total_rate_limit = sum(int(r["rate_limit_errors"]) for r in rows_out)
    return {
        "period_days": days,
        "sync_runs_scanned": len(rows),
        "platforms_total": len(rows_out),
        "overall": {
            "runs": total_runs,
            "success_runs": total_success,
            "error_runs": total_errors,
            "error_rate": round((total_errors / total_runs), 4) if total_runs > 0 else 0.0,
            "availability": round((total_success / total_runs), 4) if total_runs > 0 else 0.0,
            "rate_limit_errors": total_rate_limit,
        },
        "by_platform": rows_out,
    }


def extract_provider_reliability_metrics(report: dict[str, Any]) -> dict[str, float]:
    overall = report.get("overall") or {}
    return {
        "provider_runs": float(overall.get("runs") or 0.0),
        "provider_error_rate": float(overall.get("error_rate") or 0.0),
        "provider_availability": float(overall.get("availability") or 0.0),
        "provider_rate_limit_errors": float(overall.get("rate_limit_errors") or 0.0),
    }
