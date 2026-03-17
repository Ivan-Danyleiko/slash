"""Dry-run paper trading simulator.

Virtual $100 account. Opens positions on ARBITRAGE_CANDIDATE signals where
AI agent (Stage7) says KEEP. Stage8 live-trading gate is bypassed for paper
trading — positions sized by Kelly criterion, clamped to [3%, 5%] of cash.
No real orders are sent — purely simulated P&L tracking.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import SignalType
from app.models.models import (
    DryrunPortfolio,
    DryrunPosition,
    Market,
    Signal,
    Stage7AgentDecision,
)
from app.utils.http import retry_request

logger = logging.getLogger(__name__)

PORTFOLIO_NAME = "default"
INITIAL_BALANCE = 100.0
MIN_POSITION_PCT = 0.03
MAX_POSITION_PCT = 0.05
MIN_NOTIONAL_USD = 1.0
STOP_LOSS_RATIO = 0.50  # close if mark_price < entry * 0.50


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------


def get_or_create_portfolio(db: Session) -> DryrunPortfolio:
    row = db.scalar(select(DryrunPortfolio).where(DryrunPortfolio.name == PORTFOLIO_NAME).limit(1))
    if row is not None:
        return row
    row = DryrunPortfolio(
        name=PORTFOLIO_NAME,
        initial_balance_usd=INITIAL_BALANCE,
        current_cash_usd=INITIAL_BALANCE,
    )
    db.add(row)
    db.flush()
    return row


def reset_portfolio(db: Session) -> DryrunPortfolio:
    """Close all open positions and reset cash to $100."""
    portfolio = get_or_create_portfolio(db)
    now = datetime.now(UTC)
    open_positions = list(
        db.scalars(
            select(DryrunPosition).where(
                DryrunPosition.portfolio_id == portfolio.id,
                DryrunPosition.status == "OPEN",
            )
        )
    )
    for pos in open_positions:
        pos.status = "CLOSED"
        pos.close_reason = "portfolio_reset"
        pos.realized_pnl_usd = 0.0
        pos.closed_at = now
        pos.updated_at = now
    portfolio.current_cash_usd = INITIAL_BALANCE
    portfolio.total_realized_pnl_usd = 0.0
    portfolio.total_unrealized_pnl_usd = 0.0
    portfolio.updated_at = now
    db.flush()
    return portfolio


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


def _compute_position_pct(kelly: float, ev_pct: float) -> float | None:
    """Return position fraction [3%, 5%] or None to skip."""
    if ev_pct <= 0.0:
        return None
    if kelly > 0.0:
        return max(MIN_POSITION_PCT, min(MAX_POSITION_PCT, kelly))
    # Weak signal but positive EV → use minimum
    if ev_pct >= 0.02:
        return MIN_POSITION_PCT
    return None


# ---------------------------------------------------------------------------
# CLOB price fetch (reuse logic from PolymarketCollector)
# ---------------------------------------------------------------------------


def _fetch_clob_mid(token_id: str) -> float | None:
    settings = get_settings()
    url = f"{settings.polymarket_clob_api_base_url}/book"
    try:
        resp = retry_request(
            lambda: httpx.get(url, params={"token_id": token_id}, timeout=8.0),
            retries=2,
            backoff_seconds=0.5,
            platform="POLYMARKET",
        )
        if resp.status_code != 200:
            return None
        payload = resp.json() or {}
        bids = payload.get("bids") if isinstance(payload, dict) else None
        asks = payload.get("asks") if isinstance(payload, dict) else None
        bid, ask = None, None
        if isinstance(bids, list) and bids:
            best = bids[-1]
            if isinstance(best, dict):
                for k in ("price", "p"):
                    v = best.get(k)
                    if v is not None:
                        try:
                            bid = float(v)
                        except (TypeError, ValueError):
                            pass
                        break
        if isinstance(asks, list) and asks:
            best = asks[-1]
            if isinstance(best, dict):
                for k in ("price", "p"):
                    v = best.get(k)
                    if v is not None:
                        try:
                            ask = float(v)
                        except (TypeError, ValueError):
                            pass
                        break
        if bid is not None and ask is not None and ask >= bid:
            return (bid + ask) / 2.0
        return bid  # fallback: just bid
    except Exception:  # noqa: BLE001
        return None


def _get_token_id(market: Market) -> str | None:
    payload: dict[str, Any] = market.source_payload or {}
    for key in ("clobTokenId", "clob_token_id", "token_id", "yes_token_id", "yesTokenId"):
        v = payload.get(key)
        if v and str(v).strip():
            return str(v).strip()
    token_ids = payload.get("clobTokenIds") or payload.get("clob_token_ids")
    if isinstance(token_ids, list) and token_ids:
        return str(token_ids[0]).strip()
    return None


# ---------------------------------------------------------------------------
# Main simulation cycle
# ---------------------------------------------------------------------------


def run_simulation_cycle(db: Session) -> dict[str, Any]:
    """Scan new KEEP signals and open paper positions. Returns summary.

    Entry criteria (dry-run / paper trading):
      - Stage7 decision = KEEP  (AI agent approved)
      - Signal type = ARBITRAGE_CANDIDATE
      - Market has best_ask_yes price
      Stage8 EXECUTE_ALLOWED is NOT required — Stage8 is a live-trading safety
      gate; for paper trading we bypass it and use Stage7 directly.
    """
    portfolio = get_or_create_portfolio(db)
    opened = 0
    skipped = 0
    reasons: list[str] = []

    # Stage7 KEEP + valid market price — Stage8 not required for paper trading
    rows = list(
        db.execute(
            select(Signal, Stage7AgentDecision, Market)
            .join(Stage7AgentDecision, Stage7AgentDecision.signal_id == Signal.id)
            .join(Market, Market.id == Signal.market_id)
            .where(
                Signal.signal_type == SignalType.ARBITRAGE_CANDIDATE,
                Stage7AgentDecision.decision == "KEEP",
                Market.best_ask_yes.is_not(None),
            )
            .order_by(Stage7AgentDecision.created_at.desc())
            .limit(50)
        )
    )

    # Collect already-open market IDs to avoid duplicate positions
    open_market_ids: set[int] = set(
        db.scalars(
            select(DryrunPosition.market_id).where(
                DryrunPosition.portfolio_id == portfolio.id,
                DryrunPosition.status == "OPEN",
            )
        )
    )

    now = datetime.now(UTC)

    for signal, s7, market in rows:
        if market.id in open_market_ids:
            skipped += 1
            continue

        direction = str(signal.signal_direction or "YES").upper()
        if direction not in ("YES", "NO"):
            direction = "YES"

        ev_bundle: dict[str, Any] = s7.evidence_bundle or {}
        kelly = float(ev_bundle.get("kelly_fraction") or signal.confidence_score or 0.0)
        ev_pct = float(ev_bundle.get("expected_ev_pct") or signal.divergence_score or 0.0)

        # Fallback: use confidence_score as EV proxy so we always open something
        if ev_pct <= 0.0 and signal.confidence_score:
            ev_pct = float(signal.confidence_score) * 0.05  # conservative proxy

        position_pct = _compute_position_pct(kelly, ev_pct)
        if position_pct is None:
            # Last resort: always allocate min size for dry-run learning
            position_pct = MIN_POSITION_PCT

        notional = portfolio.current_cash_usd * position_pct
        if notional < MIN_NOTIONAL_USD:
            skipped += 1
            reasons.append(f"signal {signal.id}: notional ${notional:.2f} < min ${MIN_NOTIONAL_USD}")
            continue

        entry_price = float(market.best_ask_yes) if direction == "YES" else (1.0 - float(market.best_ask_yes))
        if entry_price <= 0.0 or entry_price >= 1.0:
            skipped += 1
            continue

        shares = notional / entry_price

        pos = DryrunPosition(
            portfolio_id=portfolio.id,
            signal_id=signal.id,
            market_id=market.id,
            platform=(market.source_payload or {}).get("platform") or "POLYMARKET",
            direction=direction,
            entry_price=entry_price,
            mark_price=entry_price,
            notional_usd=notional,
            shares_count=shares,
            status="OPEN",
            open_reason=f"kelly={kelly:.4f},ev={ev_pct:.4f},stage7=KEEP",
            entry_kelly_fraction=kelly,
            entry_ev_pct=ev_pct,
            unrealized_pnl_usd=0.0,
            resolution_deadline=market.resolution_time,
        )
        db.add(pos)
        portfolio.current_cash_usd -= notional
        open_market_ids.add(market.id)
        opened += 1

    portfolio.updated_at = now
    db.flush()

    return {
        "opened": opened,
        "skipped": skipped,
        "cash_remaining_usd": round(portfolio.current_cash_usd, 4),
        "skip_reasons": reasons[:10],
    }


# ---------------------------------------------------------------------------
# Mark-to-market refresh
# ---------------------------------------------------------------------------


def refresh_mark_prices(db: Session) -> dict[str, Any]:
    """Fetch current CLOB prices for all OPEN positions and update unrealized P&L."""
    portfolio = get_or_create_portfolio(db)
    open_positions = list(
        db.scalars(
            select(DryrunPosition)
            .where(DryrunPosition.portfolio_id == portfolio.id, DryrunPosition.status == "OPEN")
        )
    )

    updated = 0
    stop_loss_closed = 0
    expired_closed = 0
    now = datetime.now(UTC)
    total_unrealized = 0.0

    for pos in open_positions:
        market = db.get(Market, pos.market_id)
        if market is None:
            continue

        # Check expiry
        if pos.resolution_deadline and now > pos.resolution_deadline:
            mark = pos.mark_price or pos.entry_price
            pos.realized_pnl_usd = (mark - pos.entry_price) * pos.shares_count
            portfolio.current_cash_usd += pos.notional_usd + pos.realized_pnl_usd
            portfolio.total_realized_pnl_usd += pos.realized_pnl_usd
            pos.unrealized_pnl_usd = 0.0
            pos.status = "EXPIRED"
            pos.close_reason = "expired"
            pos.closed_at = now
            pos.updated_at = now
            expired_closed += 1
            continue

        # Fetch live mark price from CLOB
        token_id = _get_token_id(market)
        new_mark: float | None = None
        if token_id:
            new_mark = _fetch_clob_mid(token_id)

        if new_mark is None:
            # fallback: use market.probability_yes as mark
            new_mark = market.probability_yes

        if new_mark is None or new_mark <= 0.0:
            total_unrealized += pos.unrealized_pnl_usd
            continue

        if pos.direction == "NO":
            new_mark = 1.0 - new_mark

        pos.mark_price = new_mark
        pos.unrealized_pnl_usd = (new_mark - pos.entry_price) * pos.shares_count
        pos.updated_at = now
        updated += 1
        total_unrealized += pos.unrealized_pnl_usd

        # Stop-loss check
        if new_mark < pos.entry_price * STOP_LOSS_RATIO:
            pos.realized_pnl_usd = pos.unrealized_pnl_usd
            portfolio.current_cash_usd += pos.notional_usd + pos.realized_pnl_usd
            portfolio.total_realized_pnl_usd += pos.realized_pnl_usd
            pos.status = "CLOSED"
            pos.close_reason = "stop_loss"
            pos.closed_at = now
            pos.updated_at = now
            stop_loss_closed += 1
            total_unrealized -= pos.unrealized_pnl_usd  # already realized
            pos.unrealized_pnl_usd = 0.0

    portfolio.total_unrealized_pnl_usd = total_unrealized
    portfolio.updated_at = now
    db.flush()

    return {
        "prices_updated": updated,
        "stop_loss_closed": stop_loss_closed,
        "expired_closed": expired_closed,
        "total_unrealized_usd": round(total_unrealized, 4),
    }


# ---------------------------------------------------------------------------
# Resolution check
# ---------------------------------------------------------------------------


def check_resolutions(db: Session) -> dict[str, Any]:
    """Close positions for markets that Gamma API has marked as resolved."""
    portfolio = get_or_create_portfolio(db)
    open_positions = list(
        db.scalars(
            select(DryrunPosition).where(
                DryrunPosition.portfolio_id == portfolio.id,
                DryrunPosition.status == "OPEN",
            )
        )
    )

    resolved_count = 0
    now = datetime.now(UTC)

    for pos in open_positions:
        market = db.get(Market, pos.market_id)
        if market is None:
            continue
        status = str(market.status or "").lower()
        if status not in ("resolved", "closed", "cancelled"):
            continue

        # Determine resolution value from source_payload
        sp: dict[str, Any] = market.source_payload or {}
        res_val = sp.get("resolutionValue") or sp.get("resolution_value")

        if res_val is not None:
            try:
                res_float = float(res_val)
                won = (pos.direction == "YES" and res_float >= 0.5) or (
                    pos.direction == "NO" and res_float < 0.5
                )
            except (TypeError, ValueError):
                won = str(res_val).lower() in ("yes", "1", "true") if pos.direction == "YES" else str(res_val).lower() in ("no", "0", "false")
        else:
            # Cancelled / no resolution value — return capital
            pos.realized_pnl_usd = 0.0
            portfolio.current_cash_usd += pos.notional_usd
            pos.status = "CLOSED"
            pos.close_reason = "cancelled"
            pos.closed_at = now
            pos.updated_at = now
            portfolio.updated_at = now
            resolved_count += 1
            continue

        if won:
            # Payout: shares * $1 (binary market resolves to $1)
            pnl = pos.shares_count * 1.0 - pos.notional_usd
            close_reason = "resolved_yes" if pos.direction == "YES" else "resolved_no"
        else:
            pnl = -pos.notional_usd  # lose entire stake
            close_reason = "resolved_no" if pos.direction == "YES" else "resolved_yes"

        pos.realized_pnl_usd = pnl
        pos.unrealized_pnl_usd = 0.0
        portfolio.current_cash_usd += pos.notional_usd + pnl
        portfolio.total_realized_pnl_usd += pnl
        pos.status = "CLOSED"
        pos.close_reason = close_reason
        pos.closed_at = now
        pos.updated_at = now
        portfolio.updated_at = now
        resolved_count += 1

    db.flush()
    return {"resolved_closed": resolved_count}
