from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.models import Market, MarketSnapshot, SignalHistory


@dataclass(slots=True)
class TimelinePoint:
    probability_t: float | None
    observed_at: datetime | None
    source: str
    sufficient: bool
    reason_codes: list[str]


def _as_utc(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        raw = dt.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _as_utc(parsed)
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _safe_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        # unix seconds fallback
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except Exception:  # noqa: BLE001
            return None
    return None


def _hget(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _latest_snapshot_before(snapshots: list[MarketSnapshot], ts: datetime) -> MarketSnapshot | None:
    ts_utc = _as_utc(ts) or datetime.now(UTC)
    best: MarketSnapshot | None = None
    for snap in snapshots:
        fetched = _as_utc(snap.fetched_at)
        if fetched is None or fetched > ts_utc:
            continue
        if best is None:
            best = snap
            continue
        best_fetched = _as_utc(best.fetched_at)
        if best_fetched is None or fetched > best_fetched:
            best = snap
    return best


def _latest_payload_point_before(payload_points: list[dict[str, Any]], ts: datetime) -> tuple[float | None, datetime | None]:
    ts_utc = _as_utc(ts) or datetime.now(UTC)
    best_prob: float | None = None
    best_ts: datetime | None = None
    for point in payload_points:
        p = _safe_float(point.get("probability") or point.get("p") or point.get("q2"))
        t = _safe_datetime(point.get("timestamp") or point.get("ts") or point.get("time"))
        if p is None or t is None or t > ts_utc:
            continue
        if best_ts is None or t > best_ts:
            best_ts = t
            best_prob = p
    return best_prob, best_ts


def resolve_timeline_point(
    *,
    market: Market | None,
    history_row: SignalHistory | dict[str, Any],
    replay_timestamp: datetime,
    snapshots: list[MarketSnapshot],
) -> TimelinePoint:
    # 1) Local snapshots are preferred: deterministic and replay-safe.
    snap = _latest_snapshot_before(snapshots, replay_timestamp)
    if snap is not None and _safe_float(snap.probability_yes) is not None:
        return TimelinePoint(
            probability_t=float(snap.probability_yes),
            observed_at=_as_utc(snap.fetched_at),
            source="snapshot",
            sufficient=True,
            reason_codes=[],
        )

    payload = (market.source_payload if market and isinstance(market.source_payload, dict) else {}) or {}

    # 2) Manifold bets-derived history if persisted in payload.
    manifold_points = payload.get("manifold_bets_history")
    if isinstance(manifold_points, list):
        p, t = _latest_payload_point_before([x for x in manifold_points if isinstance(x, dict)], replay_timestamp)
        if p is not None and t is not None:
            return TimelinePoint(
                probability_t=p,
                observed_at=t,
                source="manifold_bets",
                sufficient=True,
                reason_codes=[],
            )

    # 3) Metaculus prediction history if persisted in payload.
    meta_points = payload.get("metaculus_prediction_history")
    if isinstance(meta_points, list):
        p, t = _latest_payload_point_before([x for x in meta_points if isinstance(x, dict)], replay_timestamp)
        if p is not None and t is not None:
            return TimelinePoint(
                probability_t=p,
                observed_at=t,
                source="metaculus_history",
                sufficient=True,
                reason_codes=[],
            )

    # 4) Fallback to signal_history snapshot at signal time.
    # Any row with probability_at_signal is acceptable — the TZ allows "local snapshots"
    # as a valid timeline source when no external history is available.
    p0 = _safe_float(_hget(history_row, "probability_at_signal"))
    source_tag = str(_hget(history_row, "source_tag") or "").strip().lower()
    ts0 = _as_utc(_hget(history_row, "timestamp"))
    if p0 is not None and ts0 is not None:
        return TimelinePoint(
            probability_t=p0,
            observed_at=ts0,
            source=f"signal_history:{source_tag}" if source_tag else "signal_history_local",
            sufficient=True,
            reason_codes=[],
        )
    if p0 is not None:
        return TimelinePoint(
            probability_t=p0,
            observed_at=ts0,
            source="signal_history_fallback",
            sufficient=False,
            reason_codes=["data_insufficient_timeline"],
        )

    return TimelinePoint(
        probability_t=None,
        observed_at=None,
        source="none",
        sufficient=False,
        reason_codes=["data_insufficient_timeline", "probability_t_missing"],
    )
