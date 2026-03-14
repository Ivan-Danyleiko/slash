from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product
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


def _extract_return_pct(row: SignalHistory, horizon: str) -> float | None:
    field_name = _HORIZON_TO_FIELD[_normalize_horizon(horizon)]
    exit_prob = getattr(row, field_name)
    if row.probability_at_signal is None or exit_prob is None:
        return None
    return float(exit_prob) - float(row.probability_at_signal)


def _evaluate_decision(
    *,
    avg_return: float,
    hit_rate: float,
    sharpe_like: float,
    risk_of_ruin: float,
    min_labeled_returns: int,
    returns_labeled: int,
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
        return ("MODIFY", "Positive EV but below KEEP criteria")
    return ("REMOVE", "Underperforming EV")


def _rank(decision: str) -> int:
    if decision == "KEEP":
        return 3
    if decision == "MODIFY":
        return 2
    if decision == "REMOVE":
        return 1
    return 0


def build_signal_type_optimization_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    signal_type: str = SignalType.DIVERGENCE.value,
    source_tags: list[str] | None = None,
    divergence_thresholds: list[float] | None = None,
    liquidity_thresholds: list[float] | None = None,
    volume_thresholds: list[float] | None = None,
    min_labeled_returns: int = 30,
    keep_ev_min: float = 0.01,
    keep_hit_rate_min: float = 0.52,
    keep_sharpe_like_min: float = 0.5,
    keep_risk_of_ruin_max: float = 0.10,
    modify_ev_min: float = 0.005,
    monte_carlo_sims: int = 500,
    monte_carlo_trades: int = 100,
    monte_carlo_position_size_usd: float = 100.0,
    max_candidates: int = 25,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    horizon = _normalize_horizon(horizon)
    min_labeled_returns = max(1, min(int(min_labeled_returns), 100000))
    max_candidates = max(1, min(int(max_candidates), 200))

    st = _parse_signal_type(signal_type)
    if st is None:
        return {"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]}

    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        db.scalars(
            select(SignalHistory).where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.signal_type == st,
            )
        )
    )
    if not rows:
        return {
            "period_days": days,
            "horizon": horizon,
            "signal_type": st.value,
            "decision": "INSUFFICIENT_DATA",
            "problem_summary": {"no_rows": True},
            "best_candidate": None,
            "candidates": [],
        }

    divergence_thresholds = sorted(set(divergence_thresholds or [0.0, 0.03, 0.05, 0.08, 0.10, 0.15]))
    liquidity_thresholds = sorted(set(liquidity_thresholds or [0.0, 0.1, 0.25, 0.5]))
    volume_thresholds = sorted(set(volume_thresholds or [0.0, 50.0, 100.0, 250.0, 500.0]))
    tags = source_tags or ["all"]
    tags = [t.strip() for t in tags if t and t.strip()]
    if not tags:
        tags = ["all"]

    rows_with_horizon = sum(1 for row in rows if _extract_return_pct(row, horizon) is not None)
    counters = {
        "no_rows_after_filters": 0,
        "insufficient_labeled": 0,
        "negative_ev": 0,
        "low_hit_rate": 0,
        "high_risk_of_ruin": 0,
    }

    candidates: list[dict[str, Any]] = []
    for src_tag, min_div, min_liq, min_vol in product(tags, divergence_thresholds, liquidity_thresholds, volume_thresholds):
        filtered: list[SignalHistory] = []
        for row in rows:
            if src_tag != "all" and (row.source_tag or "") != src_tag:
                continue
            if float(min_div) > 0.0:
                if row.divergence is None or float(row.divergence) < float(min_div):
                    continue
            if float(min_liq) > 0.0:
                if row.liquidity is None or float(row.liquidity) < float(min_liq):
                    continue
            if float(min_vol) > 0.0:
                if row.volume_24h is None or float(row.volume_24h) < float(min_vol):
                    continue
            filtered.append(row)

        returns = [ret for ret in (_extract_return_pct(r, horizon) for r in filtered) if ret is not None]
        if not filtered:
            counters["no_rows_after_filters"] += 1
            continue

        avg_return = (sum(returns) / len(returns)) if returns else 0.0
        hit_rate = (sum(1 for x in returns if x > 0) / len(returns)) if returns else 0.0
        sharpe_like = 0.0
        if len(returns) > 1:
            std = pstdev(returns)
            if std > 0:
                sharpe_like = mean(returns) / std
        mc = _build_monte_carlo_from_returns(
            returns,
            n_sims=monte_carlo_sims,
            trades_per_sim=monte_carlo_trades,
            position_size_usd=monte_carlo_position_size_usd,
        )
        risk_of_ruin = float(mc.get("risk_of_ruin") or 1.0)
        decision, reason = _evaluate_decision(
            avg_return=avg_return,
            hit_rate=hit_rate,
            sharpe_like=sharpe_like,
            risk_of_ruin=risk_of_ruin,
            min_labeled_returns=min_labeled_returns,
            returns_labeled=len(returns),
            keep_ev_min=keep_ev_min,
            keep_hit_rate_min=keep_hit_rate_min,
            keep_sharpe_like_min=keep_sharpe_like_min,
            keep_risk_of_ruin_max=keep_risk_of_ruin_max,
            modify_ev_min=modify_ev_min,
        )
        if len(returns) < min_labeled_returns:
            counters["insufficient_labeled"] += 1
        elif avg_return < modify_ev_min:
            counters["negative_ev"] += 1
        if len(returns) >= min_labeled_returns and hit_rate < keep_hit_rate_min:
            counters["low_hit_rate"] += 1
        if len(returns) >= min_labeled_returns and risk_of_ruin > keep_risk_of_ruin_max:
            counters["high_risk_of_ruin"] += 1

        candidates.append(
            {
                "source_tag": src_tag,
                "min_divergence": round(float(min_div), 4),
                "min_liquidity": round(float(min_liq), 4),
                "min_volume_24h": round(float(min_vol), 4),
                "sample_size": len(filtered),
                "returns_labeled": len(returns),
                "avg_return": round(avg_return, 6),
                "hit_rate": round(hit_rate, 4),
                "sharpe_like": round(sharpe_like, 6),
                "risk_of_ruin": round(risk_of_ruin, 6),
                "decision": decision,
                "reason": reason,
            }
        )

    candidates.sort(
        key=lambda r: (
            _rank(str(r.get("decision") or "")),
            float(r.get("avg_return") or 0.0),
            float(r.get("hit_rate") or 0.0),
            int(r.get("returns_labeled") or 0),
        ),
        reverse=True,
    )
    top = candidates[:max_candidates]
    best = top[0] if top else None
    final_decision = str(best.get("decision")) if best else "INSUFFICIENT_DATA"

    problems: list[str] = []
    if not top:
        problems.append("No candidate combinations produced any rows after filters.")
    else:
        if final_decision not in {"KEEP", "MODIFY"}:
            problems.append("No actionable candidate (KEEP/MODIFY) found under current criteria.")
        if counters["insufficient_labeled"] > 0:
            problems.append(
                f"{counters['insufficient_labeled']} combinations failed min_labeled_returns={min_labeled_returns}."
            )
        if counters["negative_ev"] > 0:
            problems.append(f"{counters['negative_ev']} combinations have EV below modify threshold.")
        if counters["low_hit_rate"] > 0:
            problems.append(f"{counters['low_hit_rate']} combinations have hit_rate below KEEP requirement.")
        if counters["high_risk_of_ruin"] > 0:
            problems.append(f"{counters['high_risk_of_ruin']} combinations exceed risk_of_ruin limit.")

    return {
        "period_days": days,
        "horizon": horizon,
        "signal_type": st.value,
        "decision": final_decision,
        "best_candidate": best,
        "problem_summary": {
            "rows_total": len(rows),
            "rows_with_horizon_label": rows_with_horizon,
            **counters,
            "problems": problems,
        },
        "criteria": {
            "min_labeled_returns": min_labeled_returns,
            "keep_ev_min": keep_ev_min,
            "keep_hit_rate_min": keep_hit_rate_min,
            "keep_sharpe_like_min": keep_sharpe_like_min,
            "keep_risk_of_ruin_max": keep_risk_of_ruin_max,
            "modify_ev_min": modify_ev_min,
        },
        "grid": {
            "source_tags": tags,
            "divergence_thresholds": divergence_thresholds,
            "liquidity_thresholds": liquidity_thresholds,
            "volume_thresholds": volume_thresholds,
            "candidates_total": len(candidates),
            "candidates_returned": len(top),
        },
        "candidates": top,
    }


def extract_signal_type_optimization_metrics(report: dict[str, Any]) -> dict[str, float]:
    best = report.get("best_candidate") or {}
    rank = _rank(str(report.get("decision") or ""))
    return {
        "optimization_decision_rank": float(rank),
        "optimization_best_avg_return": float(best.get("avg_return") or 0.0),
        "optimization_best_hit_rate": float(best.get("hit_rate") or 0.0),
        "optimization_best_returns_labeled": float(best.get("returns_labeled") or 0.0),
        "optimization_candidates_total": float((report.get("grid") or {}).get("candidates_total") or 0.0),
    }
