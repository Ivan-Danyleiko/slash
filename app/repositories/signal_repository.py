from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Signal


class SignalRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_signals(self, limit: int = 50, offset: int = 0) -> list[Signal]:
        stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.scalars(stmt))

    def top_signals(self, limit: int = 10) -> list[Signal]:
        stmt = select(Signal).order_by(Signal.confidence_score.desc().nullslast()).limit(limit)
        return list(self.db.scalars(stmt))
