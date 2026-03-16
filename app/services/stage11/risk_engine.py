from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Stage11RiskInput:
    daily_drawdown_pct: float
    weekly_drawdown_pct: float
    consecutive_losses: int
    execution_error_rate_1h: float
    reconciliation_gap_usd: float


def resolve_circuit_breaker_level(
    v: Stage11RiskInput,
    *,
    soft_daily_drawdown_pct: float = -1.5,
    soft_consecutive_losses: int = 4,
    hard_daily_drawdown_pct: float = -3.0,
    hard_weekly_drawdown_pct: float = -5.0,
    hard_consecutive_losses: int = 7,
    panic_daily_drawdown_pct: float = -6.0,
    panic_execution_error_rate_1h: float = 0.10,
    panic_reconciliation_gap_usd: float = 50.0,
) -> str:
    if (
        v.daily_drawdown_pct <= panic_daily_drawdown_pct
        or v.execution_error_rate_1h >= panic_execution_error_rate_1h
        or v.reconciliation_gap_usd > panic_reconciliation_gap_usd
    ):
        return "PANIC"
    if (
        v.daily_drawdown_pct <= hard_daily_drawdown_pct
        or v.weekly_drawdown_pct <= hard_weekly_drawdown_pct
        or v.consecutive_losses >= hard_consecutive_losses
    ):
        return "HARD"
    if v.daily_drawdown_pct <= soft_daily_drawdown_pct or v.consecutive_losses >= soft_consecutive_losses:
        return "SOFT"
    return "OK"

