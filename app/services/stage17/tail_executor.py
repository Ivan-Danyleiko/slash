from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import SignalType
from app.models.models import JobRun, Market, Signal, Stage17TailFill, Stage17TailPosition
from app.services.agent_stage7.tail_stage7 import evaluate_tail_stage7
from app.services.signals.tail_circuit_breaker import _category_limit_map, check_tail_circuit_breaker


def _is_market_resolved(market: Market, *, now: datetime | None = None) -> bool:
    now_ref = now or datetime.now(UTC)
    rt = market.resolution_time
    if rt is not None:
        ref = rt.astimezone(UTC) if rt.tzinfo else rt.replace(tzinfo=UTC)
        if ref <= now_ref:
            return True
    status = str(market.status or "").lower()
    if any(k in status for k in ("resolved", "closed", "settled", "final", "ended")):
        return True
    payload = market.source_payload if isinstance(market.source_payload, dict) else {}
    if bool(payload.get("isResolved")) or bool(payload.get("resolved")):
        return True
    return False


def _resolved_yes(market: Market) -> bool | None:
    payload = market.source_payload if isinstance(market.source_payload, dict) else {}
    if "resolutionProbability" in payload and payload.get("resolutionProbability") is not None:
        return float(payload.get("resolutionProbability")) >= 0.5
    if "resolved_probability" in payload and payload.get("resolved_probability") is not None:
        return float(payload.get("resolved_probability")) >= 0.5
    raw = (
        payload.get("resolution")
        or payload.get("resolvedOutcome")
        or payload.get("outcome")
        or payload.get("result")
    )
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in {"yes", "true", "1", "up"}:
            return True
        if v in {"no", "false", "0", "down"}:
            return False
    if isinstance(raw, (int, float)):
        return float(raw) >= 0.5
    # Do not infer resolved outcome from pre-resolution market price.
    return None


def _acquire_tail_cycle_lock_if_supported(db: Session) -> bool:
    bind = getattr(db, "bind", None)
    if bind is None:
        try:
            bind = db.connection().engine
        except Exception:  # noqa: BLE001
            bind = None
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "")).lower() if bind is not None else ""
    if dialect != "postgresql":
        return True
    locked = db.scalar(select(func.pg_try_advisory_xact_lock(91700017)))
    return bool(locked)


def _side_price(market: Market, direction: str) -> float:
    p_yes = min(0.999, max(0.001, float(market.probability_yes or 0.5)))
    return p_yes if direction == "YES" else (1.0 - p_yes)


def _position_shares(row: Stage17TailPosition) -> float:
    entry = float(row.entry_price or 0.0)
    notional = float(row.notional_usd or 0.0)
    if entry <= 0.0 or notional <= 0.0:
        return 0.0
    return notional / entry


def _position_pnl_from_mark(row: Stage17TailPosition, mark: float) -> float:
    shares = _position_shares(row)
    if shares <= 0.0:
        return 0.0
    return (shares * float(mark)) - float(row.notional_usd or 0.0)


def _market_platform_label(market: Market) -> str:
    if isinstance(market.source_payload, dict):
        p = str(market.source_payload.get("platform") or "").strip()
        if p:
            return p
    if getattr(market, "platform", None) is not None:
        return str(market.platform.name or "")
    return ""


def _calc_v2_notional_usd(
    *,
    settings: Settings,
    reference_balance_usd: float,
    confidence_score: float | None,
    market_prob: float,
) -> float:
    conf = min(1.0, max(0.1, float(confidence_score or 0.5)))
    koef = 1.0 / max(1e-6, float(market_prob))
    scale = min(float(koef) / 10.0, 2.0)
    base = float(reference_balance_usd) * max(0.0, float(settings.signal_tail_notional_pct))
    raw = base * conf * scale
    capped = min(float(settings.signal_tail_max_single_bet_usd), raw)
    return max(0.5, capped)


def _close_position(
    db: Session,
    *,
    row: Stage17TailPosition,
    mark: float,
    pnl: float,
    close_reason: str,
    fill_payload: dict[str, Any] | None = None,
) -> None:
    row.status = "CLOSED"
    row.closed_at = datetime.now(UTC)
    row.realized_pnl_usd = float(pnl)
    row.realized_multiplier = max(0.0, 1.0 + (float(pnl) / float(row.notional_usd or 1.0)))
    row.current_multiplier = row.realized_multiplier
    row.unrealized_pnl_usd = 0.0
    row.close_reason = close_reason
    db.add(
        Stage17TailFill(
            position_id=int(row.id),
            fill_price=float(mark),
            fill_size_usd=0.0,
            fee_usd=0.0,
            fill_payload=fill_payload or {"event": close_reason},
        )
    )


def _is_external_api_degraded(db: Session) -> tuple[bool, str]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=2)
    latest = db.scalar(
        select(JobRun)
        .where(JobRun.job_name == "provider_contract_checks")
        .where(JobRun.started_at >= cutoff)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    if latest is None:
        return False, "provider_checks_missing_recent"
    status = str(latest.status or "").upper()
    details = latest.details if isinstance(latest.details, dict) else {}
    if status == "FAILED":
        return True, "provider_checks_failed"
    if bool(details.get("has_blocking_issues")):
        return True, "provider_checks_blocking_issues"
    providers = details.get("providers")
    if isinstance(providers, list):
        failed = [p for p in providers if isinstance(p, dict) and not bool(p.get("ok"))]
        if failed:
            return True, f"provider_checks_failed_count:{len(failed)}"
    return False, "ok"


def run_stage17_tail_cycle(
    db: Session,
    *,
    settings: Settings,
    limit: int = 20,
    open_new: bool = True,
) -> dict[str, Any]:
    now_utc = datetime.now(UTC)
    if not bool(settings.signal_tail_enabled):
        return {
            "enabled": False,
            "opened": 0,
            "closed": 0,
            "skipped": 0,
            "reason": "signal_tail_disabled",
        }

    ref_balance = max(1.0, float(settings.signal_tail_reference_balance_usd))
    degraded, degraded_reason = _is_external_api_degraded(db)
    breaker_blocked, breaker_reason = check_tail_circuit_breaker(
        db,
        settings=settings,
        balance_usd=ref_balance,
        api_status={"degraded": degraded},
        lock_open_rows=True,
    )
    if breaker_blocked:
        return {
            "enabled": True,
            "opened": 0,
            "closed": 0,
            "skipped": 0,
            "breaker_blocked": True,
            "breaker_reason": f"{breaker_reason}:{degraded_reason}" if degraded else breaker_reason,
        }
    budget_pct = max(0.0, float(settings.signal_tail_budget_pct))
    budget_total = ref_balance * budget_pct
    category_limits = _category_limit_map(settings)
    category_used_map: dict[str, float] = {
        str(cat): float(used or 0.0)
        for cat, used in db.execute(
            select(
                Stage17TailPosition.tail_category,
                func.coalesce(func.sum(Stage17TailPosition.notional_usd), 0.0),
            )
            .where(Stage17TailPosition.status == "OPEN")
            .group_by(Stage17TailPosition.tail_category)
        )
    }
    open_notional_total = float(sum(float(v or 0.0) for v in category_used_map.values()))

    opened = 0
    closed = 0
    skipped = 0
    opened_alerts: list[dict[str, Any]] = []
    win_alerts: list[dict[str, Any]] = []
    if not _acquire_tail_cycle_lock_if_supported(db):
        return {
            "enabled": True,
            "opened": 0,
            "closed": 0,
            "skipped": 0,
            "breaker_blocked": True,
            "breaker_reason": "tail_cycle_lock_busy",
        }
    open_positions_count = int(
            db.scalar(select(func.count()).select_from(Stage17TailPosition).where(Stage17TailPosition.status == "OPEN"))
            or 0
    )
    if open_new and int(limit) > 0:
        if open_positions_count >= int(settings.signal_tail_max_positions_open):
            open_new = False
        candidates = list(
            db.scalars(
                select(Signal)
                .where(Signal.signal_type == SignalType.TAIL_EVENT_CANDIDATE)
                .order_by(
                    func.coalesce(Signal.divergence_score, 0.0).desc(),
                    func.coalesce(Signal.confidence_score, 0.0).desc(),
                    Signal.created_at.desc(),
                )
                .limit(max(1, int(limit)) * 5)
            )
        )
    else:
        candidates = []
    candidate_signal_ids = list({int(s.id) for s in candidates})
    candidate_market_ids = list({int(s.market_id) for s in candidates if s.market_id is not None})
    candidate_markets: dict[int, Market] = {}
    if candidate_market_ids:
        candidate_markets = {
            int(m.id): m
            for m in db.scalars(select(Market).where(Market.id.in_(candidate_market_ids)))
        }
    existing_signal_ids: set[int] = set()
    if candidate_signal_ids:
        existing_signal_ids = set(
            int(x)
            for x in db.scalars(
                select(Stage17TailPosition.signal_id).where(
                    Stage17TailPosition.signal_id.in_(candidate_signal_ids),
                )
            )
            if x is not None
        )
    open_market_ids: set[int] = set()
    if candidate_market_ids:
        open_market_ids = set(
            int(x)
            for x in db.scalars(
                select(Stage17TailPosition.market_id)
                .where(Stage17TailPosition.status == "OPEN")
                .where(Stage17TailPosition.market_id.in_(candidate_market_ids))
            )
            if x is not None
        )
    for signal in candidates:
        if opened >= max(1, int(limit)):
            break
        if (open_positions_count + opened) >= int(settings.signal_tail_max_positions_open):
            break
        # Skip if signal already has ANY position (open or closed) — prevents cyclic reopen.
        if int(signal.id) in existing_signal_ids:
            skipped += 1
            continue
        if int(signal.market_id or 0) in open_market_ids:
            skipped += 1
            continue
        market = candidate_markets.get(int(signal.market_id or 0))
        if market is None or _is_market_resolved(market, now=now_utc):
            skipped += 1
            continue
        metadata = signal.metadata_json if isinstance(signal.metadata_json, dict) else {}
        tail_category = str(metadata.get("tail_category") or "unknown")
        tail_variation = str(signal.signal_mode or metadata.get("tail_variation") or "tail_stability")
        direction = str(signal.signal_direction or metadata.get("tail_direction") or "YES").upper()
        if direction not in {"YES", "NO"}:
            direction = "YES"
        model_version = str(metadata.get("tail_base_rate_source") or "deterministic")
        input_hash = None
        prompt_version_hash = None
        reason_codes = metadata.get("reason_codes") if isinstance(metadata.get("reason_codes"), list) else None
        if tail_variation == "tail_narrative_fade":
            llm = evaluate_tail_stage7(
                settings=settings,
                signal=signal,
                market=market,
                tail_category=tail_category,
                market_prob=float(metadata.get("tail_market_prob") or market.probability_yes or 0.5),
                our_prob=float(metadata.get("tail_our_prob") or market.probability_yes or 0.5),
            )
            if str(llm.get("decision") or "KEEP").upper() == "SKIP":
                skipped += 1
                continue
            # v2: direction is always YES (bet_yes_underpriced). LLM can only SKIP, not flip direction.
            model_version = f"{llm.get('provider') or 'none'}:{llm.get('model_version') or 'none'}"
            input_hash = str(llm.get("input_hash") or "") or None
            prompt_version_hash = str(llm.get("prompt_version_hash") or "") or None
            llm_reasons = llm.get("reason_codes")
            if isinstance(llm_reasons, list) and llm_reasons:
                reason_codes = [str(x) for x in llm_reasons if str(x).strip()]
        market_prob = float(metadata.get("tail_market_prob") or market.probability_yes or 0.5)
        our_prob = float(metadata.get("tail_our_prob") or market_prob)
        koef = 1.0 / max(1e-6, market_prob)
        notional_usd = _calc_v2_notional_usd(
            settings=settings,
            reference_balance_usd=ref_balance,
            confidence_score=signal.confidence_score,
            market_prob=market_prob,
        )
        if (open_notional_total + float(notional_usd)) > budget_total:
            skipped += 1
            continue
        cap_pct = float(category_limits.get(str(tail_category), 0.01))
        used_cat = float(category_used_map.get(str(tail_category), 0.0))
        if ((used_cat + float(notional_usd)) / max(1e-9, ref_balance)) > cap_pct:
            skipped += 1
            continue
        entry_price = _side_price(market, direction)
        resolution_deadline = market.resolution_time
        if resolution_deadline is not None:
            ref_deadline = resolution_deadline if resolution_deadline.tzinfo else resolution_deadline.replace(tzinfo=UTC)
            max_days = max(1, int(settings.signal_tail_max_days_to_resolution))
            if (ref_deadline - now_utc).total_seconds() / 86400.0 > max_days:
                skipped += 1
                continue
        row = Stage17TailPosition(
            signal_id=signal.id,
            market_id=market.id,
            tail_category=tail_category,
            tail_variation=tail_variation,
            direction=direction,
            status="OPEN",
            entry_price=entry_price,
            mark_price=entry_price,
            notional_usd=notional_usd,
            shares_count=(notional_usd / entry_price) if entry_price > 0 else None,
            base_rate_prob=our_prob,
            market_prob=market_prob,
            mispricing_ratio=float(metadata.get("tail_mispricing_ratio") or 0.0) or None,
            koef_entry=koef,
            our_prob_entry=our_prob,
            days_to_resolution_entry=float(metadata.get("tail_days_to_resolution") or 0.0) or None,
            reason_codes=reason_codes,
            input_hash=input_hash,
            model_version=model_version,
            prompt_version=prompt_version_hash or str(settings.signal_tail_llm_prompt_version),
            peak_mark_price=entry_price,
            current_multiplier=1.0 if entry_price > 0 else None,
            resolution_deadline=resolution_deadline,
        )
        db.add(row)
        db.flush()
        db.add(
            Stage17TailFill(
                position_id=int(row.id),
                fill_price=entry_price,
                fill_size_usd=notional_usd,
                fee_usd=0.0,
                fill_payload={"event": "open"},
            )
        )
        opened_alerts.append(
            {
                "signal_id": int(signal.id),
                "market_id": int(market.id),
                "title": str(market.title or signal.title or ""),
                "tail_category": tail_category,
                "koef": round(float(koef), 2),
                "market_prob": round(float(market_prob), 6),
                "our_prob": round(float(our_prob), 6),
                "notional_usd": round(float(notional_usd), 2),
                "days_to_resolution": int(round(float(metadata.get("tail_days_to_resolution") or 0.0))),
                "platform": _market_platform_label(market),
                "direction": direction,
            }
        )
        existing_signal_ids.add(int(signal.id))
        if signal.market_id is not None:
            open_market_ids.add(int(signal.market_id))
        open_notional_total += float(notional_usd)
        category_used_map[str(tail_category)] = used_cat + float(notional_usd)
        opened += 1

    open_positions = list(
        db.scalars(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.status == "OPEN")
            .order_by(Stage17TailPosition.opened_at.asc())
        )
    )
    open_position_market_ids = list({int(r.market_id) for r in open_positions if r.market_id is not None})
    open_markets: dict[int, Market] = {}
    if open_position_market_ids:
        open_markets = {
            int(m.id): m
            for m in db.scalars(select(Market).where(Market.id.in_(open_position_market_ids)))
        }
    for row in open_positions:
        market = open_markets.get(int(row.market_id or 0))
        if market is None:
            continue
        direction = str(row.direction or "NO").upper()
        mark = _side_price(market, direction)
        # Stage17 v2 supports only YES tail positions. Retire legacy NO rows.
        if direction == "NO":
            pnl = _position_pnl_from_mark(row, float(mark))
            _close_position(
                db,
                row=row,
                mark=mark,
                pnl=pnl,
                close_reason="legacy_direction_no_retired",
            )
            closed += 1
            continue
        row.mark_price = mark
        if row.entry_price and row.entry_price > 0:
            cur_mult = float(mark) / float(row.entry_price)
            row.current_multiplier = cur_mult
            peak = float(row.peak_mark_price or row.entry_price)
            row.peak_mark_price = max(peak, float(mark))
        if row.entry_price and row.entry_price > 0:
            row.unrealized_pnl_usd = _position_pnl_from_mark(row, float(mark))
        # v2 exits for YES-tail strategy.
        if direction == "YES" and row.entry_price and row.entry_price > 0:
            take_profit_mark = float(row.entry_price) * (1.0 + float(settings.signal_tail_take_profit_ratio))
            stop_loss_floor_mark = float(row.entry_price) * float(settings.signal_tail_stop_loss_floor_mult)
            if float(mark) >= take_profit_mark:
                pnl = _position_pnl_from_mark(row, float(mark))
                _close_position(
                    db,
                    row=row,
                    mark=mark,
                    pnl=pnl,
                    close_reason="take_profit_50",
                )
                closed += 1
                continue
            if float(mark) <= stop_loss_floor_mark:
                pnl = _position_pnl_from_mark(row, float(mark))
                _close_position(
                    db,
                    row=row,
                    mark=mark,
                    pnl=pnl,
                    close_reason="stop_loss_floor",
                )
                closed += 1
                continue
            if row.resolution_deadline is not None:
                dl = row.resolution_deadline if row.resolution_deadline.tzinfo else row.resolution_deadline.replace(tzinfo=UTC)
                days_left = (dl - now_utc).total_seconds() / 86400.0
                if days_left <= float(settings.signal_tail_days_before_resolution_exit):
                    min_mark = float(row.entry_price) * float(settings.signal_tail_min_mark_to_hold_mult)
                    if float(mark) < min_mark:
                        pnl = _position_pnl_from_mark(row, float(mark))
                        _close_position(
                            db,
                            row=row,
                            mark=mark,
                            pnl=pnl,
                            close_reason="deadline_exit",
                        )
                        closed += 1
                        continue
        if not _is_market_resolved(market, now=now_utc):
            continue
        outcome_yes = _resolved_yes(market)
        if outcome_yes is None:
            continue
        won = bool(outcome_yes) if direction == "YES" else (not bool(outcome_yes))
        if won and row.entry_price > 0:
            pnl = float(row.notional_usd) * ((1.0 - float(row.entry_price)) / float(row.entry_price))
        else:
            pnl = -float(row.notional_usd)
        close_reason = "resolved_win" if won else "resolved_loss"
        _close_position(
            db,
            row=row,
            mark=mark,
            pnl=pnl,
            close_reason=close_reason,
            fill_payload={"event": close_reason, "outcome_yes": bool(outcome_yes)},
        )
        if won:
            win_alerts.append(
                {
                    "position_id": int(row.id),
                    "signal_id": int(row.signal_id) if row.signal_id is not None else None,
                    "market_id": int(row.market_id),
                    "title": str(market.title or ""),
                    "koef": round(float(row.koef_entry or (1.0 / max(1e-6, float(row.entry_price or 0.5)))), 4),
                    "profit_usd": round(float(pnl), 4),
                }
            )
        closed += 1

    db.commit()
    return {
        "enabled": True,
        "opened": opened,
        "closed": closed,
        "skipped": skipped,
        "breaker_blocked": False,
        "opened_alerts": opened_alerts,
        "win_alerts": win_alerts,
    }
