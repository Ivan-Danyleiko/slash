from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.models import Signal, SignalHistory
from app.services.signals.ranking import rank_score

_HORIZON_TO_FIELD = {
    "1h": "probability_after_1h",
    "6h": "probability_after_6h",
    "24h": "probability_after_24h",
    "resolution": "resolved_probability",
}


def _normalize_horizon(horizon: str) -> str:
    key = (horizon or "").strip().lower()
    return key if key in _HORIZON_TO_FIELD else "6h"


def _signal_components(signal: Signal) -> dict[str, float]:
    settings = get_settings()
    payload = signal.score_breakdown_json or {}
    execution = signal.execution_analysis or {}
    edge = float(payload.get("edge") or 0.0)
    liquidity = float(payload.get("liquidity") or (signal.liquidity_score or 0.0))
    execution_safety = float(
        payload.get("execution_safety")
        if isinstance(payload.get("execution_safety"), (int, float))
        else execution.get("utility_score")
        or 0.0
    )
    freshness = float(payload.get("freshness") or 0.0)
    confidence = float(payload.get("confidence") or (signal.confidence_score or 0.0))
    risk_penalties = float(payload.get("risk_penalties") or (signal.rules_risk_score or 0.0))
    appendix_c = (
        (settings.signal_rank_weight_edge * edge)
        + (settings.signal_rank_weight_liquidity * liquidity)
        + (settings.signal_rank_weight_execution_safety * execution_safety)
        + (settings.signal_rank_weight_freshness * freshness)
        + (settings.signal_rank_weight_confidence * confidence)
        - risk_penalties
    )
    return {
        "legacy_rank_score": rank_score(signal),
        "appendix_c_score": appendix_c,
        "edge_only": edge,
        "edge_plus_liquidity": (0.7 * edge) + (0.3 * liquidity),
        "edge_plus_liquidity_plus_freshness": (0.55 * edge) + (0.30 * liquidity) + (0.15 * freshness),
        "score_total": float(payload.get("score_total") or 0.0),
    }


def _eval_strategy(sorted_rows: list[dict[str, float]], top_k: int) -> dict[str, float]:
    window = sorted_rows[: max(1, min(top_k, len(sorted_rows)))] if sorted_rows else []
    returns = [float(row["return_pct"]) for row in window]
    if not returns:
        return {
            "window_size": 0,
            "hit_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "total_return": 0.0,
        }
    hits = sum(1 for x in returns if x > 0)
    return {
        "window_size": len(window),
        "hit_rate": round(hits / len(returns), 4),
        "avg_return": round(sum(returns) / len(returns), 6),
        "median_return": round(median(returns), 6),
        "total_return": round(sum(returns), 6),
    }


def build_ranking_research_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    top_k: int = 50,
    min_samples: int = 20,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    top_k = max(1, min(int(top_k), 500))
    min_samples = max(1, min(int(min_samples), 10000))
    horizon = _normalize_horizon(horizon)
    field_name = _HORIZON_TO_FIELD[horizon]
    cutoff = datetime.now(UTC) - timedelta(days=days)

    rows = list(
        db.scalars(
            select(SignalHistory)
            .where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.signal_id.is_not(None),
                getattr(SignalHistory, field_name).is_not(None),
            )
            .order_by(SignalHistory.timestamp.desc())
        )
    )

    dataset: list[dict[str, float]] = []
    for row in rows:
        signal = db.get(Signal, int(row.signal_id or 0))
        if signal is None or row.probability_at_signal is None:
            continue
        prob_after = getattr(row, field_name)
        if prob_after is None:
            continue
        ret = float(prob_after) - float(row.probability_at_signal)
        scores = _signal_components(signal)
        dataset.append(
            {
                "return_pct": ret,
                "legacy_rank_score": scores["legacy_rank_score"],
                "appendix_c_score": scores["appendix_c_score"],
                "score_total": scores["score_total"],
                "edge_only": scores["edge_only"],
                "edge_plus_liquidity": scores["edge_plus_liquidity"],
                "edge_plus_liquidity_plus_freshness": scores["edge_plus_liquidity_plus_freshness"],
            }
        )

    formula_keys = [
        "legacy_rank_score",
        "appendix_c_score",
        "score_total",
        "edge_only",
        "edge_plus_liquidity",
        "edge_plus_liquidity_plus_freshness",
    ]
    formulas: list[dict[str, Any]] = []
    for key in formula_keys:
        ranked = sorted(dataset, key=lambda x: float(x[key]), reverse=True)
        metrics = _eval_strategy(ranked, top_k)
        formulas.append({"formula": key, **metrics})

    formulas = sorted(formulas, key=lambda x: (float(x["avg_return"]), float(x["hit_rate"])), reverse=True)
    best = formulas[0] if formulas else None
    sufficient = len(dataset) >= min_samples
    return {
        "period_days": days,
        "horizon": horizon,
        "top_k": top_k,
        "min_samples": min_samples,
        "samples_total": len(dataset),
        "sufficient_samples": sufficient,
        "formulas": formulas,
        "best_formula": best["formula"] if best else None,
        "best_formula_metrics": best,
        "notes": None if sufficient else f"Insufficient samples (< {min_samples}) for reliable ranking comparison.",
    }


def extract_ranking_research_metrics(report: dict[str, Any]) -> dict[str, float]:
    best = report.get("best_formula_metrics") or {}
    return {
        "ranking_samples_total": float(report.get("samples_total") or 0.0),
        "ranking_sufficient_samples": 1.0 if bool(report.get("sufficient_samples")) else 0.0,
        "ranking_best_avg_return": float(best.get("avg_return") or 0.0),
        "ranking_best_hit_rate": float(best.get("hit_rate") or 0.0),
    }
