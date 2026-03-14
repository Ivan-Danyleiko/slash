from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.telegram_product import TelegramProductService

router = APIRouter(tags=["product"])


def _user_context(db: Session, x_user_id: str, x_username: str | None = None):
    svc = TelegramProductService(db)
    return svc, svc.get_or_create_user(telegram_user_id=x_user_id, username=x_username)


@router.get("/watchlist")
def watchlist(
    x_user_id: str = Header(default="demo-user"),
    x_username: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    svc, user = _user_context(db, x_user_id, x_username)
    return {"items": svc.list_watchlist(user)}


@router.post("/watchlist/add")
def watchlist_add(
    market_id: int,
    x_user_id: str = Header(default="demo-user"),
    x_username: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    svc, user = _user_context(db, x_user_id, x_username)
    ok, msg = svc.add_watchlist(user, market_id)
    return {"ok": ok, "message": msg}


@router.post("/watchlist/remove")
def watchlist_remove(
    market_id: int,
    x_user_id: str = Header(default="demo-user"),
    x_username: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    svc, user = _user_context(db, x_user_id, x_username)
    ok = svc.remove_watchlist(user, market_id)
    return {"ok": ok}


@router.get("/digest")
def digest(
    x_user_id: str = Header(default="demo-user"),
    x_username: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    svc, user = _user_context(db, x_user_id, x_username)
    return {"text": svc.daily_digest(user)}


@router.get("/user")
def user(
    x_user_id: str = Header(default="demo-user"),
    x_username: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    svc, user = _user_context(db, x_user_id, x_username)
    return {
        "telegram_user_id": user.telegram_user_id,
        "username": user.username,
        "plan": user.access_level.value,
        "signals_sent_today": user.signals_sent_today,
        "last_digest_sent": user.last_digest_sent,
    }
