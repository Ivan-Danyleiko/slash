from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import MarketSnapshot, SignalHistory

_HORIZON_FIELDS = [
    ("1h", "probability_after_1h", 1.0),
    ("6h", "probability_after_6h", 6.0),
    ("24h", "probability_after_24h", 24.0),
]


_ARCH_LIMITED_TYPES = {
    SignalType.LIQUIDITY_RISK.value,
    SignalType.WEIRD_MARKET.value,
}


def _parse_signal_type(signal_type: str | None) -> SignalType | None:
    if not signal_type:
        return None
    raw = signal_type.strip().upper()
    try:
        return SignalType(raw)
    except ValueError:
        return None


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _build_snapshot_index(db: Session, *, cutoff: datetime) -> dict[int, list[tuple[datetime, float]]]:
    rows = list(
        db.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.fetched_at >= cutoff)
            .order_by(MarketSnapshot.market_id.asc(), MarketSnapshot.fetched_at.asc())
        )
    )
    out: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    for row in rows:
        ts = _as_utc(row.fetched_at)
        if ts is None or row.probability_yes is None:
            continue
        out[row.market_id].append((ts, float(row.probability_yes)))
    return out


def _snapshot_prob_at_or_after(
    index: dict[int, list[tuple[datetime, float]]],
    *,
    market_id: int,
    target: datetime,
    grace_minutes: int,
) -> float | None:
    series = index.get(market_id) or []
    if not series:
        return None
    max_ts = target + timedelta(minutes=max(1, grace_minutes))
    for ts, prob in series:
        if ts >= target and ts <= max_ts:
            return prob
    return None


def _estimate_lifetime_hours(
    row: SignalHistory,
    *,
    close_ratio_threshold: float,
    min_initial_divergence: float,
    snapshot_index: dict[int, list[tuple[datetime, float]]],
    include_subhour: bool,
    subhour_grace_minutes: int,
) -> tuple[float | None, bool]:
    p0 = row.probability_at_signal
    pr = row.related_market_probability
    t0 = _as_utc(row.timestamp)
    if p0 is None or pr is None or t0 is None:
        return None, False

    initial_gap = abs(float(p0) - float(pr))
    if initial_gap < min_initial_divergence:
        return None, False
    target_gap = initial_gap * max(0.0, 1.0 - close_ratio_threshold)

    candidates: list[tuple[float, float]] = []
    subhour_observed = False

    if include_subhour:
        payload = row.simulated_trade or {}
        p15 = payload.get("probability_after_15m")
        p30 = payload.get("probability_after_30m")
        if not isinstance(p15, (int, float)):
            p15 = _snapshot_prob_at_or_after(
                snapshot_index,
                market_id=row.market_id,
                target=t0 + timedelta(minutes=15),
                grace_minutes=subhour_grace_minutes,
            )
        else:
            p15 = float(p15)
        if not isinstance(p30, (int, float)):
            p30 = _snapshot_prob_at_or_after(
                snapshot_index,
                market_id=row.market_id,
                target=t0 + timedelta(minutes=30),
                grace_minutes=subhour_grace_minutes,
            )
        else:
            p30 = float(p30)
        if p15 is not None:
            subhour_observed = True
            candidates.append((0.25, abs(float(p15) - float(pr))))
        if p30 is not None:
            subhour_observed = True
            candidates.append((0.5, abs(float(p30) - float(pr))))

    for _, field_name, hours in _HORIZON_FIELDS:
        ph = getattr(row, field_name)
        if ph is None:
            continue
        candidates.append((hours, abs(float(ph) - float(pr))))

    candidates.sort(key=lambda x: x[0])
    for hours, current_gap in candidates:
        if current_gap <= target_gap:
            return float(hours), subhour_observed
    return None, subhour_observed


def build_signal_lifetime_report(
    db: Session,
    *,
    days: int = 30,
    signal_type: str | None = None,
    close_ratio_threshold: float = 0.5,
    min_initial_divergence: float = 0.02,
    min_samples: int = 10,
    include_subhour: bool = True,
    subhour_grace_minutes: int = 20,
    architecture_min_subhour_coverage: float = 0.20,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    close_ratio_threshold = max(0.1, min(float(close_ratio_threshold), 0.95))
    min_initial_divergence = max(0.001, min(float(min_initial_divergence), 1.0))
    min_samples = max(1, min(int(min_samples), 10000))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    st = _parse_signal_type(signal_type)
    if signal_type and st is None:
        return {"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]}

    stmt = select(SignalHistory).where(SignalHistory.timestamp >= cutoff)
    if st is not None:
        stmt = stmt.where(SignalHistory.signal_type == st)
    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.desc())))

    snapshot_index = _build_snapshot_index(db, cutoff=cutoff) if include_subhour else {}

    by_type_lifetimes: dict[str, list[float]] = defaultdict(list)
    by_type_total = defaultdict(int)
    by_type_subhour_seen = defaultdict(int)
    by_type_closed_by_horizon: dict[str, dict[str, int]] = defaultdict(
        lambda: {"15m": 0, "30m": 0, "1h": 0, "6h": 0, "24h": 0}
    )

    for row in rows:
        t = row.signal_type.value
        lifetime, subhour_seen = _estimate_lifetime_hours(
            row,
            close_ratio_threshold=close_ratio_threshold,
            min_initial_divergence=min_initial_divergence,
            snapshot_index=snapshot_index,
            include_subhour=include_subhour,
            subhour_grace_minutes=subhour_grace_minutes,
        )
        by_type_total[t] += 1
        if subhour_seen:
            by_type_subhour_seen[t] += 1
        if lifetime is not None:
            by_type_lifetimes[t].append(lifetime)
            if lifetime <= 0.25:
                by_type_closed_by_horizon[t]["15m"] += 1
            if lifetime <= 0.5:
                by_type_closed_by_horizon[t]["30m"] += 1
            if lifetime <= 1:
                by_type_closed_by_horizon[t]["1h"] += 1
            if lifetime <= 6:
                by_type_closed_by_horizon[t]["6h"] += 1
            if lifetime <= 24:
                by_type_closed_by_horizon[t]["24h"] += 1

    rows_out: list[dict[str, Any]] = []
    for t, total in sorted(by_type_total.items()):
        lifetimes = by_type_lifetimes.get(t, [])
        subhour_coverage = (by_type_subhour_seen[t] / total) if total else 0.0

        if total < min_samples:
            rows_out.append(
                {
                    "signal_type": t,
                    "samples_total": total,
                    "samples_with_lifetime": len(lifetimes),
                    "subhour_coverage": round(subhour_coverage, 4),
                    "status": "INSUFFICIENT_DATA",
                    "reason": f"Need >= {min_samples} samples",
                }
            )
            continue

        if include_subhour and t in _ARCH_LIMITED_TYPES and subhour_coverage < architecture_min_subhour_coverage:
            rows_out.append(
                {
                    "signal_type": t,
                    "samples_total": total,
                    "samples_with_lifetime": len(lifetimes),
                    "subhour_coverage": round(subhour_coverage, 4),
                    "status": "INSUFFICIENT_ARCHITECTURE",
                    "reason": (
                        "Sub-hour coverage below architecture threshold; "
                        "high-frequency component required for reliable 15m/30m lifetime."
                    ),
                }
            )
            continue

        if not lifetimes:
            rows_out.append(
                {
                    "signal_type": t,
                    "samples_total": total,
                    "samples_with_lifetime": 0,
                    "subhour_coverage": round(subhour_coverage, 4),
                    "status": "NO_CLOSURES_DETECTED",
                    "close_rate_15m": 0.0,
                    "close_rate_30m": 0.0,
                    "close_rate_1h": 0.0,
                    "close_rate_6h": 0.0,
                    "close_rate_24h": 0.0,
                    "median_lifetime_hours": None,
                    "avg_lifetime_hours": None,
                }
            )
            continue

        closed = by_type_closed_by_horizon[t]
        rows_out.append(
            {
                "signal_type": t,
                "samples_total": total,
                "samples_with_lifetime": len(lifetimes),
                "subhour_coverage": round(subhour_coverage, 4),
                "status": "OK",
                "close_rate_15m": round(closed["15m"] / total, 4),
                "close_rate_30m": round(closed["30m"] / total, 4),
                "close_rate_1h": round(closed["1h"] / total, 4),
                "close_rate_6h": round(closed["6h"] / total, 4),
                "close_rate_24h": round(closed["24h"] / total, 4),
                "median_lifetime_hours": round(median(lifetimes), 4),
                "avg_lifetime_hours": round(sum(lifetimes) / len(lifetimes), 4),
            }
        )

    rows_out.sort(key=lambda x: (str(x.get("status")), float(x.get("avg_lifetime_hours") or 1e9)))
    return {
        "period_days": days,
        "signal_type_filter": st.value if st else None,
        "close_ratio_threshold": close_ratio_threshold,
        "min_initial_divergence": min_initial_divergence,
        "min_samples": min_samples,
        "include_subhour": include_subhour,
        "subhour_grace_minutes": subhour_grace_minutes,
        "assumption": (
            "Proxy lifetime: divergence considered closed when |p_h - related_market_probability| "
            "shrinks by close_ratio_threshold relative to initial gap. "
            "15m/30m points are estimated from nearest market snapshots when available."
        ),
        "rows": rows_out,
    }


def extract_signal_lifetime_metrics(report: dict[str, Any]) -> dict[str, float]:
    rows = list(report.get("rows") or [])
    ok = [r for r in rows if r.get("status") == "OK"]
    if not ok:
        return {
            "lifetime_types_ok": 0.0,
            "lifetime_avg_close_rate_15m": 0.0,
            "lifetime_avg_close_rate_30m": 0.0,
            "lifetime_avg_close_rate_1h": 0.0,
            "lifetime_avg_close_rate_6h": 0.0,
            "lifetime_avg_close_rate_24h": 0.0,
            "lifetime_avg_median_hours": 0.0,
        }
    return {
        "lifetime_types_ok": float(len(ok)),
        "lifetime_avg_close_rate_15m": round(sum(float(r.get("close_rate_15m") or 0.0) for r in ok) / len(ok), 6),
        "lifetime_avg_close_rate_30m": round(sum(float(r.get("close_rate_30m") or 0.0) for r in ok) / len(ok), 6),
        "lifetime_avg_close_rate_1h": round(sum(float(r.get("close_rate_1h") or 0.0) for r in ok) / len(ok), 6),
        "lifetime_avg_close_rate_6h": round(sum(float(r.get("close_rate_6h") or 0.0) for r in ok) / len(ok), 6),
        "lifetime_avg_close_rate_24h": round(sum(float(r.get("close_rate_24h") or 0.0) for r in ok) / len(ok), 6),
        "lifetime_avg_median_hours": round(
            sum(float(r.get("median_lifetime_hours") or 0.0) for r in ok) / len(ok), 6
        ),
    }
