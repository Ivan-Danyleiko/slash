from __future__ import annotations

from datetime import UTC, datetime, timedelta
import random
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import SignalHistory

_HORIZON_TO_FIELD = {
    "1h": "probability_after_1h",
    "6h": "probability_after_6h",
    "24h": "probability_after_24h",
}


def _normalize_horizon(horizon: str) -> str:
    h = (horizon or "").strip().lower()
    return h if h in _HORIZON_TO_FIELD else "6h"


def _parse_signal_type(signal_type: str | None) -> SignalType | None:
    if not signal_type:
        return None
    try:
        return SignalType(signal_type.strip().upper())
    except ValueError:
        return None


def _extract_return(row: SignalHistory, *, horizon: str) -> float | None:
    """Return direction-aware probability change for the given horizon.

    For YES signals (or undirected): return p1 - p0  (positive = price rose = win).
    For NO signals: return p0 - p1  (positive = price fell = win).
    This makes the walkforward metric measure actual signal profitability rather than
    raw probability change, which would wrongly count NO-signal wins as losses.
    """
    field = _HORIZON_TO_FIELD[horizon]
    p0 = row.probability_at_signal
    p1 = getattr(row, field)
    if p0 is None or p1 is None:
        return None
    raw = float(p1) - float(p0)
    direction = str(getattr(row, "signal_direction") or "").strip().upper()
    if direction == "NO":
        return -raw  # invert: for NO signals, price drop is a win
    return raw


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _bootstrap_ci(values: list[float], *, n_sims: int = 500, seed: int = 42) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means: list[float] = []
    n = len(values)
    for _ in range(max(100, min(int(n_sims), 5000))):
        sample = [values[rng.randrange(0, n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return (float(lo), float(hi))


def _window_metrics(returns: list[float], *, min_samples_per_window: int, bootstrap_sims: int) -> dict[str, Any]:
    n = len(returns)
    if n == 0:
        return {
            "n": 0,
            "avg_return": 0.0,
            "hit_rate": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "status": "NO_DATA",
            "low_confidence": True,
        }
    hits = sum(1 for r in returns if r > 0)
    avg_ret = sum(returns) / n
    ci_low, ci_high = _bootstrap_ci(returns, n_sims=bootstrap_sims)
    low_conf = n < min_samples_per_window
    return {
        "n": n,
        "avg_return": round(avg_ret, 6),
        "hit_rate": round(hits / n, 6),
        "ci_low": round(ci_low, 6),
        "ci_high": round(ci_high, 6),
        "status": ("LOW_CONFIDENCE" if low_conf else "OK"),
        "low_confidence": low_conf,
    }


def build_walkforward_report(
    db: Session,
    *,
    days: int = 90,
    horizon: str = "6h",
    signal_type: str | None = None,
    train_days: int = 30,
    test_days: int = 14,
    step_days: int = 14,
    embargo_hours: int = 24,
    min_samples_per_window: int = 100,
    bootstrap_sims: int = 500,
) -> dict[str, Any]:
    days = max(14, min(int(days), 365))
    horizon_key = _normalize_horizon(horizon)
    train_days = max(1, min(int(train_days), 180))
    test_days = max(1, min(int(test_days), 90))
    step_days = max(1, min(int(step_days), 90))
    embargo_hours = max(0, min(int(embargo_hours), 24 * 7))
    min_samples_per_window = max(10, min(int(min_samples_per_window), 100000))

    cutoff = datetime.now(UTC) - timedelta(days=days)
    parsed = _parse_signal_type(signal_type)
    if signal_type and parsed is None:
        return {"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]}

    stmt = select(SignalHistory).where(SignalHistory.timestamp >= cutoff)
    if parsed is not None:
        stmt = stmt.where(SignalHistory.signal_type == parsed)
    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.asc())))

    by_type: dict[str, list[tuple[datetime, float]]] = {}
    for row in rows:
        ret = _extract_return(row, horizon=horizon_key)
        if ret is None:
            continue
        ts = _as_utc(row.timestamp)
        if ts is None:
            continue
        by_type.setdefault(row.signal_type.value, []).append((ts, ret))

    report_rows: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for st, series in sorted(by_type.items()):
        windows: list[dict[str, Any]] = []
        window_start = cutoff + timedelta(days=train_days) + timedelta(hours=embargo_hours)
        latest_test_start = now - timedelta(days=test_days)
        while window_start <= latest_test_start:
            train_end = window_start - timedelta(hours=embargo_hours)
            train_start = train_end - timedelta(days=train_days)
            test_start = window_start
            test_end = test_start + timedelta(days=test_days)

            train_rets = [ret for ts, ret in series if train_start <= ts < train_end]
            test_rets = [ret for ts, ret in series if test_start <= ts < test_end]

            train_m = _window_metrics(
                train_rets,
                min_samples_per_window=min_samples_per_window,
                bootstrap_sims=bootstrap_sims,
            )
            test_m = _window_metrics(
                test_rets,
                min_samples_per_window=min_samples_per_window,
                bootstrap_sims=bootstrap_sims,
            )
            windows.append(
                {
                    "train_start": train_start.isoformat(),
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "train": train_m,
                    "test": test_m,
                }
            )
            window_start += timedelta(days=step_days)

        low_conf = any(bool((w.get("test") or {}).get("low_confidence")) for w in windows)
        test_avgs = [float((w.get("test") or {}).get("avg_return") or 0.0) for w in windows if (w.get("test") or {}).get("n")]
        test_hits = [float((w.get("test") or {}).get("hit_rate") or 0.0) for w in windows if (w.get("test") or {}).get("n")]
        report_rows.append(
            {
                "signal_type": st,
                "windows": windows,
                "windows_count": len(windows),
                "low_confidence": low_conf,
                "avg_test_return": round(mean(test_avgs), 6) if test_avgs else 0.0,
                "avg_test_hit_rate": round(mean(test_hits), 6) if test_hits else 0.0,
            }
        )

    return {
        "period_days": days,
        "horizon": horizon_key,
        "signal_type_filter": parsed.value if parsed else None,
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days,
        "embargo_hours": embargo_hours,
        "min_samples_per_window": min_samples_per_window,
        "assumption": "walk-forward with embargo; LOW_CONFIDENCE when test samples below threshold",
        "rows": report_rows,
    }


def extract_walkforward_metrics(report: dict[str, Any]) -> dict[str, float]:
    rows = list(report.get("rows") or [])
    if not rows:
        return {
            "walkforward_types": 0.0,
            "walkforward_low_confidence_types": 0.0,
            "walkforward_avg_test_return": 0.0,
            "walkforward_avg_test_hit_rate": 0.0,
        }
    low_conf = sum(1 for r in rows if bool(r.get("low_confidence")))
    avg_ret = sum(float(r.get("avg_test_return") or 0.0) for r in rows) / len(rows)
    avg_hr = sum(float(r.get("avg_test_hit_rate") or 0.0) for r in rows) / len(rows)
    return {
        "walkforward_types": float(len(rows)),
        "walkforward_low_confidence_types": float(low_conf),
        "walkforward_avg_test_return": round(avg_ret, 6),
        "walkforward_avg_test_hit_rate": round(avg_hr, 6),
    }
