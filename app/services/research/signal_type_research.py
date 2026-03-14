from __future__ import annotations

from datetime import UTC, datetime, timedelta
import random
from statistics import mean, median, pstdev
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


def _parse_signal_types_csv(signal_types: str | None) -> list[SignalType]:
    if not signal_types:
        return [x for x in SignalType if x != SignalType.WATCHLIST]
    out: list[SignalType] = []
    for raw in signal_types.split(","):
        raw = raw.strip().upper()
        if not raw:
            continue
        out.append(SignalType(raw))
    return out


def _load_returns(db: Session, *, signal_type: SignalType, horizon: str, days: int) -> list[float]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    field_name = _HORIZON_TO_FIELD[horizon]
    rows = list(
        db.scalars(
            select(SignalHistory).where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.signal_type == signal_type,
                SignalHistory.probability_at_signal.is_not(None),
                getattr(SignalHistory, field_name).is_not(None),
            )
        )
    )
    returns = []
    for row in rows:
        exit_prob = getattr(row, field_name)
        if row.probability_at_signal is None or exit_prob is None:
            continue
        returns.append(float(exit_prob) - float(row.probability_at_signal))
    return returns


def _max_drawdown_pct(curve: list[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak <= 0:
            continue
        max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def _monte_carlo(returns: list[float], *, n_sims: int = 500, trades_per_sim: int = 100, seed: int = 42) -> dict:
    if not returns:
        return {"risk_of_ruin": 1.0, "expected_return_pct": 0.0, "max_drawdown_mean": 1.0}
    rng = random.Random(seed)
    ruin_count = 0
    terminal: list[float] = []
    drawdowns: list[float] = []
    for _ in range(max(1, n_sims)):
        capital = 1000.0
        curve = [capital]
        for _ in range(max(1, trades_per_sim)):
            ret = returns[rng.randrange(0, len(returns))]
            capital += ret * 100.0
            curve.append(capital)
        terminal.append((capital - 1000.0) / 1000.0)
        drawdowns.append(_max_drawdown_pct(curve))
        if min(curve) <= 500.0:
            ruin_count += 1
    return {
        "risk_of_ruin": round(ruin_count / max(1, n_sims), 6),
        "expected_return_pct": round(mean(terminal), 6) if terminal else 0.0,
        "max_drawdown_mean": round(mean(drawdowns), 6) if drawdowns else 0.0,
    }


def build_signal_type_research_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    signal_types: str | None = None,
    min_labeled_returns: int = 30,
    keep_ev_min: float = 0.01,
    keep_hit_rate_min: float = 0.52,
    keep_sharpe_like_min: float = 0.5,
    keep_risk_of_ruin_max: float = 0.10,
    modify_ev_min: float = 0.005,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    horizon = _normalize_horizon(horizon)
    min_labeled_returns = max(1, min(int(min_labeled_returns), 100000))

    try:
        selected_types = _parse_signal_types_csv(signal_types)
    except ValueError:
        return {"error": f"unsupported signal type in '{signal_types}'", "supported": [x.value for x in SignalType]}

    rows: list[dict[str, Any]] = []
    for signal_type in selected_types:
        returns = _load_returns(db, signal_type=signal_type, horizon=horizon, days=days)
        if len(returns) < min_labeled_returns:
            rows.append(
                {
                    "signal_type": signal_type.value,
                    "decision": "INSUFFICIENT_DATA",
                    "returns_labeled": len(returns),
                    "avg_return": round(sum(returns) / len(returns), 6) if returns else 0.0,
                    "hit_rate": round(sum(1 for x in returns if x > 0) / len(returns), 4) if returns else 0.0,
                    "sharpe_like": 0.0,
                    "risk_of_ruin": 1.0,
                    "reason": f"Need >= {min_labeled_returns} labeled returns.",
                }
            )
            continue

        avg_return = (sum(returns) / len(returns)) if returns else 0.0
        hit_rate = (sum(1 for x in returns if x > 0) / len(returns)) if returns else 0.0
        std = pstdev(returns) if len(returns) > 1 else 0.0
        sharpe_like = (avg_return / std) if std > 0 else 0.0
        mc = _monte_carlo(returns)
        risk_of_ruin = float(mc["risk_of_ruin"])

        if (
            avg_return >= keep_ev_min
            and hit_rate >= keep_hit_rate_min
            and sharpe_like >= keep_sharpe_like_min
            and risk_of_ruin <= keep_risk_of_ruin_max
        ):
            decision = "KEEP"
            reason = "Meets KEEP thresholds."
        elif avg_return >= modify_ev_min:
            decision = "MODIFY"
            reason = "Positive EV, but below KEEP thresholds."
        else:
            decision = "REMOVE"
            reason = "Underperforming EV."

        rows.append(
            {
                "signal_type": signal_type.value,
                "decision": decision,
                "returns_labeled": len(returns),
                "avg_return": round(avg_return, 6),
                "median_return": round(median(returns), 6),
                "hit_rate": round(hit_rate, 4),
                "sharpe_like": round(sharpe_like, 6),
                "risk_of_ruin": round(risk_of_ruin, 6),
                "reason": reason,
                "criteria_lifetime_note": "Lifetime criterion requires dedicated lifetime tracking and is reported separately.",
            }
        )

    rows.sort(
        key=lambda r: (
            1 if r["decision"] == "KEEP" else (0 if r["decision"] == "MODIFY" else -1),
            float(r.get("avg_return", 0.0)),
        ),
        reverse=True,
    )
    decision_counts: dict[str, int] = {}
    for row in rows:
        decision_counts[row["decision"]] = decision_counts.get(row["decision"], 0) + 1
    return {
        "period_days": days,
        "horizon": horizon,
        "min_labeled_returns": min_labeled_returns,
        "signal_types_requested": [x.value for x in selected_types],
        "decision_counts": decision_counts,
        "rows": rows,
    }


def extract_signal_type_research_metrics(report: dict[str, Any]) -> dict[str, float]:
    counts = report.get("decision_counts") or {}
    rows = list(report.get("rows") or [])
    avg_return_all = 0.0
    if rows:
        avg_return_all = sum(float(r.get("avg_return", 0.0)) for r in rows) / len(rows)
    return {
        "signal_types_keep": float(counts.get("KEEP", 0)),
        "signal_types_modify": float(counts.get("MODIFY", 0)),
        "signal_types_remove": float(counts.get("REMOVE", 0)),
        "signal_types_insufficient": float(counts.get("INSUFFICIENT_DATA", 0)),
        "signal_types_avg_return_mean": round(avg_return_all, 6),
    }
