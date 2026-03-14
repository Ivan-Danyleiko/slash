from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import SignalHistory

_HORIZON_TO_FIELD = {
    "1h": "probability_after_1h",
    "6h": "probability_after_6h",
    "24h": "probability_after_24h",
    "resolution": "resolved_probability",
}


def _normalize_horizon(horizon: str) -> str:
    key = (horizon or "").strip().lower()
    return key if key in _HORIZON_TO_FIELD else "6h"


def _parse_signal_type(signal_type: str | None) -> SignalType | None:
    if not signal_type:
        return None
    raw = signal_type.strip().upper()
    try:
        return SignalType(raw)
    except ValueError:
        return None


def build_platform_comparison_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    signal_type: str | None = None,
    min_samples: int = 10,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    min_samples = max(1, min(int(min_samples), 10000))
    horizon = _normalize_horizon(horizon)
    field_name = _HORIZON_TO_FIELD[horizon]
    cutoff = datetime.now(UTC) - timedelta(days=days)

    st = _parse_signal_type(signal_type)
    if signal_type and st is None:
        return {
            "error": f"unsupported signal_type '{signal_type}'",
            "supported": [x.value for x in SignalType],
        }

    stmt = select(SignalHistory).where(
        SignalHistory.timestamp >= cutoff,
        SignalHistory.probability_at_signal.is_not(None),
        getattr(SignalHistory, field_name).is_not(None),
        SignalHistory.platform.is_not(None),
    )
    if st is not None:
        stmt = stmt.where(SignalHistory.signal_type == st)
    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.desc())))

    grouped: dict[str, list[float]] = {}
    for row in rows:
        platform = str(row.platform or "").strip().upper()
        if not platform:
            continue
        prob_after = getattr(row, field_name)
        if prob_after is None or row.probability_at_signal is None:
            continue
        ret = float(prob_after) - float(row.probability_at_signal)
        grouped.setdefault(platform, []).append(ret)

    out_rows: list[dict[str, Any]] = []
    for platform, returns in grouped.items():
        if len(returns) < min_samples:
            continue
        hits = sum(1 for x in returns if x > 0)
        avg_return = sum(returns) / len(returns)
        med_return = median(returns)
        std = pstdev(returns) if len(returns) > 1 else 0.0
        sharpe_like = (avg_return / std) if std > 0 else 0.0
        out_rows.append(
            {
                "platform": platform,
                "returns_labeled": len(returns),
                "hit_rate": round(hits / len(returns), 4),
                "avg_return": round(avg_return, 6),
                "median_return": round(med_return, 6),
                "sharpe_like": round(sharpe_like, 6),
            }
        )

    out_rows.sort(key=lambda r: (float(r["avg_return"]), float(r["hit_rate"])), reverse=True)
    best = out_rows[0] if out_rows else None
    return {
        "period_days": days,
        "horizon": horizon,
        "signal_type": st.value if st else None,
        "min_samples": min_samples,
        "platforms_total": len(out_rows),
        "best_platform": best["platform"] if best else None,
        "best_platform_metrics": best,
        "rows": out_rows,
    }


def extract_platform_comparison_metrics(report: dict[str, Any]) -> dict[str, float]:
    best = report.get("best_platform_metrics") or {}
    return {
        "platforms_total": float(report.get("platforms_total") or 0.0),
        "platform_best_avg_return": float(best.get("avg_return") or 0.0),
        "platform_best_hit_rate": float(best.get("hit_rate") or 0.0),
        "platform_best_sharpe_like": float(best.get("sharpe_like") or 0.0),
    }
