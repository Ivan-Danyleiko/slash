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
        if tail_category in {"crypto_level", "price_target"}:
            crypto = self._crypto_volatility_estimate(market)
            if crypto is not None:
                return crypto.to_dict()

        hist = self._historical_estimate(tail_category=tail_category, market_prob=market_prob)
        if hist is not None:
            return hist.to_dict()

        # deterministic category-aware fallback
        our = self._deterministic_prob_by_category(
            tail_category=tail_category,
            market_prob=market_prob,
        )
        conf = self._deterministic_confidence_by_category(
            tail_category=tail_category,
            market_prob=market_prob,
            strategy=strategy,
        )
        reasoning = f"category_rule:{tail_category};strategy={strategy};market_prob={market_prob:.4f}"
        return BaseRateEstimate(
            our_prob=our,
            confidence=conf,
            source="deterministic_category_prior_v2",
            reasoning=reasoning,
        ).to_dict()

    @staticmethod
    def _clamp_prob(value: float) -> float:
        return min(0.99, max(0.001, float(value)))

    def _deterministic_prob_by_category(self, *, tail_category: str, market_prob: float) -> float:
        p = float(market_prob)
        cat = str(tail_category or "").lower()
        low = p < 0.10
        mid = 0.10 <= p < 0.15

        if cat in {"sports_match"}:
            if low:
                return self._clamp_prob(p / 0.75)
            if mid:
                return self._clamp_prob(p * 0.95)
            return self._clamp_prob(p * 0.90)

        if cat in {"geopolitical_event", "election", "regulatory"}:
            if low:
                return self._clamp_prob(p / 0.60)
            if mid:
                return self._clamp_prob(p / 0.65)
            return self._clamp_prob(p / 0.75)

        if cat in {"earnings_surprise", "company_valuation"}:
            if low:
                return self._clamp_prob(p / 0.60)
            if mid:
                return self._clamp_prob(p / 0.65)
            return self._clamp_prob(p * 0.90)

        # fallback for remaining categories
        if low:
            return self._clamp_prob(p / 0.60)
        if mid:
            return self._clamp_prob(p / 0.65)
        return self._clamp_prob(p / 0.75)

    def _deterministic_confidence_by_category(self, *, tail_category: str, market_prob: float, strategy: str) -> float:
        cat = str(tail_category or "").lower()
        if cat in {"price_target", "crypto_level"} and bool(self.settings.signal_tail_base_rate_external_enabled):
            return 0.55
        if cat in {"sports_match"}:
            return 0.38 if market_prob >= 0.10 else 0.42
        if cat in {"geopolitical_event", "election", "regulatory"}:
            return 0.42
        if cat in {"earnings_surprise", "company_valuation"}:
            return 0.40
        if strategy == "llm_evaluate":
            return 0.38
        return 0.40

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

        m_suffix = re.search(r"[0-9][0-9,]*(?:[.][0-9]+)?\s*([kmb])\b", text)
        if m_suffix:
            raw = m_suffix.group(0)
            suffix = m_suffix.group(1).lower()
            num_str = raw[: raw.lower().index(suffix)].strip()
            target = float(num_str.replace(",", ""))
            if suffix == "k":
                target *= 1_000
            elif suffix == "m":
                target *= 1_000_000
            elif suffix == "b":
                target *= 1_000_000_000
        else:
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
            if tail_category in {"crypto_level", "price_target"} and "crypto" not in cat and "bitcoin" not in cat:
                continue
            if tail_category == "sports_match" and "sport" not in cat and "nba" not in cat and "nfl" not in cat:
                continue
            if tail_category == "geopolitical_event" and "polit" not in cat and "election" not in cat:
                continue
            if tail_category == "earnings_surprise" and "earn" not in cat and "stock" not in cat and "finance" not in cat:
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
