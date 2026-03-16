from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory


def _days_to_resolution(resolution_time: datetime | None) -> float:
    if resolution_time is None:
        return 365.0
    dt = resolution_time
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, (dt - datetime.now(UTC)).total_seconds() / 86400.0)


class ExecutionSimulator:
    """MVP execution model with explicit simplifying assumptions."""

    ASSUMPTIONS_VERSION = "v1_naive_no_orderbook"

    def simulate(
        self,
        *,
        market: Market,
        confidence_score: float | None,
        liquidity_score: float | None,
        recent_move: float | None = None,
        signal_type: SignalType | None = None,
    ) -> dict:
        prob = float(market.probability_yes or 0.5)
        conf = float(confidence_score or 0.0)
        liq = float(liquidity_score or 0.0)
        volume = float(market.volume_24h or 0.0)
        liquidity_value = float(market.liquidity_value or 0.0)

        # Edge proxy: directional conviction + confidence + optional recent move.
        edge_core = abs(prob - 0.5) * 2.0
        move_boost = min(1.0, float(recent_move or 0.0) / 0.2)
        expected_edge = min(1.0, (0.55 * edge_core) + (0.25 * conf) + (0.20 * move_boost))

        # Coarse slippage proxy when orderbook depth is unavailable.
        slippage_factor = min(0.05, (100.0 / max(volume, 1.0)) * 0.01)
        slippage_adjusted_edge = max(0.0, expected_edge - slippage_factor)

        # Capacity proxy: conservative fraction of available liquidity/volume.
        capacity_usd = max(0.0, min(liquidity_value * 0.10, volume * 0.05))

        days_to_resolution = _days_to_resolution(market.resolution_time)
        time_penalty = max(0.60, 1.0 - min(0.40, (days_to_resolution / 365.0) * 0.20))

        utility_score = slippage_adjusted_edge * (0.4 + 0.6 * liq) * time_penalty

        return {
            "assumptions_version": self.ASSUMPTIONS_VERSION,
            "expected_edge": round(expected_edge, 4),
            "slippage_adjusted_edge": round(slippage_adjusted_edge, 4),
            "slippage_factor": round(slippage_factor, 4),
            "capacity_usd": round(capacity_usd, 2),
            "days_to_resolution": round(days_to_resolution, 2),
            "time_penalty": round(time_penalty, 4),
            "utility_score": round(utility_score, 4),
        }


class ExecutionSimulatorV2:
    """Empirical EV-based execution model driven by labeled signal history."""

    ASSUMPTIONS_VERSION = "v2_empirical_labeled_returns"
    _HORIZON_TO_FIELD = {
        "1h": "probability_after_1h",
        "6h": "probability_after_6h",
        "24h": "probability_after_24h",
    }

    def __init__(self, *, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.fallback = ExecutionSimulator()
        self._platform_name_by_id: dict[int, str] = {}

    def _platform_name(self, platform_id: int) -> str:
        cached = self._platform_name_by_id.get(platform_id)
        if cached:
            return cached
        name = str(self.db.scalar(select(Platform.name).where(Platform.id == platform_id)) or "").upper()
        self._platform_name_by_id[platform_id] = name
        return name

    def _horizon_key(self) -> str:
        raw = (self.settings.signal_execution_v2_horizon or "").strip().lower()
        return raw if raw in self._HORIZON_TO_FIELD else "6h"

    def _slippage(self, *, platform: str, volume_24h: float, liquidity_value: float) -> float:
        if platform == "POLYMARKET":
            if (self.settings.signal_execution_polymarket_mode or "").strip().lower() == "clob_api":
                return min(0.04, max(0.001, (50.0 / max(volume_24h, 1.0)) * 0.01))
            return min(0.05, max(0.002, (100.0 / max(volume_24h, 1.0)) * 0.01))
        if platform == "MANIFOLD":
            return min(0.05, self.settings.signal_execution_position_size_usd / max(liquidity_value, 100.0))
        return min(0.05, (100.0 / max(volume_24h, 1.0)) * 0.01)

    @staticmethod
    def _spread_cost_pct(market: Market) -> float:
        if isinstance(market.best_bid_yes, (int, float)) and isinstance(market.best_ask_yes, (int, float)):
            bid = float(market.best_bid_yes)
            ask = float(market.best_ask_yes)
            if ask >= bid >= 0.0:
                return max(0.0, min(0.25, (ask - bid) / 2.0))
        if isinstance(market.spread_cents, (int, float)):
            return max(0.0, min(0.25, float(market.spread_cents) / 100.0 / 2.0))
        return 0.0

    def _costs_pct(self, *, market: Market, platform: str, volume_24h: float, liquidity_value: float) -> tuple[float, float]:
        slippage = self._slippage(platform=platform, volume_24h=volume_24h, liquidity_value=liquidity_value)
        size = max(1.0, float(self.settings.signal_execution_position_size_usd))
        spread = self._spread_cost_pct(market)
        if platform == "POLYMARKET":
            if bool(market.is_neg_risk):
                factor = max(0.2, min(1.0, float(self.settings.signal_execution_polymarket_negrisk_impact_multiplier)))
                slippage *= factor
                if spread > 0.0:
                    spread *= factor
            fee_mode = str(self.settings.signal_execution_polymarket_fee_mode or "zero").strip().lower()
            fee = 0.001 if fee_mode == "dcm10bps" else 0.0
            if spread == 0.0:
                spread = 0.01
            gas = float(self.settings.signal_execution_polymarket_gas_fee_usd) / size
            bridge = float(self.settings.signal_execution_polymarket_bridge_fee_usd) / size
            return (fee + spread + slippage + gas + bridge), slippage
        if platform == "KALSHI":
            price = min(0.99, max(0.01, float(market.probability_yes or 0.5)))
            taker_fee = max(0.0, float(self.settings.signal_execution_kalshi_taker_coeff) * price * (1.0 - price))
            maker_fee = max(0.0, float(self.settings.signal_execution_kalshi_maker_fee_pct))
            if spread == 0.0:
                spread = 0.005
            return (taker_fee + maker_fee + spread + slippage), slippage
        if platform == "MANIFOLD":
            fee = 0.0
            if spread == 0.0:
                spread = 0.005
            return (fee + spread + slippage), slippage
        if spread == 0.0:
            spread = 0.005
        return (spread + slippage), slippage

    def _prior_edge(self, *, market: Market, days_to_resolution: float) -> float:
        category = str(market.category or "other").strip().lower()
        if category == "crypto":
            base = float(self.settings.signal_execution_v2_prior_crypto)
        elif category == "finance":
            base = float(self.settings.signal_execution_v2_prior_finance)
        elif category == "sports":
            base = float(self.settings.signal_execution_v2_prior_sports)
        elif category == "politics":
            base = float(self.settings.signal_execution_v2_prior_politics)
        else:
            base = float(self.settings.signal_execution_v2_prior_other or self.settings.signal_execution_v2_prior_default)
        if days_to_resolution < 1.0:
            mult = 0.7
        elif days_to_resolution < 7.0:
            mult = 0.9
        elif days_to_resolution < 30.0:
            mult = 1.0
        else:
            mult = 1.1
        return max(0.0, base * mult)

    def _empirical_returns(self, *, signal_type: SignalType, market_id: int) -> list[float]:
        horizon = self._horizon_key()
        field_name = self._HORIZON_TO_FIELD[horizon]
        cutoff = datetime.now(UTC) - timedelta(days=max(1, int(self.settings.signal_execution_v2_lookback_days)))
        rows = list(
            self.db.scalars(
                select(SignalHistory).where(
                    SignalHistory.timestamp >= cutoff,
                    SignalHistory.signal_type == signal_type,
                )
            )
        )
        market_specific = [r for r in rows if r.market_id == market_id]
        selected = market_specific if len(market_specific) >= self.settings.signal_execution_v2_min_samples else rows
        returns: list[float] = []
        for row in selected:
            after = getattr(row, field_name)
            if row.probability_at_signal is None or after is None:
                continue
            returns.append(float(after) - float(row.probability_at_signal))
        return returns

    def simulate(
        self,
        *,
        market: Market,
        confidence_score: float | None,
        liquidity_score: float | None,
        recent_move: float | None = None,
        signal_type: SignalType | None = None,
    ) -> dict:
        if signal_type is None:
            payload = self.fallback.simulate(
                market=market,
                confidence_score=confidence_score,
                liquidity_score=liquidity_score,
                recent_move=recent_move,
                signal_type=signal_type,
            )
            payload["assumptions_version"] = f"{self.ASSUMPTIONS_VERSION}_fallback_missing_signal_type"
            return payload

        returns = self._empirical_returns(signal_type=signal_type, market_id=market.id)
        min_samples = max(1, int(self.settings.signal_execution_v2_min_samples))
        days_to_resolution = _days_to_resolution(market.resolution_time)

        wins = [r for r in returns if r > 0]
        losses = [-r for r in returns if r <= 0]
        hit_rate = (len(wins) / len(returns)) if returns else 0.5
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        expected_edge_empirical = (hit_rate * avg_win) - ((1.0 - hit_rate) * avg_loss)
        w_empirical = min(1.0, len(returns) / float(min_samples))
        prior_edge = self._prior_edge(market=market, days_to_resolution=days_to_resolution)
        expected_edge = (w_empirical * expected_edge_empirical) + ((1.0 - w_empirical) * prior_edge)

        platform_name = self._platform_name(market.platform_id)
        costs_pct, slippage_factor = self._costs_pct(
            market=market,
            platform=platform_name,
            volume_24h=float(market.volume_24h or 0.0),
            liquidity_value=float(market.liquidity_value or 0.0),
        )
        ev_after_costs = expected_edge - costs_pct
        slippage_adjusted_edge = max(0.0, ev_after_costs)
        capacity_usd = max(
            0.0,
            min(float(market.liquidity_value or 0.0) * 0.1, float(market.volume_24h or 0.0) * 0.05),
        )
        time_penalty = max(0.60, 1.0 - min(0.40, (days_to_resolution / 365.0) * 0.20))
        utility_score = slippage_adjusted_edge * (0.4 + 0.6 * float(liquidity_score or 0.0)) * time_penalty

        return {
            "assumptions_version": (
                self.ASSUMPTIONS_VERSION
                if len(returns) >= min_samples
                else f"{self.ASSUMPTIONS_VERSION}_shrinkage_fallback_insufficient_samples"
            ),
            "ev_model": "empirical",
            "empirical_samples": len(returns),
            "empirical_weight": round(w_empirical, 6),
            "prior_edge": round(prior_edge, 6),
            "empirical_hit_rate": round(hit_rate, 4),
            "empirical_avg_win": round(avg_win, 6),
            "empirical_avg_loss": round(avg_loss, 6),
            "expected_edge_empirical": round(expected_edge_empirical, 6),
            "expected_edge": round(expected_edge, 6),
            "expected_ev_after_costs_pct": round(ev_after_costs, 6),
            "expected_costs_pct": round(costs_pct, 6),
            "slippage_adjusted_edge": round(slippage_adjusted_edge, 6),
            "slippage_factor": round(slippage_factor, 6),
            "capacity_usd": round(capacity_usd, 2),
            "days_to_resolution": round(days_to_resolution, 2),
            "time_penalty": round(time_penalty, 4),
            "utility_score": round(utility_score, 6),
            "execution_platform": platform_name.lower() if platform_name else "unknown",
            "is_neg_risk": bool(market.is_neg_risk),
        }


def build_execution_simulator(*, db: Session, settings: Settings) -> ExecutionSimulator | ExecutionSimulatorV2:
    if (settings.signal_execution_model or "").strip().lower() == "v2":
        return ExecutionSimulatorV2(db=db, settings=settings)
    return ExecutionSimulator()
