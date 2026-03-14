from datetime import UTC, datetime, timedelta
import random
from statistics import mean, median, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import SignalHistory

STAGE5_RETURN_ASSUMPTION = "naive_long_yes"
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


def _load_labeled_returns(
    db: Session,
    *,
    days: int,
    horizon: str,
    signal_type: SignalType,
    min_divergence: float | None = None,
) -> list[float]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = select(SignalHistory).where(
        SignalHistory.timestamp >= cutoff,
        SignalHistory.signal_type == signal_type,
    )
    if min_divergence is not None:
        stmt = stmt.where(SignalHistory.divergence.is_not(None), SignalHistory.divergence >= float(min_divergence))
    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.desc())))
    returns = [ret for ret in (_extract_return_pct(row, horizon) for row in rows) if ret is not None]
    return returns


def _max_drawdown_pct(capital_curve: list[float]) -> float:
    if not capital_curve:
        return 0.0
    peak = capital_curve[0]
    max_dd = 0.0
    for point in capital_curve:
        peak = max(peak, point)
        if peak <= 0:
            continue
        drawdown = (peak - point) / peak
        max_dd = max(max_dd, drawdown)
    return max_dd


def _build_monte_carlo_from_returns(
    returns: list[float],
    *,
    n_sims: int = 1000,
    trades_per_sim: int = 100,
    initial_capital: float = 1000.0,
    position_size_usd: float = 100.0,
    ruin_drawdown_threshold: float = 0.5,
    seed: int = 42,
) -> dict:
    n_sims = max(1, min(int(n_sims), 20000))
    trades_per_sim = max(1, min(int(trades_per_sim), 1000))
    initial_capital = max(1.0, float(initial_capital))
    position_size_usd = max(1.0, float(position_size_usd))
    ruin_drawdown_threshold = min(0.99, max(0.05, float(ruin_drawdown_threshold)))

    if not returns:
        return {
            "n_sims": n_sims,
            "trades_per_sim": trades_per_sim,
            "initial_capital": initial_capital,
            "position_size_usd": position_size_usd,
            "risk_of_ruin": 1.0,
            "expected_return_pct": 0.0,
            "variance": 0.0,
            "max_drawdown_mean": 1.0,
            "p10_return_pct": 0.0,
            "p50_return_pct": 0.0,
            "p90_return_pct": 0.0,
            "assumption": "bootstrap_from_observed_returns",
        }

    rng = random.Random(seed)
    terminal_returns: list[float] = []
    drawdowns: list[float] = []
    ruin_count = 0

    ruin_capital = initial_capital * (1.0 - ruin_drawdown_threshold)
    for _ in range(n_sims):
        capital = initial_capital
        curve = [capital]
        for _ in range(trades_per_sim):
            ret = returns[rng.randrange(0, len(returns))]
            capital += ret * position_size_usd
            curve.append(capital)
        dd = _max_drawdown_pct(curve)
        drawdowns.append(dd)
        if min(curve) <= ruin_capital:
            ruin_count += 1
        terminal_returns.append((capital - initial_capital) / initial_capital)

    sorted_returns = sorted(terminal_returns)

    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        idx = int(round((len(values) - 1) * q))
        idx = max(0, min(len(values) - 1, idx))
        return values[idx]

    return {
        "n_sims": n_sims,
        "trades_per_sim": trades_per_sim,
        "initial_capital": round(initial_capital, 2),
        "position_size_usd": round(position_size_usd, 2),
        "risk_of_ruin": round(ruin_count / n_sims, 6),
        "expected_return_pct": round(mean(terminal_returns), 6),
        "variance": round(pstdev(terminal_returns) ** 2, 8),
        "max_drawdown_mean": round(mean(drawdowns), 6) if drawdowns else 0.0,
        "p10_return_pct": round(_percentile(sorted_returns, 0.10), 6),
        "p50_return_pct": round(_percentile(sorted_returns, 0.50), 6),
        "p90_return_pct": round(_percentile(sorted_returns, 0.90), 6),
        "assumption": "bootstrap_from_observed_returns",
    }


def build_signal_history_dataset(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    signal_type: str | None = None,
    min_divergence: float | None = None,
    limit: int = 1000,
) -> dict:
    horizon = _normalize_horizon(horizon)
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 10000))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    stmt = select(SignalHistory).where(SignalHistory.timestamp >= cutoff)
    st = _parse_signal_type(signal_type)
    if signal_type and st is None:
        return {
            "error": f"unsupported signal_type '{signal_type}'",
            "supported": [x.value for x in SignalType],
        }
    if st is not None:
        stmt = stmt.where(SignalHistory.signal_type == st)
    if min_divergence is not None:
        stmt = stmt.where(SignalHistory.divergence.is_not(None), SignalHistory.divergence >= float(min_divergence))

    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.desc()).limit(limit)))

    dataset: list[dict] = []
    returns: list[float] = []
    hits = 0
    for row in rows:
        ret = _extract_return_pct(row, horizon)
        if ret is not None:
            returns.append(ret)
            if ret > 0:
                hits += 1
        dataset.append(
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "signal_type": row.signal_type.value,
                "platform": row.platform,
                "market_id": row.market_id,
                "related_market_id": row.related_market_id,
                "probability_at_signal": row.probability_at_signal,
                "divergence": row.divergence,
                "liquidity": row.liquidity,
                "volume_24h": row.volume_24h,
                "horizon": horizon,
                "probability_at_horizon": getattr(row, _HORIZON_TO_FIELD[horizon]),
                "return_pct": round(ret, 6) if ret is not None else None,
                "is_hit": (ret > 0) if ret is not None else None,
                "resolved_success": row.resolved_success,
            }
        )

    metrics = {
        "rows": len(dataset),
        "returns_labeled": len(returns),
        "hit_rate": round((hits / len(returns)), 4) if returns else 0.0,
        "avg_return": round(sum(returns) / len(returns), 6) if returns else 0.0,
        "median_return": round(median(returns), 6) if returns else 0.0,
    }
    return {
        "period_days": days,
        "horizon": horizon,
        "return_assumption": STAGE5_RETURN_ASSUMPTION,
        "filters": {
            "signal_type": st.value if st else None,
            "min_divergence": min_divergence,
            "limit": limit,
        },
        "metrics": metrics,
        "rows": dataset,
    }


def build_threshold_summary(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    thresholds: list[float] | None = None,
    signal_type: str = SignalType.DIVERGENCE.value,
) -> dict:
    horizon = _normalize_horizon(horizon)
    days = max(1, min(int(days), 365))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    values = thresholds or [0.03, 0.05, 0.08, 0.10, 0.15]

    st = _parse_signal_type(signal_type)
    if st is None:
        return {
            "error": f"unsupported signal_type '{signal_type}'",
            "supported": [x.value for x in SignalType],
        }

    all_rows = list(
        db.scalars(
            select(SignalHistory)
            .where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.signal_type == st,
                SignalHistory.divergence.is_not(None),
            )
            .order_by(SignalHistory.timestamp.desc())
        )
    )

    summary: list[dict] = []
    for threshold in sorted(values):
        threshold_rows = [r for r in all_rows if (r.divergence or 0.0) >= threshold]
        returns = [
            ret
            for ret in (_extract_return_pct(r, horizon) for r in threshold_rows)
            if ret is not None
        ]
        hits = sum(1 for r in returns if r > 0)
        summary.append(
            {
                "threshold": round(float(threshold), 4),
                "sample_size": len(threshold_rows),
                "returns_labeled": len(returns),
                "hit_rate": round((hits / len(returns)), 4) if returns else 0.0,
                "avg_return": round(sum(returns) / len(returns), 6) if returns else 0.0,
                "median_return": round(median(returns), 6) if returns else 0.0,
            }
        )

    return {
        "period_days": days,
        "horizon": horizon,
        "signal_type": st.value,
        "return_assumption": STAGE5_RETURN_ASSUMPTION,
        "rows_total_signal_type": len(all_rows),
        "threshold_summary": summary,
    }


def build_divergence_decision(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    thresholds: list[float] | None = None,
    min_labeled_returns: int = 30,
    keep_ev_min: float = 0.01,
    keep_hit_rate_min: float = 0.52,
    keep_sharpe_like_min: float = 0.5,
    keep_risk_of_ruin_max: float = 0.10,
    modify_ev_min: float = 0.005,
    monte_carlo_sims: int = 1000,
    monte_carlo_trades: int = 100,
    monte_carlo_position_size_usd: float = 100.0,
) -> dict:
    summary = build_threshold_summary(
        db,
        days=days,
        horizon=horizon,
        thresholds=thresholds,
        signal_type=SignalType.DIVERGENCE.value,
    )
    if "error" in summary:
        return summary

    rows = list(summary["threshold_summary"])
    eligible = [r for r in rows if int(r["returns_labeled"]) >= max(1, min_labeled_returns)]
    if not eligible:
        return {
            **summary,
            "decision": "INSUFFICIENT_DATA",
            "recommended_threshold": None,
            "decision_reason": (
                f"No threshold has >= {max(1, min_labeled_returns)} labeled returns "
                f"for horizon '{summary['horizon']}'."
            ),
        }

    best = max(
        eligible,
        key=lambda r: (float(r["avg_return"]), float(r["hit_rate"]), int(r["returns_labeled"])),
    )
    ev = float(best["avg_return"])
    hit_rate = float(best["hit_rate"])
    threshold = float(best["threshold"])
    threshold_returns = _load_labeled_returns(
        db,
        days=days,
        horizon=horizon,
        signal_type=SignalType.DIVERGENCE,
        min_divergence=threshold,
    )
    sharpe_like = 0.0
    if len(threshold_returns) > 1:
        std = pstdev(threshold_returns)
        if std > 0:
            sharpe_like = mean(threshold_returns) / std
    monte_carlo = _build_monte_carlo_from_returns(
        threshold_returns,
        n_sims=monte_carlo_sims,
        trades_per_sim=monte_carlo_trades,
        position_size_usd=monte_carlo_position_size_usd,
    )
    risk_of_ruin = float(monte_carlo["risk_of_ruin"])

    if (
        ev >= keep_ev_min
        and hit_rate >= keep_hit_rate_min
        and sharpe_like >= keep_sharpe_like_min
        and risk_of_ruin <= keep_risk_of_ruin_max
    ):
        decision = "KEEP"
        reason = (
            f"Best threshold={threshold:.3f} meets KEEP criteria: "
            f"avg_return={ev:.4f}, hit_rate={hit_rate:.3f}, "
            f"sharpe_like={sharpe_like:.3f}, risk_of_ruin={risk_of_ruin:.3f}."
        )
    elif ev >= modify_ev_min:
        decision = "MODIFY"
        reason = (
            f"Best threshold={threshold:.3f} has positive EV but below KEEP bar: "
            f"avg_return={ev:.4f}, hit_rate={hit_rate:.3f}, "
            f"sharpe_like={sharpe_like:.3f}, risk_of_ruin={risk_of_ruin:.3f}."
        )
    else:
        decision = "REMOVE"
        reason = (
            f"Best threshold={threshold:.3f} underperforms: "
            f"avg_return={ev:.4f}, hit_rate={hit_rate:.3f}, "
            f"sharpe_like={sharpe_like:.3f}, risk_of_ruin={risk_of_ruin:.3f}."
        )

    return {
        **summary,
        "decision": decision,
        "recommended_threshold": round(threshold, 4),
        "decision_reason": reason,
        "criteria": {
            "min_labeled_returns": max(1, min_labeled_returns),
            "keep_ev_min": keep_ev_min,
            "keep_hit_rate_min": keep_hit_rate_min,
            "keep_sharpe_like_min": keep_sharpe_like_min,
            "keep_risk_of_ruin_max": keep_risk_of_ruin_max,
            "modify_ev_min": modify_ev_min,
        },
        "risk_metrics": {
            "sharpe_like": round(sharpe_like, 6),
            "risk_of_ruin": round(risk_of_ruin, 6),
            "monte_carlo": monte_carlo,
        },
        "best_threshold_metrics": best,
    }


def build_monte_carlo_summary(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    signal_type: str = SignalType.DIVERGENCE.value,
    min_divergence: float | None = None,
    n_sims: int = 1000,
    trades_per_sim: int = 100,
    initial_capital: float = 1000.0,
    position_size_usd: float = 100.0,
    ruin_drawdown_threshold: float = 0.5,
    seed: int = 42,
) -> dict:
    horizon = _normalize_horizon(horizon)
    days = max(1, min(int(days), 365))
    st = _parse_signal_type(signal_type)
    if st is None:
        return {
            "error": f"unsupported signal_type '{signal_type}'",
            "supported": [x.value for x in SignalType],
        }
    returns = _load_labeled_returns(
        db,
        days=days,
        horizon=horizon,
        signal_type=st,
        min_divergence=min_divergence,
    )
    monte_carlo = _build_monte_carlo_from_returns(
        returns,
        n_sims=n_sims,
        trades_per_sim=trades_per_sim,
        initial_capital=initial_capital,
        position_size_usd=position_size_usd,
        ruin_drawdown_threshold=ruin_drawdown_threshold,
        seed=seed,
    )
    hit_rate = (sum(1 for x in returns if x > 0) / len(returns)) if returns else 0.0
    avg_return = (sum(returns) / len(returns)) if returns else 0.0
    median_return = median(returns) if returns else 0.0
    sharpe_like = 0.0
    if len(returns) > 1:
        std = pstdev(returns)
        if std > 0:
            sharpe_like = mean(returns) / std
    return {
        "period_days": days,
        "horizon": horizon,
        "signal_type": st.value,
        "min_divergence": min_divergence,
        "return_assumption": STAGE5_RETURN_ASSUMPTION,
        "observed": {
            "returns_labeled": len(returns),
            "hit_rate": round(hit_rate, 6),
            "avg_return": round(avg_return, 6),
            "median_return": round(median_return, 6),
            "sharpe_like": round(sharpe_like, 6),
        },
        "monte_carlo": monte_carlo,
    }


def build_result_tables(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_samples: int = 10,
) -> dict:
    horizon = _normalize_horizon(horizon)
    days = max(1, min(int(days), 365))
    min_samples = max(1, min(int(min_samples), 10000))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    all_rows = list(
        db.scalars(
            select(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff)
            .order_by(SignalHistory.timestamp.desc())
        )
    )
    per_type: list[dict] = []
    for signal_type in SignalType:
        rows = [r for r in all_rows if r.signal_type == signal_type]
        returns = [ret for ret in (_extract_return_pct(r, horizon) for r in rows) if ret is not None]
        if len(returns) < min_samples:
            continue
        hit_rate = sum(1 for x in returns if x > 0) / len(returns)
        avg_return = sum(returns) / len(returns)
        med_return = median(returns)
        std = pstdev(returns) if len(returns) > 1 else 0.0
        # Confidence is higher with larger sample size and lower dispersion.
        confidence = min(1.0, len(returns) / 100.0) * max(0.0, 1.0 - min(1.0, std / 0.10))
        per_type.append(
            {
                "signal_type": signal_type.value,
                "sample_size": len(rows),
                "returns_labeled": len(returns),
                "hit_rate": round(hit_rate, 4),
                "avg_return": round(avg_return, 6),
                "median_return": round(med_return, 6),
                "confidence": round(confidence, 4),
            }
        )

    best_signals = sorted(
        [x for x in per_type if x["avg_return"] > 0],
        key=lambda x: (x["avg_return"], x["hit_rate"], x["returns_labeled"]),
        reverse=True,
    )
    best_table = [
        {
            "signal_type": row["signal_type"],
            "threshold": None,
            "avg_return": row["avg_return"],
            "confidence": row["confidence"],
        }
        for row in best_signals
    ]

    bad_signals = []
    for row in sorted(per_type, key=lambda x: (x["avg_return"], x["hit_rate"])):
        reason_parts: list[str] = []
        if row["avg_return"] <= 0:
            reason_parts.append(f"non_positive_ev({row['avg_return']:.4f})")
        if row["hit_rate"] < 0.5:
            reason_parts.append(f"low_hit_rate({row['hit_rate']:.3f})")
        if reason_parts:
            bad_signals.append(
                {
                    "signal_type": row["signal_type"],
                    "reason": ", ".join(reason_parts),
                }
            )

    return {
        "period_days": days,
        "horizon": horizon,
        "min_samples": min_samples,
        "types_evaluated": len(per_type),
        "table_best_signals": best_table,
        "table_bad_signals": bad_signals,
        "by_signal_type": per_type,
    }
