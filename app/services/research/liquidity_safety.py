from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import SignalHistory


def _parse_signal_type(signal_type: str | None) -> SignalType | None:
    if not signal_type:
        return None
    raw = signal_type.strip().upper()
    try:
        return SignalType(raw)
    except ValueError:
        return None


def _estimate_slippage_from_volume(volume_24h: float | None, trade_size_usd: float) -> float:
    volume = max(0.0, float(volume_24h or 0.0))
    if volume <= 0:
        return 0.05
    trade_pct = trade_size_usd / max(volume, 1.0)
    if trade_pct < 0.01:
        return 0.001
    if trade_pct < 0.05:
        return 0.005
    if trade_pct < 0.10:
        return 0.015
    return 0.05


def _estimate_capacity_usd(row: SignalHistory) -> float:
    simulated = row.simulated_trade or {}
    raw_capacity = simulated.get("capacity_usd")
    if isinstance(raw_capacity, (int, float)) and raw_capacity >= 0:
        return float(raw_capacity)
    volume = float(row.volume_24h or 0.0)
    liquidity_score = float(row.liquidity or 0.0)
    # Conservative fallback when direct capacity is unavailable.
    return max(0.0, min(volume * 0.05, volume * max(0.01, liquidity_score * 0.1)))


def build_liquidity_safety_report(
    db: Session,
    *,
    days: int = 30,
    signal_type: str | None = None,
    position_sizes: str = "50,100,500",
    max_slippage_pct: float = 0.015,
    min_samples: int = 10,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    max_slippage_pct = max(0.001, min(float(max_slippage_pct), 0.5))
    min_samples = max(1, min(int(min_samples), 10000))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    st = _parse_signal_type(signal_type)
    if signal_type and st is None:
        return {"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]}

    sizes: list[float] = []
    for raw in position_sizes.split(","):
        raw = raw.strip()
        if not raw:
            continue
        value = float(raw)
        if value <= 0:
            continue
        sizes.append(value)
    if not sizes:
        sizes = [50.0, 100.0, 500.0]
    sizes = sorted(set(sizes))

    stmt = select(SignalHistory).where(SignalHistory.timestamp >= cutoff)
    if st is not None:
        stmt = stmt.where(SignalHistory.signal_type == st)
    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.desc())))

    by_type: dict[str, list[SignalHistory]] = {}
    for row in rows:
        by_type.setdefault(row.signal_type.value, []).append(row)

    out_rows: list[dict[str, Any]] = []
    for signal_type_name, type_rows in sorted(by_type.items()):
        if len(type_rows) < min_samples:
            out_rows.append(
                {
                    "signal_type": signal_type_name,
                    "samples_total": len(type_rows),
                    "status": "INSUFFICIENT_DATA",
                    "reason": f"Need >= {min_samples} samples",
                }
            )
            continue
        capacities = [_estimate_capacity_usd(r) for r in type_rows]
        median_capacity = median(capacities) if capacities else 0.0
        avg_capacity = mean(capacities) if capacities else 0.0
        position_stats: list[dict[str, Any]] = []
        for size in sizes:
            executable = 0
            slippages: list[float] = []
            for row in type_rows:
                capacity = _estimate_capacity_usd(row)
                slippage = _estimate_slippage_from_volume(row.volume_24h, size)
                slippages.append(slippage)
                if capacity >= size and slippage <= max_slippage_pct:
                    executable += 1
            coverage = executable / len(type_rows) if type_rows else 0.0
            position_stats.append(
                {
                    "position_size_usd": round(size, 2),
                    "executable_rate": round(coverage, 4),
                    "avg_estimated_slippage": round(mean(slippages) if slippages else 0.0, 6),
                }
            )
        max_safe = 0.0
        for size in sizes:
            row = next((x for x in position_stats if x["position_size_usd"] == round(size, 2)), None)
            if row and row["executable_rate"] >= 0.5:
                max_safe = size
        out_rows.append(
            {
                "signal_type": signal_type_name,
                "samples_total": len(type_rows),
                "status": "OK",
                "max_trade_size_without_excess_slippage_usd": round(max_safe, 2),
                "median_capacity_usd": round(median_capacity, 2),
                "avg_capacity_usd": round(avg_capacity, 2),
                "positions": position_stats,
            }
        )

    out_rows.sort(key=lambda r: float(r.get("max_trade_size_without_excess_slippage_usd") or 0.0), reverse=True)
    return {
        "period_days": days,
        "signal_type_filter": st.value if st else None,
        "position_sizes_usd": [round(x, 2) for x in sizes],
        "max_slippage_pct": max_slippage_pct,
        "min_samples": min_samples,
        "rows": out_rows,
    }


def extract_liquidity_safety_metrics(report: dict[str, Any]) -> dict[str, float]:
    rows = [r for r in list(report.get("rows") or []) if r.get("status") == "OK"]
    if not rows:
        return {
            "liquidity_types_ok": 0.0,
            "liquidity_avg_max_safe_size_usd": 0.0,
            "liquidity_avg_capacity_usd": 0.0,
            "liquidity_avg_exec_rate_100usd": 0.0,
        }
    avg_max_safe = sum(float(r.get("max_trade_size_without_excess_slippage_usd") or 0.0) for r in rows) / len(rows)
    avg_capacity = sum(float(r.get("avg_capacity_usd") or 0.0) for r in rows) / len(rows)
    exec_rates_100 = []
    for row in rows:
        for p in row.get("positions") or []:
            if abs(float(p.get("position_size_usd") or 0.0) - 100.0) < 1e-9:
                exec_rates_100.append(float(p.get("executable_rate") or 0.0))
    return {
        "liquidity_types_ok": float(len(rows)),
        "liquidity_avg_max_safe_size_usd": round(avg_max_safe, 6),
        "liquidity_avg_capacity_usd": round(avg_capacity, 6),
        "liquidity_avg_exec_rate_100usd": round(sum(exec_rates_100) / len(exec_rates_100), 6)
        if exec_rates_100
        else 0.0,
    }
