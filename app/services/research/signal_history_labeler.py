from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import MarketSnapshot, SignalHistory


@dataclass(frozen=True)
class LabelingParams:
    hours: int
    field_name: str


_HORIZONS: dict[str, LabelingParams] = {
    "1h": LabelingParams(hours=1, field_name="probability_after_1h"),
    "6h": LabelingParams(hours=6, field_name="probability_after_6h"),
    "24h": LabelingParams(hours=24, field_name="probability_after_24h"),
}


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def label_signal_history_from_snapshots(
    db: Session,
    *,
    horizon: str,
    batch_size: int = 500,
    max_snapshot_lag_hours: float = 2.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    params = _HORIZONS.get((horizon or "").strip().lower())
    if params is None:
        return {
            "status": "error",
            "error": f"unsupported horizon '{horizon}'",
            "supported": sorted(_HORIZONS.keys()),
        }

    now = datetime.now(UTC)
    target_before = now - timedelta(hours=params.hours)
    field = getattr(SignalHistory, params.field_name)
    lag_td = timedelta(hours=max(0.1, float(max_snapshot_lag_hours)))
    limit = max(1, int(batch_size))

    rows = list(
        db.scalars(
            select(SignalHistory)
            .where(
                SignalHistory.timestamp <= target_before,
                field.is_(None),
            )
            .order_by(SignalHistory.timestamp.asc())
            .limit(limit)
        )
    )

    updated = 0
    already_labeled = 0
    skipped_market_missing = 0
    skipped_snapshot_missing = 0
    skipped_probability_missing = 0
    market_ids = sorted({int(r.market_id) for r in rows if r.market_id is not None})
    target_by_row_id: dict[int, datetime] = {}
    rows_missing_timestamp: set[int] = set()
    targets: list[datetime] = []
    for row in rows:
        signal_ts = _as_utc(row.timestamp)
        if signal_ts is None:
            rows_missing_timestamp.add(int(row.id))
            continue
        target = signal_ts + timedelta(hours=params.hours)
        target_by_row_id[int(row.id)] = target
        targets.append(target)

    snapshots_by_market: dict[int, dict[str, list]] = {}
    if market_ids and targets:
        min_target = min(targets)
        max_target = max(targets) + lag_td
        snap_rows = list(
            db.scalars(
                select(MarketSnapshot)
                .where(MarketSnapshot.market_id.in_(market_ids))
                .where(MarketSnapshot.fetched_at >= min_target)
                .where(MarketSnapshot.fetched_at <= max_target)
                .order_by(MarketSnapshot.market_id.asc(), MarketSnapshot.fetched_at.asc())
            )
        )
        for snap in snap_rows:
            fetched = _as_utc(snap.fetched_at)
            if fetched is None:
                continue
            mid = int(snap.market_id)
            buf = snapshots_by_market.setdefault(mid, {"times": [], "snaps": []})
            buf["times"].append(fetched)
            buf["snaps"].append(snap)

    for row in rows:
        if getattr(row, params.field_name) is not None:
            already_labeled += 1
            continue
        if row.market_id is None:
            row.missing_label_reason = "market_missing"
            skipped_market_missing += 1
            continue

        row_id = int(row.id)
        if row_id in rows_missing_timestamp:
            row.missing_label_reason = "timestamp_missing"
            skipped_snapshot_missing += 1
            continue

        target_ts = target_by_row_id.get(row_id)
        if target_ts is None:
            row.missing_label_reason = "timestamp_missing"
            skipped_snapshot_missing += 1
            continue
        snap = None
        market_snapshots = snapshots_by_market.get(int(row.market_id or 0))
        if market_snapshots is not None:
            times: list[datetime] = market_snapshots["times"]
            snaps: list[MarketSnapshot] = market_snapshots["snaps"]
            idx = bisect_left(times, target_ts)
            if idx < len(times) and times[idx] <= (target_ts + lag_td):
                snap = snaps[idx]

        if snap is None:
            row.missing_label_reason = f"snapshot_{params.hours}h_missing"
            skipped_snapshot_missing += 1
            continue
        if snap.probability_yes is None:
            row.missing_label_reason = f"snapshot_{params.hours}h_probability_missing"
            skipped_probability_missing += 1
            continue

        setattr(row, params.field_name, float(snap.probability_yes))
        row.labeled_at = now
        row.missing_label_reason = None
        updated += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "status": "ok",
        "horizon": horizon,
        "horizon_hours": params.hours,
        "field_name": params.field_name,
        "target_before_ts": target_before.isoformat(),
        "batch_size": limit,
        "max_snapshot_lag_hours": float(max_snapshot_lag_hours),
        "dry_run": bool(dry_run),
        "candidates": len(rows),
        "updated": updated,
        "already_labeled": already_labeled,
        "skipped_market_missing": skipped_market_missing,
        "skipped_snapshot_missing": skipped_snapshot_missing,
        "skipped_probability_missing": skipped_probability_missing,
    }
