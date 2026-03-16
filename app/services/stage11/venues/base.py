from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class Stage11PlaceRequest:
    client_id: int
    order_id: int
    market_id: int
    side: str
    notional_usd: float
    requested_price: float | None
    idempotency_key: str


@dataclass(slots=True)
class Stage11PlaceResult:
    status: str
    venue_order_id: str | None
    response_payload: dict[str, Any]
    error: str | None = None


@dataclass(slots=True)
class Stage11StatusResult:
    status: str
    response_payload: dict[str, Any]
    fill_price: float | None = None
    # cumulative filled size in USD as returned by venue, if available.
    fill_size_usd: float | None = None
    fee_usd: float | None = None
    is_partial: bool = False
    error: str | None = None


class Stage11VenueAdapter(Protocol):
    def place_order(self, req: Stage11PlaceRequest) -> Stage11PlaceResult: ...
    def cancel_order(self, venue_order_id: str) -> Stage11StatusResult: ...
    def fetch_order_status(self, venue_order_id: str) -> Stage11StatusResult: ...
