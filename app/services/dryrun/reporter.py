"""Dry-run portfolio reporter.

Builds a structured report with:
- Portfolio summary (cash, unrealized P&L, ROI)
- Statistics (win rate, avg win/loss, Kelly expectation, profit probability)
- Position list
- AI text summary
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import DryrunPortfolio, DryrunPosition, Market, Signal, Stage17TailPosition
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


def get_portfolio_snapshot(db: Session) -> dict[str, Any]:
    """Compact portfolio state for Stage7 portfolio-aware context."""
    portfolio = get_or_create_portfolio(db)
    now = datetime.now(UTC)
    open_rows = list(
        db.execute(
            select(DryrunPosition, Market)
            .join(Market, Market.id == DryrunPosition.market_id)
            .where(
                DryrunPosition.portfolio_id == portfolio.id,
                DryrunPosition.status == "OPEN",
            )
        )
    )
    open_notional = sum(float(pos.notional_usd or 0.0) for pos, _ in open_rows)
    open_pct = open_notional / max(float(portfolio.initial_balance_usd), 1.0)

    category_breakdown: dict[str, int] = {}
    bucket_breakdown: dict[str, float] = {"0_14d": 0.0, "15_45d": 0.0, "46_90d": 0.0, "91_180d": 0.0}
    for pos, market in open_rows:
        category = str(market.category or "other").strip().lower() or "other"
        category_breakdown[category] = category_breakdown.get(category, 0) + 1
        days_to_res = 999.0
        if market.resolution_time:
            base_now = now if market.resolution_time.tzinfo is not None else now.replace(tzinfo=None)
            days_to_res = max(0.0, (market.resolution_time - base_now).total_seconds() / 86400.0)
        if days_to_res <= 14:
            bucket_breakdown["0_14d"] += float(pos.notional_usd or 0.0)
        elif days_to_res <= 45:
            bucket_breakdown["15_45d"] += float(pos.notional_usd or 0.0)
        elif days_to_res <= 90:
            bucket_breakdown["46_90d"] += float(pos.notional_usd or 0.0)
        else:
            bucket_breakdown["91_180d"] += float(pos.notional_usd or 0.0)

    base = max(float(portfolio.initial_balance_usd), 1.0)
    bucket_breakdown_pct = {k: round(v / base, 4) for k, v in bucket_breakdown.items()}
    return {
        "open_positions": len(open_rows),
        "cash_usd": round(float(portfolio.current_cash_usd), 4),
        "initial_balance_usd": float(portfolio.initial_balance_usd),
        "open_positions_usd": round(open_notional, 4),
        "open_positions_pct": round(open_pct, 4),
        "category_breakdown": category_breakdown,
        "bucket_breakdown_pct": bucket_breakdown_pct,
    }


def build_tail_report(db: Session, *, days: int = 60) -> dict[str, Any]:
    """Tail-specific lightweight report for dryrun endpoint (no research package imports)."""
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    rows = list(
        db.scalars(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.opened_at >= cutoff)
            .order_by(Stage17TailPosition.opened_at.desc())
        )
    )
    closed = [r for r in rows if str(r.status or "").upper() == "CLOSED"]
    wins = [r for r in closed if float(r.realized_pnl_usd or 0.0) > 0.0]
    losses = [r for r in closed if float(r.realized_pnl_usd or 0.0) <= 0.0]
    hit_rate = (len(wins) / len(closed)) if closed else 0.0
    by_variation: dict[str, dict[str, float]] = {}
    for r in closed:
        key = str(r.tail_variation or "unknown")
        b = by_variation.setdefault(key, {"closed": 0.0, "wins": 0.0, "pnl_usd": 0.0})
        b["closed"] += 1.0
        p = float(r.realized_pnl_usd or 0.0)
        if p > 0:
            b["wins"] += 1.0
        b["pnl_usd"] += p
    for _, b in by_variation.items():
        c = float(b.get("closed") or 0.0)
        b["win_rate_tail"] = (float(b.get("wins") or 0.0) / c) if c > 0 else 0.0
    final_decision = "NO_GO_DATA_PENDING" if len(closed) < 40 else "LIMITED_GO" if hit_rate >= 0.60 else "NO_GO"
    return {
        "summary": {
            "days": int(days),
            "rows_total": len(rows),
            "closed_positions": len(closed),
            "open_positions": len([r for r in rows if str(r.status or "").upper() == "OPEN"]),
            "hit_rate_tail": round(hit_rate, 6),
            "wins": len(wins),
            "losses": len(losses),
        },
        "by_variation": by_variation,
        "final_decision": final_decision,
    }


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

    # Stage 15 metrics
    resolved_positions = [
        p for p in closed_positions if str(p.close_reason or "").startswith("resolved_")
    ]
    brier_score = 0.0
    if resolved_positions:
        errors: list[float] = []
        for p in resolved_positions:
            # Brier in YES-event coordinate system.
            # For NO positions, stored entry_price is P(NO), so convert to P(YES).
            y_pred = float(p.entry_price or 0.5)
            if str(p.direction or "YES").upper() == "NO":
                y_pred = 1.0 - y_pred
            # close_reason resolved_yes / resolved_no encodes realized YES outcome.
            y_true = 1.0 if str(p.close_reason or "") == "resolved_yes" else 0.0
            errors.append((y_pred - y_true) ** 2)
        brier_score = sum(errors) / len(errors)

    # Daily realized EV and Sharpe-like ratio
    daily_realized: dict[str, float] = {}
    for p in closed_positions:
        if p.closed_at is None:
            continue
        day = p.closed_at.date().isoformat()
        daily_realized[day] = daily_realized.get(day, 0.0) + float(p.realized_pnl_usd or 0.0)
    daily_values = list(daily_realized.values())
    daily_ev_realized = 0.0
    sharpe_like = 0.0
    if daily_values:
        daily_ev_realized = sum(daily_values) / len(daily_values)
        mean = daily_ev_realized
        var = sum((x - mean) ** 2 for x in daily_values) / max(len(daily_values), 1)
        std = math.sqrt(var)
        sharpe_like = (mean / std) if std > 0 else 0.0

    bucket_breakdown = {"0_14d": 0, "15_45d": 0, "46_90d": 0, "91_180d": 0}
    for pos, market, _ in all_positions:
        days_to_res = 999
        if market and market.resolution_time and pos.opened_at:
            days_to_res = int(max(0.0, (market.resolution_time - pos.opened_at).total_seconds() / 86400.0))
        if days_to_res <= 14:
            bucket_breakdown["0_14d"] += 1
        elif days_to_res <= 45:
            bucket_breakdown["15_45d"] += 1
        elif days_to_res <= 90:
            bucket_breakdown["46_90d"] += 1
        else:
            bucket_breakdown["91_180d"] += 1
    stats["brier_score"] = round(float(brier_score), 6)
    stats["sharpe_ratio"] = round(float(sharpe_like), 6)
    stats["realized_daily_ev_usd"] = round(float(daily_ev_realized), 6)
    stats["time_bucket_breakdown"] = bucket_breakdown

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
        "tail_report": build_tail_report(db, days=60),
        "ai_summary": _ai_summary(portfolio, stats, len(open_positions), n_closed),
        "generated_at": datetime.now(UTC).isoformat(),
    }
