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
                    SignalHistory.signal_id.is_not(None),
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
            raw = float(after) - float(row.probability_at_signal)
            direction = str(row.signal_direction or "YES").upper()
            returns.append(-raw if direction == "NO" else raw)
        return returns

    @staticmethod
    def _kelly_size(
        *,
        expected_edge: float,
        market_prob: float,
        kelly_alpha: float,
        base_size_usd: float,
        portfolio_cap_usd: float,
        per_market_cap_pct: float,
        liquidity_value: float,
    ) -> float:
        """Compute effective position size via Half-Kelly with caps.

        Kelly fraction f* = edge / (b*q - a*p)  simplified to edge / prob_complement.
        size_effective = base_size * kelly_fraction * kelly_alpha, capped at:
          - per_market_cap: per_market_cap_pct * portfolio_cap_usd
          - liquidity_cap: 10% of market liquidity
        """
        market_prob = max(0.01, min(0.99, float(market_prob)))
        q = 1.0 - market_prob  # prob of not resolving YES
        # Full-Kelly fraction relative to base_size
        if q > 0 and expected_edge > 0:
            kelly_f = expected_edge / q
        else:
            kelly_f = 0.0
        kelly_f = max(0.0, min(1.0, kelly_f))
        size = base_size_usd * kelly_f * kelly_alpha
        per_market_cap = per_market_cap_pct * portfolio_cap_usd
        liquidity_cap = max(10.0, float(liquidity_value) * 0.10)
        size = min(size, per_market_cap, liquidity_cap)
        return max(1.0, round(size, 2))

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

        # Stage19 19A: apply post-hoc calibration to market probability before cost calc.
        raw_prob = float(market.probability_yes or 0.5)
        calibrated_prob_yes = raw_prob
        calibration_version = "passthrough_v1"
        calibration_confidence = 0.0
        if bool(self.settings.stage19_calibration_enabled):
            try:
                from app.services.signals.calibration import get_calibrator
                cal = get_calibrator(
                    self.db,
                    settings=self.settings,
                    signal_type_filter=signal_type.value if signal_type else None,
                )
                calibrated_prob_yes = cal.calibrate(raw_prob)
                calibration_version = cal.calibration_version
                calibration_confidence = cal.calibration_confidence
            except Exception:  # noqa: BLE001
                pass  # calibration failure is non-fatal — fall through to raw_prob

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
        liq = float(liquidity_score or 0.0)
        utility_score = slippage_adjusted_edge * (0.4 + 0.6 * liq) * time_penalty

        # Stage19 19B: compute effective position size (size-consistent utility).
        base_size_usd = max(1.0, float(self.settings.signal_execution_position_size_usd))
        kelly_alpha = max(0.1, min(1.0, float(getattr(self.settings, "stage19_kelly_alpha", 0.5))))
        portfolio_cap = max(100.0, float(getattr(self.settings, "stage19_portfolio_cap_usd", 500.0)))
        per_market_cap_pct = max(0.01, min(0.5, float(getattr(self.settings, "stage19_per_market_cap_pct", 0.10))))
        size_consistency_enabled = bool(getattr(self.settings, "stage19_size_consistency_enabled", True))

        if size_consistency_enabled and slippage_adjusted_edge > 0:
            size_effective_usd = self._kelly_size(
                expected_edge=slippage_adjusted_edge,
                market_prob=calibrated_prob_yes,
                kelly_alpha=kelly_alpha,
                base_size_usd=base_size_usd,
                portfolio_cap_usd=portfolio_cap,
                per_market_cap_pct=per_market_cap_pct,
                liquidity_value=float(market.liquidity_value or 0.0),
            )
            # Recompute costs_pct on effective size (gas fee dominates at small sizes).
            if platform_name == "POLYMARKET":
                gas_usd = float(self.settings.signal_execution_polymarket_gas_fee_usd)
                bridge_usd = float(self.settings.signal_execution_polymarket_bridge_fee_usd)
                costs_pct_effective = costs_pct - (gas_usd / base_size_usd) + (gas_usd / size_effective_usd) \
                    - (bridge_usd / base_size_usd) + (bridge_usd / size_effective_usd)
                costs_pct_effective = max(0.0, round(costs_pct_effective, 6))
            else:
                costs_pct_effective = costs_pct
            ev_effective = max(0.0, expected_edge - costs_pct_effective)
            utility_score_effective = ev_effective * (0.4 + 0.6 * liq) * time_penalty
        else:
            size_effective_usd = base_size_usd
            costs_pct_effective = costs_pct
            utility_score_effective = utility_score

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
            # Stage19 additions
            "utility_score_effective": round(utility_score_effective, 6),
            "size_input_usd": round(base_size_usd, 2),
            "size_effective_usd": round(size_effective_usd, 2),
            "costs_pct_effective": round(costs_pct_effective, 6),
            "calibrated_prob_yes": round(calibrated_prob_yes, 6),
            "calibration_version": calibration_version,
            "calibration_confidence": round(calibration_confidence, 4),
            "execution_platform": platform_name.lower() if platform_name else "unknown",
            "is_neg_risk": bool(market.is_neg_risk),
        }


def build_execution_simulator(*, db: Session, settings: Settings) -> ExecutionSimulator | ExecutionSimulatorV2:
    if (settings.signal_execution_model or "").strip().lower() == "v2":
        return ExecutionSimulatorV2(db=db, settings=settings)
    return ExecutionSimulator()
