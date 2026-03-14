from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class PaginatedResponse(BaseModel):
    total: int
    limit: int
    offset: int


class Timestamped(BaseModel):
    created_at: datetime
