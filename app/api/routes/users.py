from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import AccessLevel
from app.models.models import SubscriptionPlan, User

router = APIRouter(tags=["users"])


@router.get("/me")
def me(x_user_id: str = Header(default="demo-user"), db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.telegram_user_id == x_user_id))
    if not user:
        user = User(telegram_user_id=x_user_id, username="api-user", access_level=AccessLevel.FREE)
        db.add(user)
        db.commit()
        db.refresh(user)
    return {
        "id": user.id,
        "telegram_user_id": user.telegram_user_id,
        "username": user.username,
        "access_level": user.access_level.value,
        "subscription_status": user.subscription_status.value,
    }


@router.get("/plans")
def plans(db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.id.asc())))
    return [{"code": p.code.value, "name": p.name, "daily_signal_limit": p.daily_signal_limit} for p in rows]
