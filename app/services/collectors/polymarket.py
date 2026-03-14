import httpx
import json

from app.core.config import get_settings
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.base import BaseCollector
from app.utils.http import retry_request


class PolymarketCollector(BaseCollector):
    """Partial integration. Uses Gamma markets endpoint where fields may vary."""

    platform_name = "POLYMARKET"

    @staticmethod
    def _as_list(value: object) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except Exception:  # noqa: BLE001
                return []
        return []

    @classmethod
    def _extract_probability_yes(cls, row: dict) -> float | None:
        direct = row.get("probability")
        if isinstance(direct, (float, int)):
            return float(direct)

        outcomes = cls._as_list(row.get("outcomes"))
        prices = cls._as_list(row.get("outcomePrices"))
        if not prices:
            return None
        numeric_prices: list[float] = []
        for p in prices:
            try:
                numeric_prices.append(float(p))
            except (TypeError, ValueError):
                continue
        if not numeric_prices:
            return None

        yes_idx = None
        for idx, outcome in enumerate(outcomes):
            token = str(outcome).strip().lower()
            if token in {"yes", "true", "1"}:
                yes_idx = idx
                break
        if yes_idx is None:
            yes_idx = 0 if len(numeric_prices) == 2 else None
        if yes_idx is None or yes_idx >= len(numeric_prices):
            return None
        yes_prob = numeric_prices[yes_idx]
        if 0.0 <= yes_prob <= 1.0:
            return yes_prob
        return None

    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        settings = get_settings()
        url = f"{settings.polymarket_api_base_url}/markets"
        resp = retry_request(
            lambda: httpx.get(url, params={"limit": 100}, timeout=20.0),
            retries=3,
            backoff_seconds=1.0,
            platform=self.platform_name,
        )
        resp.raise_for_status()
        rows = resp.json()

        markets: list[NormalizedMarketDTO] = []
        for row in rows:
            prob = self._extract_probability_yes(row)
            markets.append(
                NormalizedMarketDTO(
                    platform=self.platform_name,
                    external_market_id=str(row.get("id")),
                    title=row.get("question", ""),
                    description=row.get("description"),
                    category=row.get("category"),
                    url=row.get("url"),
                    status=row.get("status"),
                    probability_yes=float(prob) if isinstance(prob, (float, int)) else None,
                    probability_no=(1 - float(prob)) if isinstance(prob, (float, int)) else None,
                    volume_24h=float(row.get("volume24h") or row.get("volume24hr") or row.get("volumeNum") or 0),
                    liquidity_value=float(row.get("liquidity") or row.get("liquidityNum") or 0),
                    rules_text=row.get("rules"),
                    source_payload=row,
                )
            )
        return markets
