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

import json
from urllib import request as urllib_request

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
STOP_LOSS_RATIO = 0.50        # close if mark drops 50% from entry
TAKE_PROFIT_RATIO = 0.65      # close when 65% of max gain is captured
TIME_EXIT_DAYS = 14           # exit if held this many days with low EV
TIME_EXIT_MIN_EV = 0.03       # EV threshold below which time-exit triggers

# Hard limits — never enter regardless of LLM opinion
HARD_MAX_DAYS = 180
HARD_MIN_VOLUME = 5_000
HARD_MAX_SPREAD = 0.08

# Soft limits — LLM reviews borderline cases that violate these but not hard limits
SOFT_MAX_DAYS = 90
SOFT_MIN_VOLUME = 50_000
SOFT_MAX_SPREAD = 0.04

# LLM-reviewed candidates: need EV/day above this to even bother asking LLM
LLM_MIN_DAILY_EV = 0.0005    # 0.05% per day minimum to consider borderline


# ---------------------------------------------------------------------------
# LLM borderline review
# ---------------------------------------------------------------------------

_LLM_SYSTEM = (
    "You are a prediction market position filter. "
    "Given borderline trading candidates that failed soft mechanical filters, "
    "decide which are still worth entering based on risk/reward. "
    "Reply ONLY with a JSON array of signal_ids to APPROVE. Example: [1, 3]. "
    "Empty array [] means reject all. No explanation needed."
)


def _llm_review_borderline(candidates: list[dict[str, Any]]) -> set[int]:
    """Ask LLM to review borderline candidates that failed soft filters.

    Each candidate has: signal_id, title, ev_pct, daily_ev, days_to_res,
    volume_usd, spread_pct, filter_reason.
    Returns set of signal IDs approved to enter despite filter violation.
    """
    if not candidates:
        return set()

    settings = get_settings()

    # Pick first available LLM provider
    providers = []
    if settings.groq_api_key:
        providers.append(("https://api.groq.com/openai/v1", settings.groq_api_key, settings.stage7_groq_model))
    if settings.gemini_api_key:
        providers.append((
            "https://generativelanguage.googleapis.com/v1beta/openai",
            settings.gemini_api_key,
            settings.stage7_gemini_model,
        ))
    if settings.stage7_openai_api_key:
        providers.append((settings.stage7_openai_api_base_url, settings.stage7_openai_api_key, settings.stage7_openai_model))
    if not providers:
        return set()

    prompt_items = [
        {
            "signal_id": c["signal_id"],
            "title": c["title"][:80],
            "ev_pct": round(c["ev_pct"] * 100, 1),
            "daily_ev_pct": round(c["daily_ev"] * 100, 3),
            "days_to_resolution": round(c["days_to_res"], 0),
            "volume_usd": round(c["volume_usd"], 0),
            "spread_pct": round(c["spread_pct"] * 100, 1),
            "filter_violated": c["filter_reason"],
        }
        for c in candidates
    ]

    for api_base, api_key, model in providers:
        try:
            body = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM},
                    {"role": "user", "content": json.dumps(prompt_items)},
                ],
                "temperature": 0,
                "max_tokens": 200,
            }, ensure_ascii=True).encode("utf-8")
            req = urllib_request.Request(
                f"{api_base.rstrip('/')}/chat/completions",
                data=body,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            with urllib_request.urlopen(req, timeout=15.0) as resp:  # nosec B310
                result = json.loads(resp.read().decode("utf-8"))
            text = ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            # Parse JSON array from response
            import re
            match = re.search(r"\[[\d\s,]*\]", text)
            if match:
                approved_ids = json.loads(match.group(0))
                logger.info("LLM borderline review approved %d/%d: %s", len(approved_ids), len(candidates), approved_ids)
                return set(int(x) for x in approved_ids if isinstance(x, (int, float)))
            return set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM borderline review failed (%s): %s", api_base, exc)
            continue

    return set()


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
# Shared candidate scanning logic
# ---------------------------------------------------------------------------


def _scan_signal_candidates(db: Session) -> dict[str, Any]:
    """Scan all Stage7-KEEP signals and classify them without touching DB.

    Returns dict with:
      accepted      — list of dicts ready to open
      borderline    — list sent to LLM (with filter_reason)
      llm_approved  — set of signal_ids LLM approved from borderline
      hard_rejected — list of {signal_id, title, reason}
      soft_rejected — list of {signal_id, title, reason}
      duplicates    — count already-open markets
    """
    portfolio = get_or_create_portfolio(db)
    now = datetime.now(UTC)

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
            .limit(100)
        )
    )

    open_market_ids: set[int] = set(
        db.scalars(
            select(DryrunPosition.market_id).where(
                DryrunPosition.portfolio_id == portfolio.id,
                DryrunPosition.status == "OPEN",
            )
        )
    )

    accepted: list[dict[str, Any]] = []
    borderline: list[dict[str, Any]] = []
    borderline_map: dict[int, dict[str, Any]] = {}
    hard_rejected: list[dict[str, Any]] = []
    soft_rejected: list[dict[str, Any]] = []
    duplicates = 0

    for signal, s7, market in rows:
        if market.id in open_market_ids:
            duplicates += 1
            continue

        spread_pct = 0.0
        if market.best_bid_yes is not None and market.best_ask_yes is not None and market.best_ask_yes > 0:
            spread_pct = (float(market.best_ask_yes) - float(market.best_bid_yes)) / float(market.best_ask_yes)
        elif market.spread_cents is not None:
            mid = ((market.best_bid_yes or 0) + (market.best_ask_yes or 0)) / 2 or 0.5
            spread_pct = float(market.spread_cents) / 100.0 / mid

        volume = float(market.notional_value_dollars or market.liquidity_value or 0.0)
        days_to_res = 999.0
        if market.resolution_time:
            days_to_res = max(0.5, (market.resolution_time - now).total_seconds() / 86400.0)

        ev_bundle: dict[str, Any] = s7.evidence_bundle or {}
        kelly = float(ev_bundle.get("kelly_fraction") or signal.confidence_score or 0.0)
        ev_pct = float(ev_bundle.get("expected_ev_pct") or signal.divergence_score or 0.0)
        if ev_pct <= 0.0 and signal.confidence_score:
            ev_pct = float(signal.confidence_score) * 0.05
        daily_ev = ev_pct / max(1.0, days_to_res)

        entry_price = float(market.best_ask_yes)
        koef = round(1.0 / entry_price, 2) if entry_price > 0 else 0.0
        max_win_pct = round((1.0 - entry_price) / entry_price * 100, 0)

        base_info = {
            "signal_id": signal.id,
            "title": market.title[:60],
            "direction": str(signal.signal_direction or "YES").upper(),
            "ev_pct": ev_pct,
            "daily_ev": daily_ev,
            "days_to_res": days_to_res,
            "volume_usd": volume,
            "spread_pct": spread_pct,
            "koef": koef,
            "max_win_pct": max_win_pct,
            "kelly": kelly,
        }

        # Hard reject
        hard_reason = None
        if spread_pct > HARD_MAX_SPREAD:
            hard_reason = f"spread {spread_pct:.1%} > {HARD_MAX_SPREAD:.0%}"
        elif volume < HARD_MIN_VOLUME and volume > 0:
            hard_reason = f"volume ${volume:.0f} < ${HARD_MIN_VOLUME:.0f}"
        elif days_to_res > HARD_MAX_DAYS:
            hard_reason = f"{days_to_res:.0f}d > {HARD_MAX_DAYS}d limit"
        if hard_reason:
            hard_rejected.append({**base_info, "reason": hard_reason})
            continue

        # Soft violations
        soft_violations: list[str] = []
        if spread_pct > SOFT_MAX_SPREAD:
            soft_violations.append(f"spread {spread_pct:.1%}")
        if volume < SOFT_MIN_VOLUME:
            soft_violations.append(f"vol ${volume/1000:.0f}k")
        if days_to_res > SOFT_MAX_DAYS:
            soft_violations.append(f"{days_to_res:.0f}d")

        if soft_violations:
            if daily_ev >= LLM_MIN_DAILY_EV:
                entry = {**base_info, "filter_reason": "; ".join(soft_violations)}
                borderline.append(entry)
                borderline_map[signal.id] = entry
            else:
                soft_rejected.append({**base_info, "reason": "; ".join(soft_violations), "note": "daily_ev too low for LLM"})
        else:
            accepted.append(base_info)

    # LLM review for borderline
    llm_approved: set[int] = set()
    if borderline:
        llm_approved = _llm_review_borderline(borderline)
        for sig_id, entry in borderline_map.items():
            if sig_id in llm_approved:
                accepted.append(entry)

    accepted.sort(key=lambda x: x["daily_ev"], reverse=True)

    return {
        "accepted": accepted,
        "borderline": borderline,
        "llm_approved": llm_approved,
        "hard_rejected": hard_rejected,
        "soft_rejected": soft_rejected,
        "duplicates": duplicates,
    }


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

    # Build candidate list with computed metrics for smart sorting
    # Separate hard-rejected, borderline (LLM review), and accepted
    CandidateTuple = tuple  # (signal, s7, market, kelly, ev_pct, daily_ev)
    accepted: list[CandidateTuple] = []
    borderline: list[dict[str, Any]] = []   # for LLM review
    borderline_map: dict[int, CandidateTuple] = {}  # signal_id → tuple

    for signal, s7, market in rows:
        if market.id in open_market_ids:
            skipped += 1
            continue

        # --- Compute metrics ---
        spread_pct = 0.0
        if market.best_bid_yes is not None and market.best_ask_yes is not None and market.best_ask_yes > 0:
            spread_pct = (float(market.best_ask_yes) - float(market.best_bid_yes)) / float(market.best_ask_yes)
        elif market.spread_cents is not None:
            mid = ((market.best_bid_yes or 0) + (market.best_ask_yes or 0)) / 2 or 0.5
            spread_pct = float(market.spread_cents) / 100.0 / mid

        volume = float(market.notional_value_dollars or market.liquidity_value or 0.0)

        days_to_res = 999.0
        if market.resolution_time:
            days_to_res = max(0.5, (market.resolution_time - now).total_seconds() / 86400.0)

        ev_bundle: dict[str, Any] = s7.evidence_bundle or {}
        kelly = float(ev_bundle.get("kelly_fraction") or signal.confidence_score or 0.0)
        ev_pct = float(ev_bundle.get("expected_ev_pct") or signal.divergence_score or 0.0)
        if ev_pct <= 0.0 and signal.confidence_score:
            ev_pct = float(signal.confidence_score) * 0.05
        daily_ev = ev_pct / max(1.0, days_to_res)

        # --- Hard limits: always skip, no LLM override ---
        if spread_pct > HARD_MAX_SPREAD:
            skipped += 1
            reasons.append(f"signal {signal.id}: hard-reject spread {spread_pct:.1%}")
            continue
        if volume < HARD_MIN_VOLUME and volume > 0:
            skipped += 1
            reasons.append(f"signal {signal.id}: hard-reject volume ${volume:.0f}")
            continue
        if days_to_res > HARD_MAX_DAYS:
            skipped += 1
            reasons.append(f"signal {signal.id}: hard-reject {days_to_res:.0f}d")
            continue

        # --- Soft limits: send borderline cases to LLM ---
        soft_violations: list[str] = []
        if spread_pct > SOFT_MAX_SPREAD:
            soft_violations.append(f"spread {spread_pct:.1%} > {SOFT_MAX_SPREAD:.0%}")
        if volume < SOFT_MIN_VOLUME:
            soft_violations.append(f"volume ${volume:.0f} < ${SOFT_MIN_VOLUME:.0f}")
        if days_to_res > SOFT_MAX_DAYS:
            soft_violations.append(f"{days_to_res:.0f}d > {SOFT_MAX_DAYS}d")

        tup = (signal, s7, market, kelly, ev_pct, daily_ev)
        if soft_violations and daily_ev >= LLM_MIN_DAILY_EV:
            # Borderline: has some violation but enough EV/day to be worth LLM review
            borderline_map[signal.id] = tup
            borderline.append({
                "signal_id": signal.id,
                "title": market.title,
                "ev_pct": ev_pct,
                "daily_ev": daily_ev,
                "days_to_res": days_to_res,
                "volume_usd": volume,
                "spread_pct": spread_pct,
                "filter_reason": "; ".join(soft_violations),
            })
        elif soft_violations:
            skipped += 1
            reasons.append(f"signal {signal.id}: soft-reject {'; '.join(soft_violations)} (daily_ev too low)")
        else:
            accepted.append(tup)

    # LLM reviews borderline candidates
    if borderline:
        llm_approved = _llm_review_borderline(borderline)
        for sig_id, tup in borderline_map.items():
            if sig_id in llm_approved:
                accepted.append(tup)
                reasons.append(f"signal {sig_id}: borderline → LLM approved")
            else:
                skipped += 1
                reasons.append(f"signal {sig_id}: borderline → LLM rejected")

    candidates = accepted

    # Sort by daily_ev descending — best return per day of capital lock first
    candidates.sort(key=lambda x: x[5], reverse=True)  # type: ignore[attr-defined]

    for signal, s7, market, kelly, ev_pct, daily_ev in candidates:
        position_pct = _compute_position_pct(kelly, ev_pct)
        if position_pct is None:
            position_pct = MIN_POSITION_PCT

        notional = portfolio.current_cash_usd * position_pct
        if notional < MIN_NOTIONAL_USD:
            skipped += 1
            reasons.append(f"signal {signal.id}: notional ${notional:.2f} < min ${MIN_NOTIONAL_USD}")
            continue

        direction = str(signal.signal_direction or "YES").upper()
        if direction not in ("YES", "NO"):
            direction = "YES"

        entry_price = float(market.best_ask_yes) if direction == "YES" else (1.0 - float(market.best_ask_yes))
        if entry_price <= 0.0 or entry_price >= 1.0:
            skipped += 1
            continue

        days_to_res = max(0.5, (market.resolution_time - now).total_seconds() / 86400.0) if market.resolution_time else 999.0
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
            open_reason=f"kelly={kelly:.4f},ev={ev_pct:.4f},daily_ev={daily_ev:.5f},stage7=KEEP",
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

        def _close_pos(reason: str) -> None:
            pos.realized_pnl_usd = pos.unrealized_pnl_usd
            portfolio.current_cash_usd += pos.notional_usd + pos.realized_pnl_usd
            portfolio.total_realized_pnl_usd += pos.realized_pnl_usd
            pos.status = "CLOSED"
            pos.close_reason = reason
            pos.closed_at = now
            pos.updated_at = now

        # Stop-loss: mark dropped 50% from entry
        if new_mark < pos.entry_price * STOP_LOSS_RATIO:
            _close_pos("stop_loss")
            stop_loss_closed += 1
            total_unrealized -= pos.unrealized_pnl_usd
            pos.unrealized_pnl_usd = 0.0
            continue

        # Take-profit: captured 65% of max possible gain
        take_profit_price = pos.entry_price + (1.0 - pos.entry_price) * TAKE_PROFIT_RATIO
        if new_mark >= take_profit_price:
            _close_pos("take_profit")
            total_unrealized -= pos.unrealized_pnl_usd
            pos.unrealized_pnl_usd = 0.0
            continue

        # Time-exit: held too long with low remaining EV
        days_held = (now - pos.opened_at).total_seconds() / 86400.0 if pos.opened_at else 0.0
        ev_remaining = pos.entry_ev_pct or 0.0
        if days_held >= TIME_EXIT_DAYS and ev_remaining < TIME_EXIT_MIN_EV:
            _close_pos("time_exit")
            total_unrealized -= pos.unrealized_pnl_usd
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
