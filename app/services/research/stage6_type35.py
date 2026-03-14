from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import SignalHistory
from app.services.research.stage5 import _build_monte_carlo_from_returns

_HORIZON_TO_FIELD = {
    "1h": "probability_after_1h",
    "6h": "probability_after_6h",
    "24h": "probability_after_24h",
}


def _normalize_horizon(horizon: str) -> str:
    key = (horizon or "").strip().lower()
    return key if key in _HORIZON_TO_FIELD else "6h"


def _extract_return(row: SignalHistory, horizon: str) -> float | None:
    field = _HORIZON_TO_FIELD[horizon]
    p0 = row.probability_at_signal
    p1 = getattr(row, field)
    if p0 is None or p1 is None:
        return None
    return float(p1) - float(p0)


def _subhour_coverage(rows: list[SignalHistory]) -> float:
    if not rows:
        return 0.0
    covered = 0
    for row in rows:
        payload = row.simulated_trade or {}
        if payload.get("probability_after_15m") is not None or payload.get("probability_after_30m") is not None:
            covered += 1
    return covered / len(rows)


def _decision(
    *,
    avg_return: float,
    hit_rate: float,
    sharpe_like: float,
    risk_of_ruin: float,
    returns_labeled: int,
    min_labeled_returns: int,
    keep_ev_min: float,
    keep_hit_rate_min: float,
    keep_sharpe_like_min: float,
    keep_risk_of_ruin_max: float,
    modify_ev_min: float,
) -> tuple[str, str]:
    if returns_labeled < min_labeled_returns:
        return ("INSUFFICIENT_DATA", f"Need >= {min_labeled_returns} labeled returns")
    if (
        avg_return >= keep_ev_min
        and hit_rate >= keep_hit_rate_min
        and sharpe_like >= keep_sharpe_like_min
        and risk_of_ruin <= keep_risk_of_ruin_max
    ):
        return ("KEEP", "Meets KEEP criteria")
    if avg_return >= modify_ev_min:
        return ("MODIFY", "Positive EV, below KEEP criteria")
    return ("REMOVE", "Underperforming EV")


def _evaluate_type(
    db: Session,
    *,
    signal_type: SignalType,
    days: int,
    horizon: str,
    min_labeled_returns: int,
    keep_ev_min: float,
    keep_hit_rate_min: float,
    keep_sharpe_like_min: float,
    keep_risk_of_ruin_max: float,
    modify_ev_min: float,
    min_subhour_coverage: float,
    architecture_sensitive: bool,
) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        db.scalars(
            select(SignalHistory).where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.signal_type == signal_type,
            )
        )
    )

    if not rows:
        return {
            "signal_type": signal_type.value,
            "decision": "INSUFFICIENT_DATA",
            "reason": "No rows in selected period",
            "rows_total": 0,
            "returns_labeled": 0,
            "subhour_coverage": 0.0,
        }

    coverage = _subhour_coverage(rows)
    if architecture_sensitive and coverage < min_subhour_coverage:
        return {
            "signal_type": signal_type.value,
            "decision": "INSUFFICIENT_ARCHITECTURE",
            "reason": (
                f"subhour_coverage={coverage:.3f} < required={min_subhour_coverage:.3f}; "
                "needs high-frequency collector"
            ),
            "rows_total": len(rows),
            "returns_labeled": 0,
            "subhour_coverage": round(coverage, 6),
        }

    returns = [ret for ret in (_extract_return(r, horizon) for r in rows) if ret is not None]
    avg_return = (sum(returns) / len(returns)) if returns else 0.0
    hit_rate = (sum(1 for x in returns if x > 0) / len(returns)) if returns else 0.0
    std = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe_like = (avg_return / std) if std > 0 else 0.0
    mc = _build_monte_carlo_from_returns(returns, n_sims=500, trades_per_sim=100, position_size_usd=100.0)
    risk_of_ruin = float(mc.get("risk_of_ruin") or 1.0)

    decision, reason = _decision(
        avg_return=avg_return,
        hit_rate=hit_rate,
        sharpe_like=sharpe_like,
        risk_of_ruin=risk_of_ruin,
        returns_labeled=len(returns),
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
    )

    return {
        "signal_type": signal_type.value,
        "decision": decision,
        "reason": reason,
        "rows_total": len(rows),
        "returns_labeled": len(returns),
        "avg_return": round(avg_return, 6),
        "hit_rate": round(hit_rate, 6),
        "sharpe_like": round(sharpe_like, 6),
        "risk_of_ruin": round(risk_of_ruin, 6),
        "subhour_coverage": round(coverage, 6),
    }


def build_stage6_type35_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_labeled_returns: int = 30,
    keep_ev_min: float = 0.01,
    keep_hit_rate_min: float = 0.52,
    keep_sharpe_like_min: float = 0.5,
    keep_risk_of_ruin_max: float = 0.10,
    modify_ev_min: float = 0.005,
    min_subhour_coverage: float = 0.20,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    horizon = _normalize_horizon(horizon)
    min_labeled_returns = max(1, min(int(min_labeled_returns), 100000))

    # Mapping for current project taxonomy.
    # Type 3: Low Liquidity Lag -> LIQUIDITY_RISK
    # Type 5: Timing Shock -> WEIRD_MARKET
    type3 = _evaluate_type(
        db,
        signal_type=SignalType.LIQUIDITY_RISK,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        min_subhour_coverage=min_subhour_coverage,
        architecture_sensitive=True,
    )
    type5 = _evaluate_type(
        db,
        signal_type=SignalType.WEIRD_MARKET,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        min_subhour_coverage=min_subhour_coverage,
        architecture_sensitive=True,
    )

    rows = [
        {"type_label": "TYPE_3_LOW_LIQUIDITY_LAG", **type3},
        {"type_label": "TYPE_5_TIMING_SHOCK", **type5},
    ]

    decision_counts: dict[str, int] = {}
    for row in rows:
        d = str(row.get("decision") or "")
        decision_counts[d] = decision_counts.get(d, 0) + 1

    return {
        "period_days": days,
        "horizon": horizon,
        "min_labeled_returns": min_labeled_returns,
        "min_subhour_coverage": min_subhour_coverage,
        "rows": rows,
        "decision_counts": decision_counts,
    }


def extract_stage6_type35_metrics(report: dict[str, Any]) -> dict[str, float]:
    counts = report.get("decision_counts") or {}
    rows = list(report.get("rows") or [])
    avg_cov = 0.0
    if rows:
        avg_cov = sum(float(r.get("subhour_coverage") or 0.0) for r in rows) / len(rows)
    return {
        "stage6_type35_keep": float(counts.get("KEEP") or 0.0),
        "stage6_type35_modify": float(counts.get("MODIFY") or 0.0),
        "stage6_type35_remove": float(counts.get("REMOVE") or 0.0),
        "stage6_type35_insufficient_architecture": float(counts.get("INSUFFICIENT_ARCHITECTURE") or 0.0),
        "stage6_type35_insufficient_data": float(counts.get("INSUFFICIENT_DATA") or 0.0),
        "stage6_type35_avg_subhour_coverage": round(avg_cov, 6),
    }
