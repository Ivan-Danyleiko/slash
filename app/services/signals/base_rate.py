from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Market
from app.services.external.binance_history import estimate_probability_for_level
from app.services.external.usgs import estimate_no_earthquake_probability


@dataclass(slots=True)
class BaseRateEstimate:
    our_prob: float
    confidence: float
    source: str
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "our_prob": round(float(self.our_prob), 6),
            "confidence": round(float(self.confidence), 6),
            "source": str(self.source),
            "reasoning": str(self.reasoning),
        }


class BaseRateEstimator:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def estimate(self, market: Market, tail_category: str, strategy: str) -> dict[str, Any]:
        market_prob = float(market.probability_yes or 0.5)
        if not math.isfinite(market_prob):
            market_prob = 0.5
        market_prob = min(0.999, max(0.001, market_prob))
        if tail_category == "natural_disaster":
            usgs = self._usgs_estimate()
            if usgs is not None:
                return usgs.to_dict()
        if tail_category == "crypto_level":
            crypto = self._crypto_volatility_estimate(market)
            if crypto is not None:
                return crypto.to_dict()

        hist = self._historical_estimate(tail_category=tail_category, market_prob=market_prob)
        if hist is not None:
            return hist.to_dict()

        # deterministic fallback (no direct LLM call in stage17 foundation)
        if strategy == "bet_no":
            our = max(0.001, market_prob * 0.50)
            return BaseRateEstimate(
                our_prob=our,
                confidence=0.35,
                source="deterministic_fallback_bet_no",
                reasoning="no_external_or_historical_signal",
            ).to_dict()
        if strategy == "bet_yes":
            # Contrarian uplift for underestimated tail-yes scenarios.
            # Using division by 0.6 gives stronger correction than linear +25%.
            our = min(0.99, max(0.001, market_prob / 0.60))
            return BaseRateEstimate(
                our_prob=our,
                confidence=0.35,
                source="deterministic_fallback_bet_yes",
                reasoning="no_external_or_historical_signal",
            ).to_dict()
        return BaseRateEstimate(
            our_prob=market_prob,
            confidence=0.20,
            source="deterministic_fallback_neutral",
            reasoning="strategy_requires_tail_llm_stage_not_enabled",
        ).to_dict()

    @staticmethod
    def _extract_crypto_target(title: str) -> tuple[str, str, float] | None:
        text = str(title or "").lower()
        symbol = None
        if "bitcoin" in text or "btc" in text:
            symbol = "BTCUSDT"
        elif "ethereum" in text or "eth" in text:
            symbol = "ETHUSDT"
        elif "solana" in text or "sol" in text:
            symbol = "SOLUSDT"
        if symbol is None:
            return None

        direction = None
        if any(k in text for k in ("above", "over", "reach", "hit", "exceed")):
            direction = "above"
        elif any(k in text for k in ("below", "under", "drop below", "fall below")):
            direction = "below"
        if direction is None:
            return None

        m = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text)
        if not m:
            return None
        target = float(m.group(1).replace(",", ""))
        if target <= 0:
            return None
        return symbol, direction, target

    def _crypto_volatility_estimate(self, market: Market) -> BaseRateEstimate | None:
        if not bool(self.settings.signal_tail_base_rate_external_enabled):
            return None
        parsed = self._extract_crypto_target(str(market.title or ""))
        if parsed is None:
            return None
        symbol, direction, target = parsed
        try:
            resp = httpx.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=8.0,
            )
            if resp.status_code != 200:
                return None
            payload = resp.json() or {}
            spot = float(payload.get("price") or 0.0)
            if spot <= 0:
                return None
        except Exception:  # noqa: BLE001
            return None

        rt = market.resolution_time
        if rt is not None:
            now = datetime.now(UTC)
            ref = rt.astimezone(UTC) if rt.tzinfo else rt.replace(tzinfo=UTC)
            days = max(1.0, (ref - now).total_seconds() / 86400.0)
        else:
            days = 30.0
        out = estimate_probability_for_level(
            symbol=symbol,
            spot_price=spot,
            target_price=float(target),
            days_to_deadline=days,
            direction=direction,
            timeout_seconds=8.0,
        )
        if out is None:
            return None
        return BaseRateEstimate(
            our_prob=float(out.get("our_prob") or 0.0),
            confidence=float(out.get("confidence") or 0.0),
            source=str(out.get("source") or "external_binance_lognormal"),
            reasoning=str(out.get("reasoning") or ""),
        )

    def _usgs_estimate(self) -> BaseRateEstimate | None:
        if not bool(self.settings.signal_tail_base_rate_external_enabled):
            return None
        out = estimate_no_earthquake_probability(
            min_magnitude=4.5,
            lookback_days=365,
            timeout_seconds=8.0,
        )
        if out is None:
            return None
        return BaseRateEstimate(
            our_prob=float(out.get("our_prob") or 0.0),
            confidence=float(out.get("confidence") or 0.0),
            source=str(out.get("source") or "external_usgs_poisson"),
            reasoning=str(out.get("reasoning") or ""),
        )

    def _historical_estimate(self, *, tail_category: str, market_prob: float) -> BaseRateEstimate | None:
        rows = list(
            self.db.scalars(
                select(Market)
                .where(Market.category.is_not(None))
                .where(Market.probability_yes.is_not(None))
                .order_by(Market.fetched_at.desc())
                .limit(2000)
            )
        )
        if not rows:
            return None
        # lightweight proxy: use category prior from recent market probabilities
        vals: list[float] = []
        for m in rows:
            cat = str(m.category or "").lower()
            if tail_category == "crypto_level" and "crypto" not in cat and "bitcoin" not in cat:
                continue
            if tail_category == "sports_outcome" and "sport" not in cat and "nba" not in cat and "nfl" not in cat:
                continue
            if tail_category == "political_stability" and "polit" not in cat and "election" not in cat:
                continue
            vals.append(float(m.probability_yes or 0.0))
            if len(vals) >= 200:
                break
        if len(vals) < 25:
            return None
        mean_prob = sum(vals) / len(vals)
        # Pull estimate away from market probability only slightly.
        our = (0.6 * market_prob) + (0.4 * mean_prob)
        conf = min(0.7, 0.3 + (len(vals) / 500.0))
        return BaseRateEstimate(
            our_prob=max(0.001, min(0.999, our)),
            confidence=conf,
            source="historical_category_prior",
            reasoning=f"n={len(vals)},mean_prob={mean_prob:.4f}",
        )
