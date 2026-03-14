from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.models import Market, Platform
from app.services.collectors.sync_service import CollectorSyncService


def _count_by_platform(db: Session, platform_name: str) -> int:
    stmt = (
        select(func.count())
        .select_from(Market)
        .join(Platform, Platform.id == Market.platform_id)
        .where(Platform.name == platform_name)
    )
    return int(db.scalar(stmt) or 0)


def main() -> None:
    db = SessionLocal()
    try:
        service = CollectorSyncService(db)
        for p in ["manifold", "metaculus"]:
            before = _count_by_platform(db, p.upper())
            result = service.sync_all(platform=p)
            after = _count_by_platform(db, p.upper())
            print(f"{p}: before={before}, after={after}, saved_delta={after - before}")
            print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
