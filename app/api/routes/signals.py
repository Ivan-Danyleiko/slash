from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal
from app.repositories.signal_repository import SignalRepository
from app.schemas.signal import SignalOut
from app.services.signals.ranking import select_top_signals

router = APIRouter(prefix="/signals", tags=["signals"])


def _map(signal: Signal) -> SignalOut:
    return SignalOut(
        id=signal.id,
        signal_type=signal.signal_type.value,
        market_id=signal.market_id,
        related_market_id=signal.related_market_id,
        title=signal.title,
        summary=signal.summary,
        confidence_score=signal.confidence_score,
        liquidity_score=signal.liquidity_score,
        rules_risk_score=signal.rules_risk_score,
        divergence_score=signal.divergence_score,
        metadata_json=signal.metadata_json,
        signal_mode=signal.signal_mode,
        score_breakdown_json=signal.score_breakdown_json,
        drop_reason=signal.drop_reason,
        execution_analysis=signal.execution_analysis,
        created_at=signal.created_at,
        updated_at=signal.updated_at,
    )


@router.get("", response_model=list[SignalOut])
def list_signals(
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    signal_type: SignalType | None = Query(default=None),
    platform: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    db: Session = Depends(get_db),
) -> list[SignalOut]:
    stmt = select(Signal).join(Market, Market.id == Signal.market_id).join(Platform, Platform.id == Market.platform_id)
    if signal_type:
        stmt = stmt.where(Signal.signal_type == signal_type)
    if platform:
        stmt = stmt.where(Platform.name.ilike(platform))
    if min_confidence is not None:
        stmt = stmt.where(Signal.confidence_score.is_not(None), Signal.confidence_score >= min_confidence)
    stmt = stmt.order_by(Signal.created_at.desc()).limit(limit).offset(offset)
    rows = list(db.scalars(stmt))
    return [_map(s) for s in rows]


@router.get("/top", response_model=list[SignalOut])
def top_signals(limit: int = Query(default=10, le=100), db: Session = Depends(get_db)) -> list[SignalOut]:
    settings = get_settings()
    stmt = select(Signal).where(
        Signal.signal_type.in_(
            [
                SignalType.ARBITRAGE_CANDIDATE,
                SignalType.DIVERGENCE,
                SignalType.WEIRD_MARKET,
                SignalType.RULES_RISK,
                SignalType.DUPLICATE_MARKET,
            ]
        )
    )
    rows = list(db.scalars(stmt))
    top = select_top_signals(rows, limit=limit, settings=settings)
    return [_map(s) for s in top]


@router.get("/latest", response_model=list[SignalOut])
def latest_signals(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    signal_type: SignalType | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[SignalOut]:
    stmt = select(Signal).order_by(Signal.created_at.desc())
    if signal_type:
        stmt = stmt.where(Signal.signal_type == signal_type)
    offset = (page - 1) * page_size
    rows = list(db.scalars(stmt.offset(offset).limit(page_size)))
    return [_map(s) for s in rows]


@router.get("/{signal_id}", response_model=SignalOut)
def get_signal(signal_id: int, db: Session = Depends(get_db)) -> SignalOut:
    signal = db.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return _map(signal)


@router.get("/{signal_id}/why")
def explain_signal(signal_id: int, db: Session = Depends(get_db)) -> dict:
    signal = db.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return {
        "id": signal.id,
        "signal_type": signal.signal_type.value,
        "signal_mode": signal.signal_mode,
        "title": signal.title,
        "summary": signal.summary,
        "score_breakdown_json": signal.score_breakdown_json,
        "metadata_json": signal.metadata_json,
        "execution_analysis": signal.execution_analysis,
        "created_at": signal.created_at,
        "updated_at": signal.updated_at,
    }
