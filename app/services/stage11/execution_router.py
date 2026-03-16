from __future__ import annotations

from app.core.config import Settings
from app.services.stage11.venues.base import Stage11VenueAdapter
from app.services.stage11.venues.polymarket_clob_adapter import PolymarketClobAdapter


def get_stage11_venue_adapter(*, settings: Settings) -> Stage11VenueAdapter:
    venue = str(getattr(settings, "stage11_venue", "POLYMARKET_CLOB") or "POLYMARKET_CLOB").strip().upper()
    if venue != "POLYMARKET_CLOB":
        # Stage 11 v1 scope: Polymarket CLOB execution. Unknown venue falls back safely.
        return PolymarketClobAdapter(settings=settings)
    return PolymarketClobAdapter(settings=settings)
