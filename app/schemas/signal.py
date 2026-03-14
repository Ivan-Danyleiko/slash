from datetime import datetime

from pydantic import BaseModel


class SignalOut(BaseModel):
    id: int
    signal_type: str
    market_id: int
    related_market_id: int | None
    title: str
    summary: str
    confidence_score: float | None
    liquidity_score: float | None
    rules_risk_score: float | None
    divergence_score: float | None
    metadata_json: dict | None
    signal_mode: str | None
    score_breakdown_json: dict | None
    drop_reason: str | None
    execution_analysis: dict | None
    created_at: datetime
    updated_at: datetime | None
