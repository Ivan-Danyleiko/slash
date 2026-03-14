from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.enums import AccessLevel
from app.models.models import Platform, SubscriptionPlan


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        for code, name, limit in [
            (AccessLevel.FREE, "Free", settings.free_plan_daily_signals),
            (AccessLevel.PRO, "Pro", settings.pro_plan_daily_signals),
            (AccessLevel.PREMIUM, "Premium", settings.premium_plan_daily_signals),
        ]:
            existing = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code))
            if not existing:
                db.add(SubscriptionPlan(code=code, name=name, daily_signal_limit=limit))

        for platform in ["MANIFOLD", "METACULUS", "POLYMARKET"]:
            existing_platform = db.scalar(select(Platform).where(Platform.name == platform))
            if not existing_platform:
                db.add(Platform(name=platform))

        db.commit()
        print("Seed complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
