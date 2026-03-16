from datetime import datetime

import httpx

from app.core.config import get_settings
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.base import BaseCollector
from app.utils.http import retry_request


class ManifoldCollector(BaseCollector):
    platform_name = "MANIFOLD"

    @staticmethod
    def _normalize_category(group_slugs: list[str] | None) -> str | None:
        if not group_slugs:
            return None
        joined = " ".join(str(x).lower() for x in group_slugs if x)
        if any(k in joined for k in ("crypto", "bitcoin", "ethereum", "defi", "solana")):
            return "crypto"
        if any(k in joined for k in ("politic", "election", "government", "policy")):
            return "politics"
        if any(k in joined for k in ("sport", "soccer", "nba", "nfl", "mlb", "tennis")):
            return "sports"
        if any(k in joined for k in ("finance", "econom", "fed", "rate", "stocks", "market")):
            return "finance"
        return "other"

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
            lambda: httpx.get(url, params={"limit": 500}, timeout=15.0),
            retries=3,
            backoff_seconds=1.0,
            platform=self.platform_name,
        )
        resp.raise_for_status()
        data = resp.json()

        items: list[NormalizedMarketDTO] = []
        for row in data[:500]:
            # Stage 9: only open, binary-compatible markets for cleaner execution research.
            if bool(row.get("isResolved")):
                continue
            outcome_type = str(row.get("outcomeType") or "").strip().upper()
            if outcome_type not in {"BINARY", "PSEUDO_NUMERIC"}:
                continue
            if str(row.get("mechanism") or "").strip().upper() == "FIXED_PRICE":
                continue
            yes_prob = row.get("probability")
            close_time = row.get("closeTime")
            group_slugs = row.get("groupSlugs") or []
            category = self._normalize_category(group_slugs if isinstance(group_slugs, list) else None)
            payload = {**row, "execution_source": "manifold_api"}
            items.append(
                NormalizedMarketDTO(
                    platform=self.platform_name,
                    external_market_id=str(row.get("id")),
                    title=row.get("question", ""),
                    description=row.get("description"),
                    category=category,
                    url=f"https://manifold.markets/{row.get('creatorUsername', '')}/{row.get('slug', '')}",
                    status="OPEN",
                    probability_yes=yes_prob,
                    probability_no=(1 - yes_prob) if yes_prob is not None else None,
                    volume_24h=float(row.get("volume24Hours") or 0),
                    liquidity_value=float(row.get("totalLiquidity") or 0),
                    created_at=self._parse_ms_timestamp(row.get("createdTime")),
                    resolution_time=self._parse_ms_timestamp(close_time),
                    rules_text=row.get("resolutionCriteria"),
                    source_payload=payload,
                )
            )
        return items
