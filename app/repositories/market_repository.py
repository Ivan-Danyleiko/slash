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
