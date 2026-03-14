from datetime import datetime

from pydantic import BaseModel


class NormalizedMarketDTO(BaseModel):
    platform: str
    external_market_id: str
    title: str
    description: str | None = None
    category: str | None = None
    url: str | None = None
    status: str | None = None
    probability_yes: float | None = None
    probability_no: float | None = None
    volume_24h: float | None = None
    liquidity_value: float | None = None
    created_at: datetime | None = None
    resolution_time: datetime | None = None
    rules_text: str | None = None
    source_payload: dict | None = None
