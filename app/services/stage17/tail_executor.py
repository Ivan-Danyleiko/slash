from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import SignalType
from app.models.models import JobRun, Market, Signal, Stage17TailFill, Stage17TailPosition
from app.services.agent_stage7.tail_stage7 import evaluate_tail_stage7
from app.services.signals.tail_circuit_breaker import can_open_tail_by_category, check_tail_circuit_breaker


def _is_market_resolved(market: Market) -> bool:
    now = datetime.now(UTC)
    rt = market.resolution_time
    if rt is not None:
        ref = rt.astimezone(UTC) if rt.tzinfo else rt.replace(tzinfo=UTC)
        if ref <= now:
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
    for signal in candidates:
        if opened >= max(1, int(limit)):
            break
        if (open_positions_count + opened) >= int(settings.signal_tail_max_positions_open):
            break
        existing = db.scalar(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.signal_id == signal.id)
            .where(Stage17TailPosition.status == "OPEN")
            .limit(1)
        )
        if existing is not None:
            skipped += 1
            continue
        existing_market = db.scalar(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.market_id == signal.market_id)
            .where(Stage17TailPosition.status == "OPEN")
            .limit(1)
        )
        if existing_market is not None:
            skipped += 1
            continue
        market = db.get(Market, signal.market_id)
        if market is None or _is_market_resolved(market):
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
            d = str(llm.get("direction") or direction).upper()
            if d in {"YES", "NO"}:
                direction = d
            model_version = f"{llm.get('provider') or 'none'}:{llm.get('model_version') or 'none'}"
            input_hash = str(llm.get("input_hash") or "") or None
            prompt_version_hash = str(llm.get("prompt_version_hash") or "") or None
            llm_reasons = llm.get("reason_codes")
            if isinstance(llm_reasons, list) and llm_reasons:
                reason_codes = [str(x) for x in llm_reasons if str(x).strip()]
        breaker_blocked, _loop_reason = check_tail_circuit_breaker(
            db,
            settings=settings,
            balance_usd=ref_balance,
            api_status={"degraded": degraded},
            lock_open_rows=False,
        )
        if breaker_blocked:
            skipped += 1
            continue
        market_prob = float(metadata.get("tail_market_prob") or market.probability_yes or 0.5)
        our_prob = float(metadata.get("tail_our_prob") or market_prob)
        koef = 1.0 / max(1e-6, market_prob)
        notional_usd = _calc_v2_notional_usd(
            settings=settings,
            reference_balance_usd=ref_balance,
            confidence_score=signal.confidence_score,
            market_prob=market_prob,
        )
        allowed, _cat_reason = can_open_tail_by_category(
            db,
            settings=settings,
            category=tail_category,
            notional_usd=notional_usd,
            balance_usd=ref_balance,
            lock_open_rows=False,
        )
        if not allowed:
            skipped += 1
            continue
        entry_price = _side_price(market, direction)
        resolution_deadline = market.resolution_time
        if resolution_deadline is not None:
            ref_deadline = resolution_deadline if resolution_deadline.tzinfo else resolution_deadline.replace(tzinfo=UTC)
            max_days = max(1, int(settings.signal_tail_max_days_to_resolution))
            if (ref_deadline - datetime.now(UTC)).total_seconds() / 86400.0 > max_days:
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
                "koef": round(float(koef), 4),
                "market_prob": round(float(market_prob), 6),
                "our_prob": round(float(our_prob), 6),
                "notional_usd": round(float(notional_usd), 4),
                "direction": direction,
            }
        )
        opened += 1

    open_positions = list(
        db.scalars(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.status == "OPEN")
            .order_by(Stage17TailPosition.opened_at.asc())
        )
    )
    for row in open_positions:
        market = db.get(Market, row.market_id)
        if market is None:
            continue
        direction = str(row.direction or "NO").upper()
        mark = _side_price(market, direction)
        # Stage17 v2 supports only YES tail positions. Retire legacy NO rows.
        if direction == "NO":
            if row.entry_price and row.entry_price > 0 and row.notional_usd and row.notional_usd > 0:
                shares = float(row.notional_usd) / float(row.entry_price)
                pnl = (shares * float(mark)) - float(row.notional_usd)
            else:
                pnl = 0.0
            row.status = "CLOSED"
            row.closed_at = datetime.now(UTC)
            row.realized_pnl_usd = pnl
            row.realized_multiplier = max(0.0, 1.0 + (pnl / float(row.notional_usd or 1.0)))
            row.current_multiplier = row.realized_multiplier
            row.unrealized_pnl_usd = 0.0
            row.close_reason = "legacy_direction_no_retired"
            db.add(
                Stage17TailFill(
                    position_id=int(row.id),
                    fill_price=mark,
                    fill_size_usd=0.0,
                    fee_usd=0.0,
                    fill_payload={"event": row.close_reason},
                )
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
            shares = float(row.notional_usd) / float(row.entry_price)
            mark_value = shares * mark
            row.unrealized_pnl_usd = mark_value - float(row.notional_usd)
        # v2 exits for YES-tail strategy.
        if direction == "YES" and row.entry_price and row.entry_price > 0:
            take_profit_mark = float(row.entry_price) * (1.0 + float(settings.signal_tail_take_profit_ratio))
            stop_loss_floor_mark = float(row.entry_price) * float(settings.signal_tail_stop_loss_floor_mult)
            if float(mark) >= take_profit_mark:
                shares = float(row.notional_usd) / float(row.entry_price)
                pnl = (shares * float(mark)) - float(row.notional_usd)
                row.status = "CLOSED"
                row.closed_at = datetime.now(UTC)
                row.realized_pnl_usd = pnl
                row.realized_multiplier = max(0.0, 1.0 + (pnl / float(row.notional_usd or 1.0)))
                row.current_multiplier = row.realized_multiplier
                row.unrealized_pnl_usd = 0.0
                row.close_reason = "take_profit_50"
                db.add(
                    Stage17TailFill(
                        position_id=int(row.id),
                        fill_price=mark,
                        fill_size_usd=0.0,
                        fee_usd=0.0,
                        fill_payload={"event": row.close_reason},
                    )
                )
                closed += 1
                continue
            if float(mark) <= stop_loss_floor_mark:
                shares = float(row.notional_usd) / float(row.entry_price)
                pnl = (shares * float(mark)) - float(row.notional_usd)
                row.status = "CLOSED"
                row.closed_at = datetime.now(UTC)
                row.realized_pnl_usd = pnl
                row.realized_multiplier = max(0.0, 1.0 + (pnl / float(row.notional_usd or 1.0)))
                row.current_multiplier = row.realized_multiplier
                row.unrealized_pnl_usd = 0.0
                row.close_reason = "stop_loss_floor"
                db.add(
                    Stage17TailFill(
                        position_id=int(row.id),
                        fill_price=mark,
                        fill_size_usd=0.0,
                        fee_usd=0.0,
                        fill_payload={"event": row.close_reason},
                    )
                )
                closed += 1
                continue
            if row.resolution_deadline is not None:
                dl = row.resolution_deadline if row.resolution_deadline.tzinfo else row.resolution_deadline.replace(tzinfo=UTC)
                days_left = (dl - datetime.now(UTC)).total_seconds() / 86400.0
                if days_left <= float(settings.signal_tail_days_before_resolution_exit):
                    min_mark = float(row.entry_price) * float(settings.signal_tail_min_mark_to_hold_mult)
                    if float(mark) < min_mark:
                        shares = float(row.notional_usd) / float(row.entry_price)
                        pnl = (shares * float(mark)) - float(row.notional_usd)
                        row.status = "CLOSED"
                        row.closed_at = datetime.now(UTC)
                        row.realized_pnl_usd = pnl
                        row.realized_multiplier = max(0.0, 1.0 + (pnl / float(row.notional_usd or 1.0)))
                        row.current_multiplier = row.realized_multiplier
                        row.unrealized_pnl_usd = 0.0
                        row.close_reason = "deadline_exit"
                        db.add(
                            Stage17TailFill(
                                position_id=int(row.id),
                                fill_price=mark,
                                fill_size_usd=0.0,
                                fee_usd=0.0,
                                fill_payload={"event": row.close_reason},
                            )
                        )
                        closed += 1
                        continue
        if not _is_market_resolved(market):
            continue
        outcome_yes = _resolved_yes(market)
        if outcome_yes is None:
            continue
        won = bool(outcome_yes) if direction == "YES" else (not bool(outcome_yes))
        if won and row.entry_price > 0:
            pnl = float(row.notional_usd) * ((1.0 - float(row.entry_price)) / float(row.entry_price))
        else:
            pnl = -float(row.notional_usd)
        row.status = "CLOSED"
        row.closed_at = datetime.now(UTC)
        row.realized_pnl_usd = pnl
        row.realized_multiplier = max(0.0, 1.0 + (pnl / float(row.notional_usd or 1.0)))
        row.current_multiplier = row.realized_multiplier
        row.unrealized_pnl_usd = 0.0
        row.close_reason = "resolved_win" if won else "resolved_loss"
        db.add(
            Stage17TailFill(
                position_id=int(row.id),
                fill_price=mark,
                fill_size_usd=0.0,
                fee_usd=0.0,
                fill_payload={"event": row.close_reason, "outcome_yes": bool(outcome_yes)},
            )
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
