from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


FORBIDDEN_FEATURE_KEYS: tuple[str, ...] = (
    "resolved_probability",
    "resolved_success",
    "resolved_outcome",
    "probability_after_15m",
    "probability_after_30m",
    "probability_after_1h",
    "probability_after_6h",
    "probability_after_24h",
    "final_report_decision",
)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def detect_leakage_for_row(
    *,
    replay_timestamp: datetime,
    feature_observed_at_max: datetime | None,
    feature_keys: list[str],
    embargo_seconds: int,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    replay_utc = _as_utc(replay_timestamp) or datetime.now(UTC)
    observed_utc = _as_utc(feature_observed_at_max)

    if observed_utc is not None:
        embargo_cutoff = replay_utc.timestamp() - float(max(0, int(embargo_seconds)))
        if observed_utc.timestamp() > embargo_cutoff:
            reasons.append("feature_timestamp_after_embargo")

    if observed_utc is not None and observed_utc.timestamp() > replay_utc.timestamp():
        reasons.append("feature_timestamp_after_replay")

    fset = {str(k).strip() for k in feature_keys if str(k).strip()}
    for forbidden in FORBIDDEN_FEATURE_KEYS:
        if forbidden in fset:
            reasons.append(f"forbidden_feature:{forbidden}")

    has_violation = len(reasons) > 0
    return has_violation, reasons


def extract_stage10_leakage_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = dict(report.get("summary") or {})
    return {
        "stage10_rows_total": float(summary.get("rows_total") or 0.0),
        "stage10_events_total": float(summary.get("events_total") or 0.0),
        "stage10_leakage_violations_count": float(summary.get("leakage_violations_count") or 0.0),
        "stage10_leakage_violation_rate": float(summary.get("leakage_violation_rate") or 0.0),
    }
