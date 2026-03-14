from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import LiquidityAnalysis, Market, Platform, RulesAnalysis
from app.schemas.market import MarketAnalysisOut, MarketOut
from app.services.telegram_product import TelegramProductService

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=list[MarketOut])
def list_markets(
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[MarketOut]:
    stmt = select(Market, Platform).join(Platform, Platform.id == Market.platform_id)
    if platform:
        stmt = stmt.where(Platform.name.ilike(platform))
    if status:
        stmt = stmt.where(Market.status.ilike(status))
    if category:
        stmt = stmt.where(Market.category.ilike(category))
    stmt = stmt.order_by(Market.id.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).all()
    result: list[MarketOut] = []
    for m, p in rows:
        result.append(
            MarketOut(
                id=m.id,
                platform=p.name if p else "UNKNOWN",
                external_market_id=m.external_market_id,
                title=m.title,
                description=m.description,
                category=m.category,
                url=m.url,
                status=m.status,
                probability_yes=m.probability_yes,
                probability_no=m.probability_no,
                volume_24h=m.volume_24h,
                liquidity_value=m.liquidity_value,
                created_at=m.created_at,
                resolution_time=m.resolution_time,
                fetched_at=m.fetched_at,
            )
        )
    return result


@router.get("/{market_id}", response_model=MarketOut)
def get_market(
    market_id: int,
    x_user_id: str | None = Header(default=None),
    x_username: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> MarketOut:
    m = db.get(Market, market_id)
    if not m:
        raise HTTPException(status_code=404, detail="Market not found")
    if x_user_id:
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(telegram_user_id=x_user_id, username=x_username)
        svc.record_market_opened(user, market_id)
    platform = db.scalar(select(Platform).where(Platform.id == m.platform_id))
    return MarketOut(
        id=m.id,
        platform=platform.name if platform else "UNKNOWN",
        external_market_id=m.external_market_id,
        title=m.title,
        description=m.description,
        category=m.category,
        url=m.url,
        status=m.status,
        probability_yes=m.probability_yes,
        probability_no=m.probability_no,
        volume_24h=m.volume_24h,
        liquidity_value=m.liquidity_value,
        created_at=m.created_at,
        resolution_time=m.resolution_time,
        fetched_at=m.fetched_at,
    )


@router.get("/{market_id}/analysis", response_model=MarketAnalysisOut)
def market_analysis(market_id: int, db: Session = Depends(get_db)) -> MarketAnalysisOut:
    market = db.get(Market, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    rules = db.scalar(select(RulesAnalysis).where(RulesAnalysis.market_id == market_id).order_by(RulesAnalysis.id.desc()))
    liq = db.scalar(select(LiquidityAnalysis).where(LiquidityAnalysis.market_id == market_id).order_by(LiquidityAnalysis.id.desc()))
    return MarketAnalysisOut(
        market_id=market_id,
        rules_risk_score=rules.score if rules else None,
        rules_risk_level=rules.level if rules else None,
        liquidity_score=liq.score if liq else None,
        liquidity_level=liq.level if liq else None,
    )
