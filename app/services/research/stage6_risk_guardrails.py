from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import erf, sqrt
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import Signal, SignalHistory


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _one_sided_p_value_mean_lt_zero(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mu = mean(values)
    sigma = pstdev(values)
    if sigma <= 0:
        return 1.0 if mu >= 0 else 0.0
    z = (mu - 0.0) / (sigma / sqrt(n))
    return _normal_cdf(z)


def _daily_ev_pnl(signal_rows: list[Signal], *, nav_usd: float = 10000.0) -> float:
    # Proxy daily PnL from expected EV after costs where available.
    pnl = 0.0
    for row in signal_rows:
        ex = row.execution_analysis or {}
        ev = ex.get("expected_ev_after_costs_pct")
        if not isinstance(ev, (int, float)):
            ev = ex.get("slippage_adjusted_edge")
        if not isinstance(ev, (int, float)):
            continue
        size = float(ex.get("position_size_usd") or 100.0)
        pnl += float(ev) * size
    return pnl / max(1.0, nav_usd)


def _circuit_breaker_level(*, daily_loss_pct: float) -> str:
    if daily_loss_pct > 0.05:
        return "PANIC"
    if daily_loss_pct > 0.02:
        return "HARD"
    if daily_loss_pct > 0.01:
        return "SOFT"
    return "OK"


def build_stage6_risk_guardrails_report(
    db: Session,
    *,
    days: int = 7,
    horizon: str = "6h",
    signal_type: str = SignalType.DIVERGENCE.value,
    nav_usd: float = 10000.0,
    rollback_min_samples: int = 30,
    rollback_pvalue_threshold: float = 0.10,
    rollback_cooldown_days: int = 7,
) -> dict[str, Any]:
    days = max(1, min(int(days), 60))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    signal_rows = list(db.scalars(select(Signal).where(Signal.created_at >= cutoff)))
    daily_pnl_pct = _daily_ev_pnl(signal_rows, nav_usd=nav_usd)
    daily_loss_pct = max(0.0, -daily_pnl_pct)
    breaker_level = _circuit_breaker_level(daily_loss_pct=daily_loss_pct)

    field_name = {
        "1h": "probability_after_1h",
        "6h": "probability_after_6h",
        "24h": "probability_after_24h",
    }.get((horizon or "6h").strip().lower(), "probability_after_6h")

    try:
        parsed_type = SignalType(signal_type.strip().upper())
    except ValueError:
        return {"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]}

    history = list(
        db.scalars(
            select(SignalHistory).where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.signal_type == parsed_type,
                SignalHistory.probability_at_signal.is_not(None),
                getattr(SignalHistory, field_name).is_not(None),
            )
        )
    )
    returns = [
        float(getattr(r, field_name)) - float(r.probability_at_signal)
        for r in history
        if r.probability_at_signal is not None and getattr(r, field_name) is not None
    ]

    n = len(returns)
    p_value = _one_sided_p_value_mean_lt_zero(returns)
    rollback_triggered = False
    rollback_reason = None
    if n >= rollback_min_samples and p_value is not None and p_value < rollback_pvalue_threshold and mean(returns) < 0:
        rollback_triggered = True
        rollback_reason = (
            f"n={n} >= {rollback_min_samples}, mean_return={mean(returns):.6f} < 0, "
            f"one-sided p={p_value:.4f} < {rollback_pvalue_threshold}"
        )

    actions = {
        "SOFT": "reduce_position_size_50pct_and_alert",
        "HARD": "halt_new_entries_manual_reset_required",
        "PANIC": "panic_mode_no_new_risk_and_emergency_review",
        "OK": "none",
    }

    return {
        "period_days": days,
        "horizon": horizon,
        "signal_type": parsed_type.value,
        "daily_loss_pct": round(daily_loss_pct, 6),
        "circuit_breaker_level": breaker_level,
        "circuit_breaker_action": actions[breaker_level],
        "rollback": {
            "triggered": rollback_triggered,
            "reason": rollback_reason,
            "samples": n,
            "mean_return": round(mean(returns), 6) if returns else 0.0,
            "one_sided_p_value": round(float(p_value), 6) if p_value is not None else None,
            "min_samples": rollback_min_samples,
            "pvalue_threshold": rollback_pvalue_threshold,
            "cooldown_days": rollback_cooldown_days,
        },
        "notes": [
            "Circuit breaker uses expected EV proxy from execution_analysis.",
            "Statistical rollback uses one-sided test H1: mean_return < 0.",
        ],
    }


def extract_stage6_risk_guardrails_metrics(report: dict[str, Any]) -> dict[str, float]:
    rollback = report.get("rollback") or {}
    level = str(report.get("circuit_breaker_level") or "OK")
    level_score = {"OK": 0.0, "SOFT": 0.33, "HARD": 0.66, "PANIC": 1.0}.get(level, 0.0)
    return {
        "stage6_guardrail_level_score": level_score,
        "stage6_daily_loss_pct": float(report.get("daily_loss_pct") or 0.0),
        "stage6_rollback_triggered": 1.0 if bool(rollback.get("triggered")) else 0.0,
        "stage6_rollback_samples": float(rollback.get("samples") or 0.0),
    }
