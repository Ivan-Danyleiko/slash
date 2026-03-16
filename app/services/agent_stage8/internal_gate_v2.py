from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.models import Market, Signal


@dataclass
class InternalGateV2Result:
    passed: bool
    edge_after_costs: float
    reason_codes: list[str]


def _edge_after_costs(signal: Signal) -> float:
    payload = signal.execution_analysis or {}
    if isinstance(payload.get("expected_ev_after_costs_pct"), (int, float)):
        return float(payload.get("expected_ev_after_costs_pct") or 0.0)
    if isinstance(payload.get("slippage_adjusted_edge"), (int, float)):
        return float(payload.get("slippage_adjusted_edge") or 0.0)
    if isinstance(payload.get("expected_edge"), (int, float)):
        return float(payload.get("expected_edge") or 0.0)
    return 0.0


def _freshness_minutes(market: Market) -> float:
    if not market.fetched_at:
        return 10_000.0
    fetched_at = market.fetched_at if market.fetched_at.tzinfo else market.fetched_at.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - fetched_at).total_seconds() / 60.0)


def evaluate_internal_gate_v2(
    *,
    signal: Signal,
    market: Market,
    category_policy: dict[str, float],
) -> InternalGateV2Result:
    reason_codes: list[str] = []
    passed = True

    edge_after_costs = _edge_after_costs(signal)
    min_edge = float(category_policy.get("min_edge_after_costs", 0.0))
    if edge_after_costs < min_edge:
        passed = False
        reason_codes.append("edge_after_costs_below_min")

    liquidity_value = float(market.liquidity_value or 0.0)
    volume_24h = float(market.volume_24h or 0.0)
    liquidity_proxy = max(liquidity_value, volume_24h)
    min_liq = float(category_policy.get("min_liquidity_usd", 0.0))
    if liquidity_proxy < min_liq:
        passed = False
        reason_codes.append("liquidity_below_min")

    min_ttr = float(category_policy.get("min_ttr_hours", 0.0))
    if not market.resolution_time:
        passed = False
        reason_codes.append("ttr_missing")
    else:
        resolution_time = (
            market.resolution_time if market.resolution_time.tzinfo else market.resolution_time.replace(tzinfo=UTC)
        )
        ttr_hours = max(0.0, (resolution_time - datetime.now(UTC)).total_seconds() / 3600.0)
        if ttr_hours < min_ttr:
            passed = False
            reason_codes.append("ttr_below_min")

    freshness_min = float(category_policy.get("min_freshness_minutes", 0.0))
    if _freshness_minutes(market) > freshness_min:
        passed = False
        reason_codes.append("market_freshness_too_old")

    return InternalGateV2Result(passed=passed, edge_after_costs=edge_after_costs, reason_codes=reason_codes)
