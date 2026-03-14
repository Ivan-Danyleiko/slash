from datetime import datetime

from pydantic import BaseModel


class MarketOut(BaseModel):
    id: int
    platform: str
    external_market_id: str
    title: str
    description: str | None
    category: str | None
    url: str | None
    status: str | None
    probability_yes: float | None
    probability_no: float | None
    volume_24h: float | None
    liquidity_value: float | None
    created_at: datetime | None
    resolution_time: datetime | None
    fetched_at: datetime


class MarketAnalysisOut(BaseModel):
    market_id: int
    rules_risk_score: float | None
    rules_risk_level: str | None
    liquidity_score: float | None
    liquidity_level: str | None
