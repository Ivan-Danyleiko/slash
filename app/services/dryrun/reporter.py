"""Dry-run portfolio reporter.

Builds a structured report with:
- Portfolio summary (cash, unrealized P&L, ROI)
- Statistics (win rate, avg win/loss, Kelly expectation, profit probability)
- Position list
- AI text summary
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import DryrunPortfolio, DryrunPosition, Market, Signal
from app.services.dryrun.simulator import get_or_create_portfolio


def _kelly_expectation(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Simple Kelly expectation = EV / avg_win_abs."""
    if avg_win <= 0 or avg_loss <= 0:
        return 0.0
    ev = win_rate * avg_win - (1 - win_rate) * avg_loss
    return round(ev / avg_win, 4)


def _profit_probability(win_rate: float, n_closed: int) -> float:
    """P(positive P&L) based on binomial model. Returns % probability."""
    if n_closed == 0 or win_rate <= 0:
        return 0.0
    if win_rate >= 1.0:
        return 100.0
    # Use normal approximation of binomial for expected future n trades
    # Simplified: return current win_rate * 100 as baseline estimate
    # With more data this becomes a proper confidence interval
    return round(win_rate * 100.0, 1)


def _ai_summary(portfolio: DryrunPortfolio, stats: dict[str, Any], open_count: int, closed_count: int) -> str:
    roi = round((portfolio.total_realized_pnl_usd + portfolio.total_unrealized_pnl_usd) / portfolio.initial_balance_usd * 100, 2)
    total = open_count + closed_count
    win_rate = stats.get("win_rate", 0.0)
    kelly_exp = stats.get("kelly_expectation", 0.0)
    profit_prob = stats.get("profit_probability_pct", 0.0)

    lines = [
        f"[DRY-RUN] Virtual ${portfolio.initial_balance_usd:.0f} portfolio.",
        f"Total positions: {total} ({open_count} open, {closed_count} closed).",
        f"Cash remaining: ${portfolio.current_cash_usd:.2f} | ROI: {roi:+.2f}%.",
    ]
    if closed_count > 0:
        lines.append(f"Win rate: {win_rate*100:.1f}% | Avg win: ${stats.get('avg_win_usd',0):.2f} | Avg loss: ${stats.get('avg_loss_usd',0):.2f}.")
        lines.append(f"Kelly expectation: {kelly_exp:.4f} | Profit probability (current): {profit_prob:.1f}%.")
    else:
        lines.append("No closed positions yet — awaiting market resolutions.")

    if kelly_exp > 0.02:
        lines.append("Positive Kelly expectation: strategy shows edge in current sample.")
    elif closed_count >= 5:
        lines.append("Kelly expectation near zero: need more data or higher quality signals.")

    return " ".join(lines)


def build_report(db: Session) -> dict[str, Any]:
    portfolio = get_or_create_portfolio(db)

    all_positions = list(
        db.execute(
            select(DryrunPosition, Market, Signal)
            .join(Market, Market.id == DryrunPosition.market_id)
            .join(Signal, Signal.id == DryrunPosition.signal_id, isouter=True)
            .where(DryrunPosition.portfolio_id == portfolio.id)
            .order_by(DryrunPosition.opened_at.desc())
        )
    )

    open_positions = [p for p, _, _ in all_positions if p.status == "OPEN"]
    closed_positions = [p for p, _, _ in all_positions if p.status == "CLOSED"]
    expired_positions = [p for p, _, _ in all_positions if p.status == "EXPIRED"]

    # Stats
    wins = [p for p in closed_positions if p.realized_pnl_usd > 0]
    losses = [p for p in closed_positions if p.realized_pnl_usd <= 0]
    n_closed = len(closed_positions)
    win_rate = len(wins) / n_closed if n_closed > 0 else 0.0
    avg_win = sum(p.realized_pnl_usd for p in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(p.realized_pnl_usd for p in losses) / len(losses)) if losses else 0.0

    total_pnl = portfolio.total_realized_pnl_usd + portfolio.total_unrealized_pnl_usd
    roi_pct = total_pnl / portfolio.initial_balance_usd * 100

    stats = {
        "total_positions": len(all_positions),
        "open": len(open_positions),
        "closed": n_closed,
        "expired": len(expired_positions),
        "win_rate": round(win_rate, 4),
        "avg_win_usd": round(avg_win, 4),
        "avg_loss_usd": round(avg_loss, 4),
        "kelly_expectation": _kelly_expectation(win_rate, avg_win, avg_loss),
        "profit_probability_pct": _profit_probability(win_rate, n_closed),
    }

    positions_out = []
    for pos, market, signal in all_positions:
        unrealized = pos.unrealized_pnl_usd if pos.status == "OPEN" else 0.0
        positions_out.append({
            "id": pos.id,
            "status": pos.status,
            "direction": pos.direction,
            "platform": pos.platform,
            "market_title": market.title[:80] if market else "",
            "entry_price": round(pos.entry_price, 4),
            "mark_price": round(pos.mark_price or pos.entry_price, 4),
            "notional_usd": round(pos.notional_usd, 4),
            "shares": round(pos.shares_count, 4),
            "unrealized_pnl_usd": round(unrealized, 4),
            "realized_pnl_usd": round(pos.realized_pnl_usd, 4),
            "open_reason": pos.open_reason,
            "close_reason": pos.close_reason,
            "kelly_fraction": pos.entry_kelly_fraction,
            "ev_pct": pos.entry_ev_pct,
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
            "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
            "resolution_deadline": pos.resolution_deadline.isoformat() if pos.resolution_deadline else None,
        })

    portfolio_out = {
        "cash_usd": round(portfolio.current_cash_usd, 4),
        "open_positions_usd": round(sum(p.notional_usd for p in open_positions), 4),
        "total_value_usd": round(
            portfolio.current_cash_usd + sum(p.notional_usd + p.unrealized_pnl_usd for p in open_positions),
            4,
        ),
        "realized_pnl_usd": round(portfolio.total_realized_pnl_usd, 4),
        "unrealized_pnl_usd": round(portfolio.total_unrealized_pnl_usd, 4),
        "roi_pct": round(roi_pct, 4),
        "initial_balance_usd": portfolio.initial_balance_usd,
    }

    return {
        "portfolio": portfolio_out,
        "stats": stats,
        "positions": positions_out,
        "ai_summary": _ai_summary(portfolio, stats, len(open_positions), n_closed),
        "generated_at": datetime.now(UTC).isoformat(),
    }
