from datetime import datetime

import httpx

from app.core.config import get_settings
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.base import BaseCollector
from app.utils.http import retry_request


class ManifoldCollector(BaseCollector):
    platform_name = "MANIFOLD"

    @staticmethod
    def _parse_ms_timestamp(value: int | float | None) -> datetime | None:
        if value is None:
            return None
        # Manifold returns unix timestamps in milliseconds.
        return datetime.fromtimestamp(float(value) / 1000)

    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        settings = get_settings()
        url = f"{settings.manifold_api_base_url}/markets"
        resp = retry_request(
            lambda: httpx.get(url, timeout=15.0),
            retries=3,
            backoff_seconds=1.0,
            platform=self.platform_name,
        )
        resp.raise_for_status()
        data = resp.json()

        items: list[NormalizedMarketDTO] = []
        for row in data[:200]:
            yes_prob = row.get("probability")
            close_time = row.get("closeTime")
            items.append(
                NormalizedMarketDTO(
                    platform=self.platform_name,
                    external_market_id=str(row.get("id")),
                    title=row.get("question", ""),
                    description=row.get("description"),
                    category=row.get("groupSlugs", [None])[0],
                    url=f"https://manifold.markets/{row.get('creatorUsername', '')}/{row.get('slug', '')}",
                    status=row.get("outcomeType"),
                    probability_yes=yes_prob,
                    probability_no=(1 - yes_prob) if yes_prob is not None else None,
                    volume_24h=float(row.get("volume24Hours") or 0),
                    liquidity_value=float(row.get("totalLiquidity") or 0),
                    created_at=self._parse_ms_timestamp(row.get("createdTime")),
                    resolution_time=self._parse_ms_timestamp(close_time),
                    rules_text=row.get("resolutionCriteria"),
                    source_payload=row,
                )
            )
        return items
