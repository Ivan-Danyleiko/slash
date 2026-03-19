from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Stage17TailPosition


def _category_limit_map(settings: Settings) -> dict[str, float]:
    return {
        "crypto": float(settings.signal_tail_category_limit_crypto),
        "crypto_level": float(settings.signal_tail_category_limit_crypto),
        "natural_disaster": float(settings.signal_tail_category_limit_disasters),
        "political_stability": float(settings.signal_tail_category_limit_geopolitics),
        "sports_outcome": float(settings.signal_tail_category_limit_sports),
        "regulatory": float(settings.signal_tail_category_limit_regulatory),
        "zero_event": float(settings.signal_tail_category_limit_zero_event),
    }


def _lock_open_positions_if_supported(db: Session) -> None:
    bind = getattr(db, "bind", None)
    if bind is None:
        try:
            bind = db.connection().engine
        except Exception:  # noqa: BLE001
            bind = None
    if bind is None:
        return
    dialect = str(getattr(bind.dialect, "name", "")).lower()
    if dialect not in {"postgresql"}:
        return
    _ = list(
        db.scalars(
            select(Stage17TailPosition.id)
            .where(Stage17TailPosition.status == "OPEN")
            .with_for_update()
        )
    )


def check_tail_circuit_breaker(
    db: Session,
    *,
    settings: Settings,
    balance_usd: float,
    api_status: dict[str, Any] | None = None,
    lock_open_rows: bool = False,
) -> tuple[bool, str]:
    if lock_open_rows:
        _lock_open_positions_if_supported(db)
    raw_budget_pct = float(settings.signal_tail_budget_pct)
    if not math.isfinite(raw_budget_pct):
        return True, "tail_budget_config_invalid"
    budget_pct = max(0.0, raw_budget_pct)
    if budget_pct <= 0.0:
        return True, "tail_budget_disabled"
    budget_total = float(balance_usd) * budget_pct
    if not math.isfinite(budget_total) or budget_total <= 0.0:
        return True, "tail_budget_config_invalid"
    used = float(
        db.scalar(
            select(func.coalesce(func.sum(Stage17TailPosition.notional_usd), 0.0)).where(
                Stage17TailPosition.status == "OPEN"
            )
        )
        or 0.0
    )
    used_pct = used / max(1e-9, budget_total) if budget_total > 0 else 0.0
    if used_pct >= 1.0:
        return True, f"tail_budget_exhausted:{used_pct:.2%}"

    now = datetime.now(UTC)
    max_losses = max(1, int(settings.signal_tail_circuit_breaker_consecutive_losses))
    cooldown_h = max(1, int(settings.signal_tail_circuit_breaker_cooldown_hours))
    recent_closed = list(
        db.scalars(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.status == "CLOSED")
            .where(Stage17TailPosition.closed_at.is_not(None))
            .where(Stage17TailPosition.closed_at >= (now - timedelta(hours=24)))
            .order_by(Stage17TailPosition.closed_at.desc())
            .limit(max_losses)
        )
    )
    if len(recent_closed) >= max_losses and all(float(p.realized_pnl_usd or 0.0) < 0 for p in recent_closed):
        latest_loss_ts = recent_closed[0].closed_at
        if latest_loss_ts is not None:
            ref = latest_loss_ts if latest_loss_ts.tzinfo else latest_loss_ts.replace(tzinfo=UTC)
            if now < (ref + timedelta(hours=cooldown_h)):
                return True, f"tail_consecutive_losses_{max_losses}_cooldown_{cooldown_h}h"

    api = api_status or {}
    if bool(api.get("degraded")):
        return True, "tail_external_api_degraded"

    return False, "ok"


def can_open_tail_by_category(
    db: Session,
    *,
    settings: Settings,
    category: str,
    notional_usd: float,
    balance_usd: float,
    lock_open_rows: bool = False,
) -> tuple[bool, str]:
    if lock_open_rows:
        _lock_open_positions_if_supported(db)
    limits = _category_limit_map(settings)
    cap_pct = limits.get(str(category), 0.01)
    portfolio_total = float(balance_usd)
    if portfolio_total <= 0:
        return False, "tail_portfolio_total_zero"

    used = float(
        db.scalar(
            select(func.coalesce(func.sum(Stage17TailPosition.notional_usd), 0.0))
            .where(Stage17TailPosition.status == "OPEN")
            .where(Stage17TailPosition.tail_category == str(category))
        )
        or 0.0
    )
    next_used_pct = (used + float(notional_usd)) / portfolio_total
    if next_used_pct > cap_pct:
        return False, f"tail_category_limit:{category}:{next_used_pct:.2%}>{cap_pct:.2%}"
    return True, "ok"
