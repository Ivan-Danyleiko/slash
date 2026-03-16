from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Market, MarketSnapshot, Platform
from app.schemas.collector import NormalizedMarketDTO


class MarketRepository:
    def __init__(self, db: Session):
        self.db = db

    def ensure_platform(self, name: str, base_url: str | None = None) -> Platform:
        platform = self.db.scalar(select(Platform).where(Platform.name == name))
        if platform:
            return platform
        platform = Platform(name=name, base_url=base_url)
        self.db.add(platform)
        self.db.flush()
        return platform

    def upsert_market(self, dto: NormalizedMarketDTO) -> tuple[Market, bool]:
        platform = self.ensure_platform(dto.platform)
        market = self.db.scalar(
            select(Market).where(
                Market.platform_id == platform.id,
                Market.external_market_id == dto.external_market_id,
            )
        )
        is_inserted = False
        if market is None:
            market = Market(platform_id=platform.id, external_market_id=dto.external_market_id, title=dto.title)
            self.db.add(market)
            is_inserted = True

        market.title = dto.title
        market.description = dto.description
        market.category = dto.category
        market.url = dto.url
        market.status = dto.status
        market.probability_yes = dto.probability_yes
        market.probability_no = dto.probability_no
        market.volume_24h = dto.volume_24h
        market.liquidity_value = dto.liquidity_value
        market.created_at = dto.created_at
        market.resolution_time = dto.resolution_time
        market.rules_text = dto.rules_text
        market.source_payload = dto.source_payload
        market.fetched_at = datetime.now(UTC)
        payload = dto.source_payload or {}
        spread_cents = payload.get("spread_cents") or payload.get("spreadCents")
        best_bid = payload.get("best_bid_yes") or payload.get("bestBidYes") or payload.get("yes_bid")
        best_ask = payload.get("best_ask_yes") or payload.get("bestAskYes") or payload.get("yes_ask")
        open_interest = payload.get("open_interest") or payload.get("openInterest") or payload.get("open_interest_fp")
        notional = payload.get("notional_value_dollars") or payload.get("notionalValueDollars")
        prev_yes_bid = payload.get("previous_yes_bid") or payload.get("previous_yes_bid_dollars")
        execution_source = payload.get("execution_source")
        is_neg_risk = payload.get("neg_risk")

        market.spread_cents = float(spread_cents) if isinstance(spread_cents, (int, float)) else None
        market.best_bid_yes = float(best_bid) if isinstance(best_bid, (int, float)) else None
        market.best_ask_yes = float(best_ask) if isinstance(best_ask, (int, float)) else None
        market.open_interest = float(open_interest) if isinstance(open_interest, (int, float)) else None
        market.notional_value_dollars = float(notional) if isinstance(notional, (int, float)) else None
        market.previous_yes_bid = float(prev_yes_bid) if isinstance(prev_yes_bid, (int, float)) else None
        market.execution_source = str(execution_source)[:32] if execution_source is not None else None
        market.is_neg_risk = bool(is_neg_risk) if isinstance(is_neg_risk, bool) else None

        self.db.flush()
        self.db.add(
            MarketSnapshot(
                market_id=market.id,
                probability_yes=market.probability_yes,
                probability_no=market.probability_no,
                volume_24h=market.volume_24h,
                liquidity_value=market.liquidity_value,
            )
        )
        return market, is_inserted
