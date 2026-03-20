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
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.models.enums import SignalType
from app.models.models import (
    DryrunPortfolio,
    DryrunPosition,
    Market,
    Signal,
    Stage7AgentDecision,
)
from app.services.dryrun.kelly import kelly_fraction, portfolio_kelly_adjustment
from app.services.dryrun.scorer import composite_score
from app.services.dryrun.cross_platform import build_cross_platform_prob_map, get_cross_platform_prob
from app.services.stage17.tail_executor import run_stage17_tail_cycle
from app.utils.http import retry_request

logger = logging.getLogger(__name__)

PORTFOLIO_NAME = "default"
INITIAL_BALANCE = 100.0
CLOB_MAX_POSITION_PCT = 0.05
NON_CLOB_MAX_POSITION_PCT = 0.02
NON_CLOB_MAX_TOTAL_EXPOSURE_PCT = 0.15
MAX_TOTAL_EXPOSURE_PCT = 0.80
MIN_NOTIONAL_USD = 1.0
STOP_LOSS_PARTIAL_RATIO = 0.65  # close 50% if mark drops 35% from entry
STOP_LOSS_FULL_RATIO = 0.40     # close full if mark drops 60% from entry
TRAILING_TAKE_PROFIT_DRAWDOWN = 0.15
TIME_EXIT_MIN_DAILY_EV = 0.0001
TIME_EXIT_MIN_HOLD_DAYS = 7.0

# Hard limits — never enter regardless of LLM opinion
HARD_MAX_DAYS = 180
HARD_MAX_SPREAD = 0.10
MIN_SCORE_THRESHOLD = 0.35
TOP_N_PER_CYCLE = 30
MOMENTUM_CONTRARIAN_EDGE = 0.07


def _as_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


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
    providers: list[tuple[str, str, str, dict[str, str]]] = []
    if settings.groq_api_key:
        providers.append(
            ("https://api.groq.com/openai/v1", settings.groq_api_key, settings.stage7_groq_model, {})
        )
    if settings.gemini_api_key:
        providers.append((
            "https://generativelanguage.googleapis.com/v1beta/openai",
            settings.gemini_api_key,
            settings.stage7_gemini_model,
            {},
        ))
    if settings.openrouter_api_key:
        extra_headers: dict[str, str] = {}
        if str(settings.stage7_openrouter_http_referer or "").strip():
            extra_headers["HTTP-Referer"] = str(settings.stage7_openrouter_http_referer).strip()
        if str(settings.stage7_openrouter_x_title or "").strip():
            extra_headers["X-Title"] = str(settings.stage7_openrouter_x_title).strip()
        providers.append(
            (
                "https://openrouter.ai/api/v1",
                settings.openrouter_api_key,
                settings.stage7_openrouter_model,
                extra_headers,
            )
        )
    if settings.stage7_openai_api_key:
        providers.append(
            (settings.stage7_openai_api_base_url, settings.stage7_openai_api_key, settings.stage7_openai_model, {})
        )
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

    for api_base, api_key, model, extra_headers in providers:
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
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **extra_headers,
                },
            )
            with urllib_request.urlopen(req, timeout=15.0) as resp:  # nosec B310
                result = json.loads(resp.read().decode("utf-8"))
            text = ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            # Parse JSON array — accept [1, 2, 3] or [1 2 3] formats
            import re
            match = re.search(r"\[[\d\s,]*\]", text)
            if match:
                approved_ids = json.loads(match.group(0).replace(" ", ",").replace(",,", ","))
                approved = set(int(x) for x in approved_ids if isinstance(x, (int, float)))
                logger.info("LLM borderline review via %s: approved %d/%d signals: %s", api_base, len(approved), len(candidates), approved)
                return approved
            logger.warning("LLM borderline review via %s: no JSON array in response: %r", api_base, text[:200])
            return set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM borderline review failed (%s): %s", api_base, exc)
            continue

    logger.warning("LLM borderline review: all providers failed, skipping %d candidates", len(candidates))
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


def _lock_portfolio(db: Session) -> DryrunPortfolio:
    """Lock portfolio row for update to avoid concurrent cash/exposure races."""
    row = get_or_create_portfolio(db)
    locked = db.scalar(select(DryrunPortfolio).where(DryrunPortfolio.id == row.id).with_for_update())
    return locked or row


def reset_portfolio(db: Session) -> DryrunPortfolio:
    """Close all open positions and reset cash to $100."""
    portfolio = _lock_portfolio(db)
    now = datetime.now(UTC)
    now_cmp = _as_utc_naive(now) or now.replace(tzinfo=None)
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


def _extract_side_prices(market: Market, direction: str) -> tuple[float | None, float]:
    is_clob = market.best_ask_yes is not None and market.best_bid_yes is not None
    market_prob_yes = float(market.probability_yes) if market.probability_yes is not None else None
    spread_pct = 0.05
    if market.best_bid_yes is not None and market.best_ask_yes is not None and market.best_ask_yes > 0:
        spread_pct = max(0.0, (float(market.best_ask_yes) - float(market.best_bid_yes)) / float(market.best_ask_yes))
    elif market.spread_cents is not None:
        spread_pct = max(0.0, min(0.2, float(market.spread_cents) / 100.0))

    if direction == "YES":
        if market.best_ask_yes is not None:
            return float(market.best_ask_yes), spread_pct
        if market_prob_yes is not None:
            return min(0.99, max(0.01, market_prob_yes + spread_pct / 2.0)), spread_pct
        return None, spread_pct

    # NO side
    if market.best_bid_yes is not None:
        return min(0.99, max(0.01, 1.0 - float(market.best_bid_yes))), spread_pct
    if market_prob_yes is not None:
        no_prob = 1.0 - market_prob_yes
        return min(0.99, max(0.01, no_prob + spread_pct / 2.0)), spread_pct
    return None, spread_pct


def _estimate_our_prob_yes(
    db: Session,
    signal: Signal,
    s7: Stage7AgentDecision,
    market: Market,
    *,
    cross_prob_cache: dict[int, dict[str, Any] | None] | None = None,
    precomputed_cross_probs: dict[int, dict[str, Any] | None] | None = None,
) -> float:
    settings = get_settings()
    market_prob_yes = float(market.probability_yes or 0.5)

    cross: dict[str, Any] | None
    if precomputed_cross_probs is not None and int(market.id) in precomputed_cross_probs:
        cross = precomputed_cross_probs.get(int(market.id))
    elif cross_prob_cache is not None:
        mid = int(market.id)
        if mid not in cross_prob_cache:
            cross_prob_cache[mid] = get_cross_platform_prob(db, market=market, settings=settings)
        cross = cross_prob_cache.get(mid)
    else:
        cross = get_cross_platform_prob(db, market=market, settings=settings)
    if cross:
        cross_prob = float(cross.get("cross_prob") or market_prob_yes)
        if abs(cross_prob - market_prob_yes) >= float(settings.dryrun_cross_platform_min_diff):
            w = min(1.0, max(0.0, float(settings.dryrun_cross_platform_prob_weight)))
            blended = (w * cross_prob) + ((1.0 - w) * market_prob_yes)
            return min(0.95, max(0.05, blended))

    ev_bundle: dict[str, Any] = s7.evidence_bundle or {}
    market_prob = ev_bundle.get("market_prob")
    if isinstance(market_prob, (int, float)):
        return min(0.95, max(0.05, float(market_prob)))

    consensus = ev_bundle.get("external_consensus") if isinstance(ev_bundle.get("external_consensus"), dict) else {}
    if isinstance(consensus, dict):
        weighted = consensus.get("consensus_weighted_prob")
        if isinstance(weighted, (int, float)):
            return min(0.95, max(0.05, float(weighted)))

    meta = signal.metadata_json or {}
    cross_platform = meta.get("cross_platform_prob")
    if isinstance(cross_platform, (int, float)):
        return min(0.95, max(0.05, float(cross_platform)))

    if str(signal.signal_mode or "").lower() in ("momentum", "uncertainty_liquid"):
        current = float(market.probability_yes or 0.5)
        signed_recent = meta.get("signed_recent_move")
        recent_move = meta.get("recent_move")
        price_move = meta.get("price_move")

        move: float = 0.0
        if isinstance(signed_recent, (int, float)):
            move = float(signed_recent)
        elif isinstance(price_move, (int, float)):
            move = float(price_move)
        elif isinstance(recent_move, (int, float)):
            inferred_sign = 1.0 if str(signal.signal_direction or "YES").upper() == "YES" else -1.0
            move = float(recent_move) * inferred_sign

        # Empirical mean-reversion edge from inverted-momentum backtest (57% win rate, 23 samples).
        # Scale edge with move magnitude: small moves → smaller edge; cap at MOMENTUM_CONTRARIAN_EDGE.
        sign = 1.0 if move >= 0 else -1.0
        scaled_edge = min(MOMENTUM_CONTRARIAN_EDGE, max(0.02, abs(move) * 1.5))
        return min(0.95, max(0.05, current - sign * scaled_edge))

    return min(0.95, max(0.05, market_prob_yes))


def _time_bucket_limit(days_to_res: float) -> float:
    if days_to_res <= 14:
        return 0.35
    if days_to_res <= 45:
        return 0.35
    if days_to_res <= 90:
        return 0.20
    return 0.10


def _resolve_trade_direction(signal: Signal) -> str:
    direction = str(signal.signal_direction or "YES").upper()
    if direction not in ("YES", "NO"):
        direction = "YES"
    mode = str(signal.signal_mode or "").lower()
    if mode in ("momentum", "uncertainty_liquid"):
        direction = "NO" if direction == "YES" else "YES"
    return direction


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


def _load_latest_keep_rows(db: Session, *, limit: int) -> list[tuple[Signal, Stage7AgentDecision, Market]]:
    """Load candidate rows using only the latest Stage7 decision per signal."""
    latest_ids = (
        select(
            Stage7AgentDecision.signal_id.label("signal_id"),
            func.max(Stage7AgentDecision.id).label("latest_id"),
        )
        .group_by(Stage7AgentDecision.signal_id)
        .subquery()
    )
    latest = aliased(Stage7AgentDecision)
    return list(
        db.execute(
            select(Signal, latest, Market)
            .join(latest_ids, latest_ids.c.signal_id == Signal.id)
            .join(latest, latest.id == latest_ids.c.latest_id)
            .join(Market, Market.id == Signal.market_id)
            .where(
                Signal.signal_type == SignalType.ARBITRAGE_CANDIDATE,
                latest.decision == "KEEP",
                or_(
                    Market.best_ask_yes.is_not(None),
                    Market.best_bid_yes.is_not(None),
                    Market.probability_yes.is_not(None),
                ),
            )
            .order_by(latest.created_at.desc())
            .limit(limit)
        )
    )


# ---------------------------------------------------------------------------
# Shared candidate scanning logic
# ---------------------------------------------------------------------------


def _scan_signal_candidates(db: Session) -> dict[str, Any]:
    """Scan all Stage7-KEEP signals and classify them without touching DB.

    Returns dict with:
      accepted      — ranked top candidates ready to open
      borderline    — retained for backward-compatible UI (always empty in stage15)
      llm_approved  — retained for backward-compatible UI (always empty in stage15)
      hard_rejected — list of {signal_id, title, reason}
      soft_rejected — kept for backward-compatible UI (unused in stage15)
      duplicates    — count already-open markets
      row_map       — latest Stage7 rows by signal_id for reuse in run phase
    """
    portfolio = get_or_create_portfolio(db)
    now = datetime.now(UTC)

    rows = _load_latest_keep_rows(db, limit=300)
    row_map = {int(signal.id): (signal, s7, market) for signal, s7, market in rows}

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
    hard_rejected: list[dict[str, Any]] = []
    soft_rejected: list[dict[str, Any]] = []
    duplicates = 0
    cross_prob_cache: dict[int, dict[str, Any] | None] = {}
    precomputed_cross_probs = build_cross_platform_prob_map(
        db,
        markets=[market for _, _, market in rows],
        settings=get_settings(),
    )

    for signal, s7, market in rows:
        if market.id in open_market_ids:
            duplicates += 1
            continue

        direction = _resolve_trade_direction(signal)
        entry_price, spread_pct = _extract_side_prices(market, direction=direction)
        is_clob = market.best_bid_yes is not None and market.best_ask_yes is not None
        if entry_price is None:
            hard_rejected.append({"signal_id": signal.id, "title": market.title[:60], "reason": "entry_price_missing"})
            continue
        volume = float(market.notional_value_dollars or market.liquidity_value or 0.0)
        days_to_res = 999.0
        if market.resolution_time:
            days_to_res = max(0.5, (market.resolution_time - now).total_seconds() / 86400.0)

        ev_bundle: dict[str, Any] = s7.evidence_bundle or {}
        _internal = ev_bundle.get("internal_metrics_snapshot") or {}
        ev_pct = float(
            ev_bundle.get("expected_ev_pct")
            or _internal.get("expected_ev_pct")
            or signal.divergence_score
            or 0.0
        )
        if ev_pct <= 0.0 and signal.confidence_score:
            ev_pct = float(signal.confidence_score) * 0.05
        daily_ev = ev_pct / max(1.0, days_to_res)
        confidence = float(signal.confidence_score or 0.0)

        our_prob_yes = _estimate_our_prob_yes(
            db,
            signal,
            s7,
            market,
            cross_prob_cache=cross_prob_cache,
            precomputed_cross_probs=precomputed_cross_probs,
        )
        our_prob_side = our_prob_yes if direction == "YES" else (1.0 - our_prob_yes)
        raw_kelly = kelly_fraction(
            market_price=entry_price,
            our_prob=our_prob_side,
            alpha=0.25,
            max_fraction=0.10,
        )
        score = composite_score(
            daily_ev_pct=daily_ev,
            spread=spread_pct,
            volume_usd=volume,
            confidence=confidence,
            days_to_resolution=days_to_res,
            kelly_fraction=raw_kelly,
            is_clob=is_clob,
        )

        koef = round(1.0 / entry_price, 2) if entry_price > 0 else 0.0
        max_win_pct = round((1.0 - entry_price) / entry_price * 100, 0)

        base_info = {
            "signal_id": signal.id,
            "title": market.title[:60],
            "direction": direction,
            "ev_pct": ev_pct,
            "daily_ev": daily_ev,
            "days_to_res": days_to_res,
            "volume_usd": volume,
            "spread_pct": spread_pct,
            "koef": koef,
            "max_win_pct": max_win_pct,
            "kelly": raw_kelly,
            "score": score,
            "is_clob": is_clob,
            "our_prob_yes": our_prob_yes,
        }

        # Hard reject
        hard_reason = None
        if spread_pct > HARD_MAX_SPREAD:
            hard_reason = f"spread {spread_pct:.1%} > {HARD_MAX_SPREAD:.0%}"
        elif days_to_res > HARD_MAX_DAYS:
            hard_reason = f"{days_to_res:.0f}d > {HARD_MAX_DAYS}d limit"
        if hard_reason:
            hard_rejected.append({**base_info, "reason": hard_reason})
            continue

        if score < MIN_SCORE_THRESHOLD:
            soft_rejected.append({**base_info, "reason": f"score {score:.3f} < {MIN_SCORE_THRESHOLD:.2f}"})
            continue
        accepted.append(base_info)

    accepted.sort(key=lambda x: x["score"], reverse=True)
    accepted = accepted[:TOP_N_PER_CYCLE]

    return {
        "accepted": accepted,
        "borderline": borderline,
        "llm_approved": set(),
        "hard_rejected": hard_rejected,
        "soft_rejected": soft_rejected,
        "duplicates": duplicates,
        "row_map": row_map,
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
    tail_result = run_stage17_tail_cycle(
        db,
        settings=get_settings(),
        limit=max(1, int(get_settings().signal_tail_max_candidates)),
        open_new=True,
    )
    scan = _scan_signal_candidates(db)
    accepted_scored = list(scan.get("accepted") or [])
    if not accepted_scored:
        return {
            "opened": 0,
            "skipped": len(scan.get("hard_rejected") or []) + len(scan.get("soft_rejected") or []),
            "cash_remaining_usd": round(portfolio.current_cash_usd, 4),
            "skip_reasons": ["no_candidates_after_scoring"],
            "tail": tail_result,
        }
    portfolio = _lock_portfolio(db)
    by_signal_id = dict(scan.get("row_map") or {})

    # Collect already-open market IDs to avoid duplicate positions
    open_positions = list(
        db.scalars(
            select(DryrunPosition).where(
                DryrunPosition.portfolio_id == portfolio.id,
                DryrunPosition.status == "OPEN",
            )
        )
    )
    open_market_ids: set[int] = {int(p.market_id) for p in open_positions}

    now = datetime.now(UTC)
    total_open_notional_pct = sum(float(p.notional_usd or 0.0) for p in open_positions) / max(
        float(portfolio.initial_balance_usd), 1.0
    )
    non_clob_open_notional_pct = (
        sum(float(p.notional_usd or 0.0) for p in open_positions if (str(p.open_reason or "").find("non_clob") >= 0))
        / max(float(portfolio.initial_balance_usd), 1.0)
    )
    bucket_open_notional_pct: dict[str, float] = {"0_14": 0.0, "15_45": 0.0, "46_90": 0.0, "91_180": 0.0}
    for p in open_positions:
        days = 999.0
        if p.resolution_deadline:
            days = max(0.5, (p.resolution_deadline - now).total_seconds() / 86400.0)
        if days <= 14:
            bucket_open_notional_pct["0_14"] += float(p.notional_usd or 0.0)
        elif days <= 45:
            bucket_open_notional_pct["15_45"] += float(p.notional_usd or 0.0)
        elif days <= 90:
            bucket_open_notional_pct["46_90"] += float(p.notional_usd or 0.0)
        else:
            bucket_open_notional_pct["91_180"] += float(p.notional_usd or 0.0)
    for key in list(bucket_open_notional_pct.keys()):
        bucket_open_notional_pct[key] /= max(float(portfolio.initial_balance_usd), 1.0)

    cross_prob_cache: dict[int, dict[str, Any] | None] = {}
    for cand in accepted_scored:
        sid = int(cand.get("signal_id") or 0)
        triple = by_signal_id.get(sid)
        if triple is None:
            skipped += 1
            reasons.append(f"signal {sid}: missing latest row")
            continue
        signal, s7, market = triple
        if market.id in open_market_ids:
            skipped += 1
            reasons.append(f"signal {signal.id}: duplicate open market")
            continue

        direction = str(cand.get("direction") or _resolve_trade_direction(signal)).upper()
        if direction not in ("YES", "NO"):
            direction = _resolve_trade_direction(signal)
        entry_price, spread_pct = _extract_side_prices(market, direction)
        if entry_price is None:
            skipped += 1
            reasons.append(f"signal {signal.id}: entry price unavailable")
            continue
        days_to_res = float(cand.get("days_to_res") or 999.0)
        ev_pct = float(cand.get("ev_pct") or 0.0)
        daily_ev = float(cand.get("daily_ev") or 0.0)
        is_clob = bool(cand.get("is_clob"))

        raw_our_prob = cand.get("our_prob_yes")
        if isinstance(raw_our_prob, (int, float)):
            our_prob_yes = min(0.95, max(0.05, float(raw_our_prob)))
        else:
            our_prob_yes = _estimate_our_prob_yes(
                db,
                signal,
                s7,
                market,
                cross_prob_cache=cross_prob_cache,
            )
        our_prob_side = our_prob_yes if direction == "YES" else (1.0 - our_prob_yes)
        base_kelly = kelly_fraction(market_price=entry_price, our_prob=our_prob_side, alpha=0.25, max_fraction=0.10)
        adjusted_kelly = portfolio_kelly_adjustment(
            base_kelly=base_kelly,
            total_open_notional_pct=total_open_notional_pct,
            max_total_exposure=MAX_TOTAL_EXPOSURE_PCT,
        )
        position_cap = CLOB_MAX_POSITION_PCT if is_clob else NON_CLOB_MAX_POSITION_PCT
        position_pct = min(adjusted_kelly, position_cap)
        if position_pct <= 0.0:
            skipped += 1
            reasons.append(f"signal {signal.id}: zero kelly after portfolio adjustment")
            continue

        bucket_key = "91_180"
        if days_to_res <= 14:
            bucket_key = "0_14"
        elif days_to_res <= 45:
            bucket_key = "15_45"
        elif days_to_res <= 90:
            bucket_key = "46_90"
        bucket_limit = _time_bucket_limit(days_to_res)
        if bucket_open_notional_pct[bucket_key] + position_pct > bucket_limit:
            skipped += 1
            reasons.append(f"signal {signal.id}: bucket exposure limit reached ({bucket_key})")
            continue
        if (not is_clob) and (non_clob_open_notional_pct + position_pct > NON_CLOB_MAX_TOTAL_EXPOSURE_PCT):
            skipped += 1
            reasons.append(f"signal {signal.id}: non-clob exposure limit reached")
            continue

        notional = portfolio.current_cash_usd * position_pct
        if notional < MIN_NOTIONAL_USD:
            skipped += 1
            reasons.append(f"signal {signal.id}: notional ${notional:.2f} < min ${MIN_NOTIONAL_USD}")
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
            open_reason=(
                f"kelly={adjusted_kelly:.4f},ev={ev_pct:.4f},daily_ev={daily_ev:.5f},"
                f"score={float(cand.get('score') or 0.0):.4f},"
                f"{'clob' if is_clob else 'non_clob'},peak={entry_price:.6f}"
            ),
            entry_kelly_fraction=adjusted_kelly,
            entry_ev_pct=ev_pct,
            unrealized_pnl_usd=0.0,
            resolution_deadline=market.resolution_time,
        )
        db.add(pos)
        portfolio.current_cash_usd -= notional
        total_open_notional_pct += position_pct
        if not is_clob:
            non_clob_open_notional_pct += position_pct
        bucket_open_notional_pct[bucket_key] += position_pct
        open_market_ids.add(market.id)
        opened += 1

    portfolio.updated_at = now
    db.flush()

    return {
        "opened": opened,
        "skipped": skipped,
        "cash_remaining_usd": round(portfolio.current_cash_usd, 4),
        "skip_reasons": reasons[:10],
        "tail": tail_result,
    }


# ---------------------------------------------------------------------------
# Mark-to-market refresh
# ---------------------------------------------------------------------------


def refresh_mark_prices(db: Session) -> dict[str, Any]:
    """Fetch current CLOB prices for all OPEN positions and update unrealized P&L."""
    portfolio = _lock_portfolio(db)
    open_positions = list(
        db.scalars(
            select(DryrunPosition)
            .where(DryrunPosition.portfolio_id == portfolio.id, DryrunPosition.status == "OPEN")
        )
    )

    updated = 0
    stop_loss_partial = 0
    stop_loss_closed = 0
    trailing_closed = 0
    time_exit_closed = 0
    expired_closed = 0
    now = datetime.now(UTC)
    now_cmp = _as_utc_naive(now) or now.replace(tzinfo=None)
    total_unrealized = 0.0

    # Batch-load all markets for open positions — avoids N+1 db.get() per position.
    _mids = list({pos.market_id for pos in open_positions if pos.market_id is not None})
    _mmap: dict[int, Market] = {
        m.id: m for m in db.scalars(select(Market).where(Market.id.in_(_mids)))
    } if _mids else {}

    for pos in open_positions:
        market = _mmap.get(pos.market_id)
        if market is None:
            continue

        # Check expiry
        pos_deadline = _as_utc_naive(pos.resolution_deadline)
        if pos_deadline and now_cmp > pos_deadline:
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
        # Persist peak mark in open_reason as "...peak=0.123456"
        peak = pos.entry_price
        reason_text = str(pos.open_reason or "")
        try:
            if "peak=" in reason_text:
                peak = float(reason_text.split("peak=")[-1].split(",")[0].strip())
        except Exception:
            peak = pos.entry_price
        if new_mark > peak:
            peak = new_mark
            if "peak=" in reason_text:
                prefix = reason_text.split("peak=")[0].rstrip(",")
                pos.open_reason = f"{prefix},peak={peak:.6f}"
            else:
                pos.open_reason = f"{reason_text},peak={peak:.6f}".strip(",")

        partial_already_done = "partial_stop_hit" in reason_text

        # Stop-loss stage 1: partial exit at 65% of entry (one-time only)
        if (not partial_already_done) and new_mark <= pos.entry_price * STOP_LOSS_PARTIAL_RATIO and pos.shares_count > 0:
            partial_shares = pos.shares_count * 0.5
            partial_notional = pos.notional_usd * 0.5
            partial_pnl = (new_mark - pos.entry_price) * partial_shares
            pos.shares_count -= partial_shares
            pos.notional_usd -= partial_notional
            pos.realized_pnl_usd += partial_pnl
            portfolio.current_cash_usd += partial_notional + partial_pnl
            portfolio.total_realized_pnl_usd += partial_pnl
            pos.open_reason = f"{str(pos.open_reason or '')},partial_stop_hit".strip(",")
            # Recompute unrealized after partial close
            pos.unrealized_pnl_usd = (new_mark - pos.entry_price) * pos.shares_count
            stop_loss_partial += 1

        # Stop-loss stage 2: full exit at 40% of entry
        if new_mark <= pos.entry_price * STOP_LOSS_FULL_RATIO:
            _close_pos("stop_loss_full")
            stop_loss_closed += 1
            total_unrealized -= pos.unrealized_pnl_usd
            pos.unrealized_pnl_usd = 0.0
            continue

        # Take-profit: trailing stop from peak mark
        trailing_trigger = peak * (1.0 - TRAILING_TAKE_PROFIT_DRAWDOWN)
        if peak > pos.entry_price and new_mark <= trailing_trigger:
            _close_pos("take_profit_trailing")
            trailing_closed += 1
            total_unrealized -= pos.unrealized_pnl_usd
            pos.unrealized_pnl_usd = 0.0
            continue

        # Time-exit: close if expected remaining value per day is tiny
        opened_at_cmp = _as_utc_naive(pos.opened_at)
        days_held = (now_cmp - opened_at_cmp).total_seconds() / 86400.0 if opened_at_cmp else 0.0
        days_left = max(
            1.0,
            ((pos_deadline - now_cmp).total_seconds() / 86400.0) if pos_deadline else 999.0,
        )
        ev_remaining = pos.unrealized_pnl_usd / pos.notional_usd if pos.notional_usd > 0 else 0.0
        daily_ev_remaining = ev_remaining / days_left
        # Skip time-exit if partial stop already executed — position may still recover
        partial_stop_active = "partial_stop_hit" in str(pos.open_reason or "")
        if (
            days_held >= TIME_EXIT_MIN_HOLD_DAYS
            and daily_ev_remaining < TIME_EXIT_MIN_DAILY_EV
            and not partial_stop_active
        ):
            _close_pos("time_exit_low_daily_ev")
            time_exit_closed += 1
            total_unrealized -= pos.unrealized_pnl_usd
            pos.unrealized_pnl_usd = 0.0

    portfolio.total_unrealized_pnl_usd = total_unrealized
    portfolio.updated_at = now
    db.flush()

    tail_result = run_stage17_tail_cycle(
        db,
        settings=get_settings(),
        limit=0,
        open_new=False,
    )
    return {
        "prices_updated": updated,
        "stop_loss_partial": stop_loss_partial,
        "stop_loss_closed": stop_loss_closed,
        "trailing_closed": trailing_closed,
        "time_exit_closed": time_exit_closed,
        "expired_closed": expired_closed,
        "total_unrealized_usd": round(total_unrealized, 4),
        "tail": tail_result,
    }


# ---------------------------------------------------------------------------
# Resolution check
# ---------------------------------------------------------------------------


def check_resolutions(db: Session) -> dict[str, Any]:
    """Close positions for markets that Gamma API has marked as resolved."""
    portfolio = _lock_portfolio(db)
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

    # Batch-load markets — avoids N+1 db.get() per position.
    _res_mids = list({pos.market_id for pos in open_positions if pos.market_id is not None})
    _res_mmap: dict[int, Market] = {
        m.id: m for m in db.scalars(select(Market).where(Market.id.in_(_res_mids)))
    } if _res_mids else {}

    for pos in open_positions:
        market = _res_mmap.get(pos.market_id)
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
    tail_result = run_stage17_tail_cycle(
        db,
        settings=get_settings(),
        limit=0,
        open_new=False,
    )
    return {"resolved_closed": resolved_count, "tail": tail_result}
