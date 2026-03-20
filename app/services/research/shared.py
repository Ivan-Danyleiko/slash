from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import SignalHistory


def resolved_success_map(db: Session, signal_ids: list[int]) -> dict[int, bool | None]:
    """
    Return {signal_id: resolved_success} for the given signal IDs.
    Only includes signals that have a non-null resolved_success.
    Latest record per signal_id wins (ordered by id desc).
    """
    if not signal_ids:
        return {}
    rows = list(
        db.execute(
            select(SignalHistory.signal_id, SignalHistory.resolved_success)
            .where(SignalHistory.signal_id.in_(signal_ids))
            .where(SignalHistory.resolved_success.is_not(None))
            .order_by(SignalHistory.id.desc())
        )
    )
    out: dict[int, bool | None] = {}
    for sid, success in rows:
        key = int(sid or 0)
        if key and key not in out:
            out[key] = bool(success) if success is not None else None
    return out
