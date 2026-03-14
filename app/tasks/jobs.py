from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import SignalType
from app.models.models import JobRun, Market, MarketSnapshot, Signal, SignalHistory, SignalQualityMetrics, User
from app.services.collectors.sync_service import CollectorSyncService
from app.services.signals.engine import SignalEngine
from app.services.signals.ranking import rank_score, select_top_signals
from app.services.telegram_product import TelegramProductService


def _start_job(db: Session, name: str) -> JobRun:
    job = JobRun(job_name=name, status="RUNNING", details={})
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _finish_job(db: Session, job: JobRun, status: str, details: dict) -> None:
    # Ensure we can persist job status even if a previous statement failed in the session.
    db.rollback()
    job.status = status
    job.details = details
    job.finished_at = datetime.utcnow()
    db.commit()


def sync_all_platforms_job(db: Session, platform: str | None = None) -> dict:
    job = _start_job(db, "sync_all_platforms")
    try:
        result = CollectorSyncService(db).sync_all(platform=platform)
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def analyze_markets_job(db: Session) -> dict:
    job = _start_job(db, "analyze_markets")
    try:
        result = SignalEngine(db).run()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def detect_duplicates_job(db: Session) -> dict:
    job = _start_job(db, "detect_duplicates")
    try:
        result = SignalEngine(db).detect_duplicates()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def analyze_rules_job(db: Session) -> dict:
    job = _start_job(db, "analyze_rules")
    try:
        result = SignalEngine(db).analyze_rules()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def detect_divergence_job(db: Session) -> dict:
    job = _start_job(db, "detect_divergence")
    try:
        result = SignalEngine(db).detect_divergence()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def generate_signals_job(db: Session) -> dict:
    job = _start_job(db, "generate_signals")
    try:
        result = SignalEngine(db).generate_signals()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def send_test_signal_job(db: Session) -> dict:
    latest = db.query(Signal).order_by(Signal.id.desc()).first()
    return {
        "status": "ok",
        "message": "Telegram send is wired in bot service; this endpoint returns preview for MVP.",
        "latest_signal": latest.title if latest else None,
    }


def daily_digest_job(db: Session) -> dict:
    job = _start_job(db, "daily_digest")
    try:
        svc = TelegramProductService(db)
        users = list(db.scalars(select(User)))
        for user in users:
            user.signals_sent_today = 0
        db.commit()
        sent = 0
        for user in users:
            svc.daily_digest(user)
            sent += 1
        _finish_job(db, job, "SUCCESS", {"digests_sent": sent})
        return {"status": "ok", "result": {"digests_sent": sent}}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def signal_push_job(db: Session) -> dict:
    job = _start_job(db, "signal_push")
    try:
        settings = get_settings()
        if not settings.telegram_bot_token:
            _finish_job(db, job, "SUCCESS", {"signals_prepared": 0, "signals_sent": 0, "note": "bot token missing"})
            return {"status": "ok", "result": {"signals_prepared": 0, "signals_sent": 0}}

        svc = TelegramProductService(db)
        users = list(db.scalars(select(User)))
        sent = 0
        prepared = 0
        for user in users:
            top = svc.top_ranked_signals(user=user, limit=5)
            top = [s for s in top if rank_score(s) > 0.1][:5]
            prepared += len(top)
            for signal in top:
                if not svc.can_send_signal(user, 1):
                    break
                ex = signal.execution_analysis or {}
                utility = float(ex.get("utility_score") or 0.0)
                expected_edge = float(ex.get("expected_edge") or 0.0)
                slippage_edge = float(ex.get("slippage_adjusted_edge") or 0.0)
                cost_impact = max(0.0, expected_edge - slippage_edge)
                assumptions = str(ex.get("assumptions_version") or "n/a")
                if signal.signal_type == SignalType.ARBITRAGE_CANDIDATE:
                    metric_label = "Recent move"
                    metric_value = float((signal.divergence_score if signal.divergence_score is not None else 0.0) * 100.0)
                else:
                    metric_label = "Divergence"
                    metric_value = float((signal.divergence_score if signal.divergence_score is not None else 0.0) * 100.0)
                text = (
                    f"🔥 *{signal.signal_type.value}*\\n"
                    f"{signal.title}\\n"
                    f"Confidence: {signal.confidence_score or 0:.2f}\\n"
                    f"{metric_label}: {metric_value:.1f}%\\n"
                    f"Utility (exec): {utility:.3f}\\n"
                    f"Edge after costs: {slippage_edge:.3f} (cost impact: {cost_impact:.3f})\\n"
                    f"Execution assumptions: `{assumptions}`\\n"
                    f"_Disclaimer: {settings.research_ethics_disclaimer_text}_"
                )
                resp = httpx.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": user.telegram_user_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    svc.record_signal_sent(user, signal)
                    sent += 1
        _finish_job(db, job, "SUCCESS", {"signals_prepared": prepared, "signals_sent": sent})
        return {"status": "ok", "result": {"signals_prepared": prepared, "signals_sent": sent}}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def cleanup_old_signals_job(db: Session, keep_days: int = 30) -> dict:
    job = _start_job(db, "cleanup_old_signals")
    try:
        cutoff = datetime.utcnow().replace(microsecond=0) - timedelta(days=keep_days)  # type: ignore[name-defined]
        stmt = delete(Signal).where(Signal.created_at < cutoff)
        deleted = db.execute(stmt).rowcount or 0
        db.commit()
        _finish_job(db, job, "SUCCESS", {"deleted": deleted})
        return {"status": "ok", "result": {"deleted": deleted}}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def update_watchlists_job(db: Session) -> dict:
    job = _start_job(db, "update_watchlists")
    try:
        # Placeholder for future diff-based watchlist alerts.
        _finish_job(db, job, "SUCCESS", {"watchlists_checked": True})
        return {"status": "ok", "result": {"watchlists_checked": True}}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def provider_contract_checks_job(db: Session) -> dict:
    job = _start_job(db, "provider_contract_checks")
    settings = get_settings()

    def _check(name: str, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        started = datetime.now(UTC)
        try:
            resp = httpx.get(url, params=params, headers=headers or {}, timeout=15.0)
            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            ok = False
            detail = ""
            if resp.status_code == 200:
                body = resp.json()
                ok = isinstance(body, (list, dict))
                if isinstance(body, dict):
                    detail = ",".join(sorted(body.keys())[:8])
                elif isinstance(body, list) and body:
                    detail = f"list_len={len(body)}"
            else:
                detail = (resp.text or "")[:160]
            return {
                "provider": name,
                "ok": bool(ok),
                "status_code": int(resp.status_code),
                "latency_ms": latency_ms,
                "url": url,
                "detail": detail,
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            return {
                "provider": name,
                "ok": False,
                "status_code": None,
                "latency_ms": latency_ms,
                "url": url,
                "detail": str(exc)[:200],
            }

    checks: list[dict] = []
    checks.append(
        _check(
            "MANIFOLD",
            f"{settings.manifold_api_base_url}/markets",
            params={"limit": 1},
        )
    )
    checks.append(
        _check(
            "POLYMARKET",
            f"{settings.polymarket_api_base_url}/markets",
            params={"limit": 1},
        )
    )
    if settings.metaculus_api_token:
        checks.append(
            _check(
                "METACULUS",
                f"{settings.metaculus_api_base_url}/questions/",
                params={"limit": 1},
                headers={
                    "Authorization": f"Token {settings.metaculus_api_token}",
                    "Accept": "application/json",
                    "User-Agent": settings.metaculus_user_agent,
                },
            )
        )
    else:
        checks.append(
            {
                "provider": "METACULUS",
                "ok": False,
                "status_code": None,
                "latency_ms": 0,
                "url": f"{settings.metaculus_api_base_url}/questions/",
                "detail": "METACULUS_API_TOKEN missing",
            }
        )

    failed = [c for c in checks if not c.get("ok")]
    summary = {
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks_passed": len(checks) - len(failed),
        "providers": checks,
        "has_blocking_issues": len(failed) > 0,
    }
    status = "SUCCESS" if not failed else "FAILED"
    _finish_job(db, job, status, summary)
    return {"status": ("ok" if not failed else "error"), "result": summary}


def quality_snapshot_job(db: Session) -> dict:
    job = _start_job(db, "quality_snapshot")
    try:
        settings = get_settings()
        now = datetime.now(UTC)
        day = now.date()
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        fresh_cutoff = now - timedelta(hours=settings.snapshot_fresh_hours)
        top_window = max(5, settings.top_window_size)

        rows = list(
            db.scalars(
                select(Signal).where(Signal.created_at >= day_start, Signal.created_at < day_end).order_by(Signal.created_at.desc())
            )
        )
        signals_by_type: dict[str, int] = {}
        signals_by_mode: dict[str, int] = {}
        avg_buf: dict[str, list[float]] = {}
        for s in rows:
            st = s.signal_type.value
            signals_by_type[st] = signals_by_type.get(st, 0) + 1
            mode = s.signal_mode or "untyped"
            signals_by_mode[mode] = signals_by_mode.get(mode, 0) + 1
            if s.confidence_score is not None:
                avg_buf.setdefault(st, []).append(float(s.confidence_score))
        avg_score_by_type = {k: round(sum(v) / len(v), 4) for k, v in avg_buf.items() if v}

        arb_rows = [s for s in rows if s.signal_type == SignalType.ARBITRAGE_CANDIDATE]
        momentum_rows = [s for s in arb_rows if (s.signal_mode or "").lower() == "momentum"]
        zero_move_count = 0
        for s in momentum_rows:
            mv = float((s.metadata_json or {}).get("recent_move", 0) or 0)
            if mv <= 1e-9:
                zero_move_count += 1
        zero_move_arbitrage_ratio = (zero_move_count / len(momentum_rows)) if momentum_rows else 0.0

        top_rows = select_top_signals(rows, limit=top_window, settings=settings)
        top_missing_rules = sum(1 for s in top_rows if (s.signal_mode or "") == "missing_rules_risk")
        missing_rules_share = (top_missing_rules / len(top_rows)) if top_rows else 0.0

        actionable = sum(1 for s in rows if (s.liquidity_score or 0.0) >= 0.6 and (s.confidence_score or 0.0) >= 0.4)
        actionable_rate = (actionable / len(rows)) if rows else 0.0
        exec_edges: list[float] = []
        exec_utilities: list[float] = []
        for s in rows:
            ex = s.execution_analysis or {}
            edge = ex.get("slippage_adjusted_edge")
            util = ex.get("utility_score")
            if isinstance(edge, (int, float)):
                exec_edges.append(float(edge))
            if isinstance(util, (int, float)):
                exec_utilities.append(float(util))
        simulated_edge_mean = (sum(exec_edges) / len(exec_edges)) if exec_edges else 0.0
        simulated_edge_p10 = 0.0
        if exec_edges:
            sorted_edges = sorted(exec_edges)
            p10_idx = max(0, int(0.1 * (len(sorted_edges) - 1)))
            simulated_edge_p10 = sorted_edges[p10_idx]
        top5_utility_daily = sum(sorted(exec_utilities, reverse=True)[:5]) if exec_utilities else 0.0

        markets_ingested = int(db.scalar(select(func.count()).select_from(Market)) or 0)
        markets_with_prob = int(
            db.scalar(select(func.count()).select_from(Market).where(Market.probability_yes.is_not(None))) or 0
        )
        markets_with_rules = int(
            db.scalar(
                select(func.count()).select_from(Market).where(Market.rules_text.is_not(None), Market.rules_text != "")
            )
            or 0
        )
        markets_with_fresh_snapshot = int(
            db.scalar(
                select(func.count(func.distinct(MarketSnapshot.market_id)))
                .select_from(MarketSnapshot)
                .where(MarketSnapshot.fetched_at >= fresh_cutoff)
            )
            or 0
        )
        snapshots_fresh_ratio = (markets_with_fresh_snapshot / markets_ingested) if markets_ingested else 0.0

        metric = db.scalar(select(SignalQualityMetrics).where(SignalQualityMetrics.date == day))
        if not metric:
            metric = SignalQualityMetrics(date=day)
            db.add(metric)

        metric.markets_ingested = markets_ingested
        metric.markets_with_prob = markets_with_prob
        metric.markets_with_rules = markets_with_rules
        metric.snapshots_fresh_ratio = round(snapshots_fresh_ratio, 4)
        metric.pairs_generated = int(db.scalar(select(func.count()).select_from(Signal).where(Signal.signal_type == SignalType.DUPLICATE_MARKET, Signal.created_at >= day_start, Signal.created_at < day_end)) or 0)
        metric.pairs_filtered = 0
        metric.rules_candidates_total = int(signals_by_type.get(SignalType.RULES_RISK.value, 0))
        metric.arbitrage_candidates_total = int(signals_by_type.get(SignalType.ARBITRAGE_CANDIDATE.value, 0))
        metric.signals_by_type = signals_by_type
        metric.signals_by_mode = signals_by_mode
        metric.avg_score_by_type = avg_score_by_type
        metric.zero_move_arbitrage_ratio = round(zero_move_arbitrage_ratio, 4)
        metric.missing_rules_share = round(missing_rules_share, 4)
        metric.actionable_rate = round(actionable_rate, 4)
        metric.simulated_edge_mean = round(simulated_edge_mean, 4)
        metric.simulated_edge_p10 = round(simulated_edge_p10, 4)
        metric.top5_utility_daily = round(top5_utility_daily, 4)

        db.commit()
        result = {
            "date": str(day),
            "signals_total": len(rows),
            "signals_by_type": signals_by_type,
            "signals_by_mode": signals_by_mode,
            "zero_move_arbitrage_ratio": round(zero_move_arbitrage_ratio, 4),
            "missing_rules_share": round(missing_rules_share, 4),
            "actionable_rate": round(actionable_rate, 4),
            "simulated_edge_mean": round(simulated_edge_mean, 4),
            "simulated_edge_p10": round(simulated_edge_p10, 4),
            "top5_utility_daily": round(top5_utility_daily, 4),
        }
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _market_is_resolved(market: Market, *, now: datetime) -> bool:
    if _as_utc(market.resolution_time) and _as_utc(market.resolution_time) <= now:
        return True
    status = (market.status or "").strip().lower()
    if any(token in status for token in ("resolved", "closed", "settled", "final", "ended")):
        return True
    payload = market.source_payload or {}
    if isinstance(payload.get("isResolved"), bool) and payload.get("isResolved"):
        return True
    if isinstance(payload.get("resolved"), bool) and payload.get("resolved"):
        return True
    return False


def _extract_resolved_probability(market: Market) -> float | None:
    payload = market.source_payload or {}
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

    if market.probability_yes is not None:
        return float(market.probability_yes)
    return None


def _label_signal_history_horizon(db: Session, *, hours: int, field_name: str) -> dict:
    job = _start_job(db, f"label_signal_history_{hours}h")
    try:
        now = datetime.now(UTC)
        target = now - timedelta(hours=hours)
        field = getattr(SignalHistory, field_name)

        rows = list(
            db.scalars(
                select(SignalHistory)
                .where(
                    SignalHistory.timestamp <= target,
                    field.is_(None),
                )
                .order_by(SignalHistory.timestamp.asc())
            )
        )

        updated = 0
        skipped_market_missing = 0
        skipped_probability_unavailable = 0
        for row in rows:
            market = db.get(Market, row.market_id)
            if not market:
                row.missing_label_reason = "market_missing"
                skipped_market_missing += 1
                continue
            prob = market.probability_yes
            if prob is None:
                row.missing_label_reason = "probability_unavailable"
                skipped_probability_unavailable += 1
                continue
            setattr(row, field_name, float(prob))
            row.labeled_at = now
            row.missing_label_reason = None
            updated += 1

        db.commit()
        result = {
            "horizon_hours": hours,
            "target_before_ts": target.isoformat(),
            "candidates": len(rows),
            "updated": updated,
            "skipped_market_missing": skipped_market_missing,
            "skipped_probability_unavailable": skipped_probability_unavailable,
        }
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def _label_signal_history_subhour(db: Session, *, minutes: int, key_name: str) -> dict:
    job = _start_job(db, f"label_signal_history_{minutes}m")
    try:
        now = datetime.now(UTC)
        target = now - timedelta(minutes=minutes)
        tolerance = max(1, int(get_settings().signal_labeling_tolerance_minutes))

        rows = list(
            db.scalars(
                select(SignalHistory)
                .where(SignalHistory.timestamp <= target)
                .order_by(SignalHistory.timestamp.asc())
            )
        )

        updated = 0
        skipped_market_missing = 0
        skipped_snapshot_missing = 0
        for row in rows:
            payload = dict(row.simulated_trade or {})
            if payload.get(key_name) is not None:
                continue
            market = db.get(Market, row.market_id)
            if not market:
                row.missing_label_reason = "market_missing"
                skipped_market_missing += 1
                continue

            target_ts = _as_utc(row.timestamp)
            if target_ts is None:
                row.missing_label_reason = "timestamp_missing"
                skipped_snapshot_missing += 1
                continue
            target_ts = target_ts + timedelta(minutes=minutes)
            snap = db.scalar(
                select(MarketSnapshot)
                .where(
                    MarketSnapshot.market_id == row.market_id,
                    MarketSnapshot.fetched_at >= target_ts,
                    MarketSnapshot.fetched_at <= target_ts + timedelta(minutes=tolerance),
                )
                .order_by(MarketSnapshot.fetched_at.asc())
                .limit(1)
            )
            if not snap or snap.probability_yes is None:
                row.missing_label_reason = f"snapshot_{minutes}m_missing"
                skipped_snapshot_missing += 1
                continue

            payload[key_name] = float(snap.probability_yes)
            row.simulated_trade = payload
            row.labeled_at = now
            row.missing_label_reason = None
            updated += 1

        db.commit()
        result = {
            "horizon_minutes": minutes,
            "target_before_ts": target.isoformat(),
            "candidates": len(rows),
            "updated": updated,
            "skipped_market_missing": skipped_market_missing,
            "skipped_snapshot_missing": skipped_snapshot_missing,
            "store": "signal_history.simulated_trade",
            "key": key_name,
        }
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def label_signal_history_15m_job(db: Session) -> dict:
    return _label_signal_history_subhour(db, minutes=15, key_name="probability_after_15m")


def label_signal_history_30m_job(db: Session) -> dict:
    return _label_signal_history_subhour(db, minutes=30, key_name="probability_after_30m")


def label_signal_history_1h_job(db: Session) -> dict:
    return _label_signal_history_horizon(db, hours=1, field_name="probability_after_1h")


def label_signal_history_6h_job(db: Session) -> dict:
    return _label_signal_history_horizon(db, hours=6, field_name="probability_after_6h")


def label_signal_history_24h_job(db: Session) -> dict:
    return _label_signal_history_horizon(db, hours=24, field_name="probability_after_24h")


def label_signal_history_resolution_job(db: Session) -> dict:
    job = _start_job(db, "label_signal_history_resolution")
    try:
        now = datetime.now(UTC)
        rows = list(
            db.scalars(
                select(SignalHistory)
                .where(SignalHistory.resolution_checked_at.is_(None))
                .order_by(SignalHistory.timestamp.asc())
            )
        )
        checked = 0
        updated = 0
        skipped_not_resolved = 0
        skipped_no_probability = 0
        for row in rows:
            market = db.get(Market, row.market_id)
            if not market:
                row.missing_label_reason = "market_missing"
                continue
            if not _market_is_resolved(market, now=now):
                skipped_not_resolved += 1
                continue

            resolved_probability = _extract_resolved_probability(market)
            checked += 1
            if resolved_probability is None:
                skipped_no_probability += 1
                row.missing_label_reason = "resolved_probability_unavailable"
                continue

            row.resolved_probability = float(resolved_probability)
            if row.probability_at_signal is not None:
                row.resolved_success = bool(float(resolved_probability) > float(row.probability_at_signal))
            else:
                row.resolved_success = None
            row.resolution_checked_at = now
            row.missing_label_reason = None
            updated += 1

        db.commit()
        result = {
            "candidates": len(rows),
            "checked_resolved": checked,
            "updated": updated,
            "skipped_not_resolved": skipped_not_resolved,
            "skipped_no_probability": skipped_no_probability,
        }
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def cleanup_signal_history_job(db: Session) -> dict:
    job = _start_job(db, "cleanup_signal_history")
    try:
        settings = get_settings()
        cutoff = datetime.now(UTC) - timedelta(days=max(1, settings.signal_history_retention_days))
        deleted = (
            db.execute(
                delete(SignalHistory).where(SignalHistory.timestamp < _as_utc(cutoff))
            ).rowcount
            or 0
        )
        db.commit()
        result = {"deleted": int(deleted), "retention_days": settings.signal_history_retention_days}
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}
