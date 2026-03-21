from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.models import Market


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def is_market_resolved(market: "Market", *, now: datetime | None = None) -> bool:
    """
    Canonical single-source-of-truth for 'is this market resolved?'
    Handles Kalshi settlement_timer_seconds, all provider status strings,
    and payload boolean flags.
    """
    now_ref = now or datetime.now(UTC)
    payload = market.source_payload if isinstance(market.source_payload, dict) else {}
    status = str(market.status or "").strip().lower()

    # Kalshi: "closed" ≠ settled until outcome fields are present
    if "closed" in status and "settled" not in status:
        settlement_timer = payload.get("settlement_timer_seconds")
        has_outcome = any(
            payload.get(k) is not None
            for k in ("resolution", "resolvedOutcome", "outcome", "result", "resolutionProbability")
        )
        if settlement_timer is not None and not has_outcome:
            return False

    resolution_time_utc = _as_utc(market.resolution_time)
    if resolution_time_utc and resolution_time_utc <= now_ref:
        return True

    if any(token in status for token in ("resolved", "settled", "final", "ended")):
        return True
    if "closed" in status:
        # "closed" without outcome evidence is not yet resolved (market may still be settling).
        has_outcome = any(
            payload.get(k) is not None
            for k in ("resolution", "resolvedOutcome", "outcome", "result", "resolutionProbability")
        )
        return has_outcome

    if isinstance(payload.get("isResolved"), bool) and payload["isResolved"]:
        return True
    if isinstance(payload.get("resolved"), bool) and payload["resolved"]:
        return True

    return False


def extract_resolved_probability(market: "Market") -> float | None:
    """
    Extract final YES probability (0.0–1.0) from a resolved market's payload.
    Returns None if outcome cannot be determined.
    """
    payload = market.source_payload if isinstance(market.source_payload, dict) else {}

    for key in ("resolutionProbability", "resolved_probability", "finalProbability"):
        value = payload.get(key)
        if isinstance(value, (float, int)):
            return float(value)

    for key in ("resolution", "resolvedOutcome", "outcome", "result"):
        value = payload.get(key)
        if value in (True, 1, "1", "YES", "Yes", "yes"):
            return 1.0
        if value in (False, 0, "0", "NO", "No", "no"):
            return 0.0

    return None
