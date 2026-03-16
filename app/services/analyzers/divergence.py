from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.models.models import Market


@dataclass(frozen=True)
class ExecutableDivergenceResult:
    gross_divergence: float
    executable_divergence: float
    net_edge_after_costs: float
    direction: str
    has_clob_data: bool
    spread_a: float
    spread_b: float
    ask_a: float
    bid_a: float
    ask_b: float
    bid_b: float


class DivergenceDetector:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings

    def divergence(self, market_a: Market, market_b: Market) -> float | None:
        if market_a.probability_yes is None or market_b.probability_yes is None:
            return None
        return abs(float(market_a.probability_yes) - float(market_b.probability_yes))

    @staticmethod
    def _effective_spread(market: Market) -> tuple[float, float, float, bool]:
        if isinstance(market.best_ask_yes, (int, float)) and isinstance(market.best_bid_yes, (int, float)):
            ask = float(market.best_ask_yes)
            bid = float(market.best_bid_yes)
            spread = max(0.0, ask - bid)
            return ask, bid, spread, True

        p = float(market.probability_yes or 0.5)
        if isinstance(market.spread_cents, (int, float)):
            half = max(0.0, float(market.spread_cents) / 200.0)
        else:
            half = 0.01
        ask = min(0.999, p + half)
        bid = max(0.001, p - half)
        spread = max(0.0, ask - bid)
        return ask, bid, spread, False

    def compute_executable_divergence(
        self,
        market_a: Market,
        market_b: Market,
        *,
        position_size_usd: float | None = None,
        gas_fee_usd: float | None = None,
        bridge_fee_usd: float | None = None,
    ) -> ExecutableDivergenceResult | None:
        gross = self.divergence(market_a, market_b)
        if gross is None:
            return None

        ask_a, bid_a, spread_a, has_clob_a = self._effective_spread(market_a)
        ask_b, bid_b, spread_b, has_clob_b = self._effective_spread(market_b)

        p_a = float(market_a.probability_yes or 0.5)
        p_b = float(market_b.probability_yes or 0.5)
        if p_a <= p_b:
            executable = bid_b - ask_a
            direction = "YES"
        else:
            executable = bid_a - ask_b
            direction = "NO"

        size = max(1.0, float(position_size_usd or (self.settings.signal_divergence_position_size_usd if self.settings else 50.0)))
        gas = float(gas_fee_usd if gas_fee_usd is not None else (self.settings.signal_divergence_gas_fee_usd if self.settings else 2.0))
        bridge = float(
            bridge_fee_usd if bridge_fee_usd is not None else (self.settings.signal_divergence_bridge_fee_usd if self.settings else 0.5)
        )
        fixed_costs_pct = (gas + bridge) / size
        net = executable - fixed_costs_pct

        return ExecutableDivergenceResult(
            gross_divergence=float(gross),
            executable_divergence=float(executable),
            net_edge_after_costs=float(net),
            direction=direction,
            has_clob_data=bool(has_clob_a or has_clob_b),
            spread_a=float(spread_a),
            spread_b=float(spread_b),
            ask_a=float(ask_a),
            bid_a=float(bid_a),
            ask_b=float(ask_b),
            bid_b=float(bid_b),
        )
