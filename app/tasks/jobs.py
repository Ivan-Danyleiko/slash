from datetime import UTC, datetime, timedelta
from bisect import bisect_left

import httpx
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.secrets import redact_text
from app.models.enums import SignalType
from app.models.models import (
    DryrunPosition,
    JobRun,
    Market,
    MarketSnapshot,
    Signal,
    SignalHistory,
    SignalQualityMetrics,
    Stage7AgentDecision,
    Stage8Decision,
    Stage8Position,
    Stage10ReplayRow,
    Stage11Order,
    Stage17TailPosition,
    User,
)
from app.services.collectors.sync_service import CollectorSyncService
from app.services.research.stage7_shadow import build_stage7_shadow_report
from app.services.research.stage8_shadow_ledger import (
    build_stage8_shadow_ledger_report,
    extract_stage8_shadow_ledger_metrics,
)
from app.services.research.stage8_final_report import (
    build_stage8_final_report,
    extract_stage8_final_report_metrics,
)
from app.services.research.stage9_batch import build_stage9_batch_report
from app.services.research.stage10_batch import build_stage10_batch_report
from app.services.research.stage10_timeline_backfill_run import run_stage10_timeline_backfill
from app.services.research.stage17_batch import build_stage17_batch_report
from app.services.research.stage17_tail_report import (
    build_stage17_tail_report,
    extract_stage17_tail_report_metrics,
)
from app.services.research.signal_history_labeler import label_signal_history_from_snapshots
from app.services.stage11.reports import build_stage11_track_report
from app.services.stage11.order_manager import reconcile_orders
from app.services.stage17.tail_executor import run_stage17_tail_cycle
from app.services.research.tracking import record_stage5_experiment
from app.services.signals.engine import SignalEngine
from app.services.signals.ranking import rank_score, select_top_signals
from app.services.telegram_product import TelegramProductService
from app.utils.market_resolve import is_market_resolved as _market_is_resolved

_STAGE17_CATEGORY_EMOJI = {
    "price_target": "💰",
    "crypto_level": "💰",
    "sports_match": "🏆",
    "geopolitical_event": "🌍",
    "election": "🗳️",
    "earnings_surprise": "📈",
    "regulatory": "⚖️",
    "company_valuation": "🏢",
}


def _as_obj_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _escape_markdown_v2(text: str) -> str:
    out = str(text)
    for ch in r"\_*[]()~`>#+-=|{}.!":
        out = out.replace(ch, f"\\{ch}")
    return out


def _send_stage17_telegram_messages(settings, messages: list[str]) -> int:
    token = str(settings.telegram_bot_token or "").strip()
    chat_id = str(settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        return 0
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    try:
        with httpx.Client(timeout=10.0) as client:
            for text in messages:
                try:
                    resp = client.post(
                        url,
                        json={"chat_id": chat_id, "text": str(text), "disable_web_page_preview": True},
                    )
                    if int(resp.status_code) == 200:
                        sent += 1
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return sent
    return sent


def _build_stage17_open_message(item: dict) -> str:
    category = str(item.get("tail_category") or "")
    emoji = _STAGE17_CATEGORY_EMOJI.get(category, "🔥")
    koef = float(item.get("koef") or 0.0)
    our_pct = 100.0 * float(item.get("our_prob") or 0.0)
    mkt_pct = 100.0 * float(item.get("market_prob") or 0.0)
    bet = float(item.get("notional_usd") or 0.0)
    platform = str(item.get("platform") or "Unknown")
    days_to_resolution = int(item.get("days_to_resolution") or 0)
    return (
        "🔥 STAGE17 OPEN\n"
        f"{item.get('title')}\n"
        f"{emoji} {category} · {platform} · {days_to_resolution} днів\n"
        f"Koef x{koef:.2f} · our {our_pct:.1f}% vs mkt {mkt_pct:.1f}%\n"
        f"bet=${bet:.2f}"
    )


def _build_stage17_win_message(item: dict) -> str:
    return f"🎉 WIN x{item.get('koef')}: {item.get('title')} | +${item.get('profit_usd')}"


def _cleanup_stale_running_jobs(
    db: Session,
    *,
    job_name: str,
    stale_minutes: int,
) -> int:
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=max(1, int(stale_minutes)))
    stale_rows = list(
        db.scalars(
            select(JobRun).where(
                JobRun.job_name == job_name,
                JobRun.status == "RUNNING",
                JobRun.started_at < stale_before,
            )
        )
    )
    for stale in stale_rows:
        stale.status = "FAILED"
        stale.finished_at = now
        stale.details = {
            "error": "stale_timeout",
            "note": f"auto-closed by guard for {job_name}",
        }
    if stale_rows:
        db.commit()
    return len(stale_rows)


def _is_recent_running_job(db: Session, *, job_name: str, stale_minutes: int) -> bool:
    stale_before = datetime.now(UTC) - timedelta(minutes=max(1, int(stale_minutes)))
    running_exists = db.scalar(
        select(func.count())
        .select_from(JobRun)
        .where(
            JobRun.job_name == job_name,
            JobRun.status == "RUNNING",
            JobRun.started_at >= stale_before,
        )
    )
    return int(running_exists or 0) > 0


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
    job.finished_at = datetime.now(UTC)
    db.commit()


def _safe_error(exc: Exception, *, max_len: int = 240) -> str:
    return redact_text(str(exc), max_len=max_len)


def _run_job_with_guard(
    db: Session,
    *,
    job_name: str,
    stale_minutes: int,
    run_fn,
) -> dict:
    _cleanup_stale_running_jobs(db, job_name=job_name, stale_minutes=stale_minutes)
    if _is_recent_running_job(db, job_name=job_name, stale_minutes=stale_minutes):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, job_name)
    try:
        result = run_fn()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": _safe_error(exc)})
        return {"status": "error", "error": _safe_error(exc)}


def _run_job(
    db: Session,
    *,
    job_name: str,
    run_fn,
    details_fn=None,
    response_fn=None,
    stale_minutes: int | None = None,
) -> dict:
    if stale_minutes is not None:
        _cleanup_stale_running_jobs(db, job_name=job_name, stale_minutes=stale_minutes)
        if _is_recent_running_job(db, job_name=job_name, stale_minutes=stale_minutes):
            return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, job_name)
    try:
        payload = run_fn()
        details = details_fn(payload) if callable(details_fn) else payload
        _finish_job(db, job, "SUCCESS", details)
        if callable(response_fn):
            return response_fn(payload)
        return {"status": "ok", "result": payload}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": _safe_error(exc)})
        return {"status": "error", "error": _safe_error(exc)}


def _run_job_with_status_from_result(
    db: Session,
    *,
    job_name: str,
    run_fn,
    status_fn,
    response_fn=None,
) -> dict:
    job = _start_job(db, job_name)
    try:
        payload = run_fn()
        status = str(status_fn(payload)).upper()
        if status not in {"SUCCESS", "FAILED"}:
            status = "FAILED"
        _finish_job(db, job, status, payload if isinstance(payload, dict) else {"payload": payload})
        if callable(response_fn):
            return response_fn(payload, status)
        return {"status": "ok" if status == "SUCCESS" else "error", "result": payload}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": _safe_error(exc)})
        return {"status": "error", "error": _safe_error(exc)}


def sync_all_platforms_job(db: Session, platform: str | None = None) -> dict:
    return _run_job_with_guard(
        db,
        job_name="sync_all_platforms",
        stale_minutes=40,
        run_fn=lambda: CollectorSyncService(db).sync_all(platform=platform),
    )


def analyze_markets_job(db: Session) -> dict:
    return _run_job_with_guard(
        db,
        job_name="analyze_markets",
        stale_minutes=30,
        run_fn=lambda: SignalEngine(db).run(),
    )


def detect_duplicates_job(db: Session) -> dict:
    return _run_job_with_guard(
        db,
        job_name="detect_duplicates",
        stale_minutes=45,
        run_fn=lambda: SignalEngine(db).detect_duplicates(),
    )


def analyze_rules_job(db: Session) -> dict:
    return _run_job_with_guard(
        db,
        job_name="analyze_rules",
        stale_minutes=25,
        run_fn=lambda: SignalEngine(db).analyze_rules(),
    )


def detect_divergence_job(db: Session) -> dict:
    return _run_job_with_guard(
        db,
        job_name="detect_divergence",
        stale_minutes=30,
        run_fn=lambda: SignalEngine(db).detect_divergence(),
    )


def generate_signals_job(db: Session) -> dict:
    return _run_job_with_guard(
        db,
        job_name="generate_signals",
        stale_minutes=30,
        run_fn=lambda: SignalEngine(db).generate_signals(),
    )


def send_test_signal_job(db: Session) -> dict:
    latest = db.scalar(select(Signal).order_by(Signal.id.desc()))
    return {
        "status": "ok",
        "message": "Telegram send is wired in bot service; this endpoint returns preview for MVP.",
        "latest_signal": latest.title if latest else None,
    }


def daily_digest_job(db: Session) -> dict:
    def _run() -> dict:
        svc = TelegramProductService(db)
        users = list(db.scalars(select(User)))
        for user in users:
            user.signals_sent_today = 0
        db.commit()
        sent = 0
        for user in users:
            svc.daily_digest(user)
            sent += 1
        return {"digests_sent": sent}

    return _run_job(db, job_name="daily_digest", run_fn=_run)


def signal_push_job(db: Session) -> dict:
    def _run() -> dict:
        settings = get_settings()
        if not settings.telegram_bot_token:
            return {"signals_prepared": 0, "signals_sent": 0, "note": "bot token missing"}

        svc = TelegramProductService(db)
        users = list(db.scalars(select(User)))
        sent = 0
        prepared = 0
        skipped_by_reason: dict[str, int] = {}
        now = datetime.now(UTC)

        def _skip(reason: str) -> None:
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

        # Pre-load signal pool + market map once — avoids N+1 in user loops.
        signal_pool, markets_by_id = svc.load_signal_pool_with_markets()
        tg_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        with httpx.Client(timeout=10.0) as client:
            for user in users:
                top = svc.top_ranked_signals(
                    user=user,
                    limit=5,
                    pool=signal_pool,
                    log_variant_event=False,
                )
                top = [s for s in top if rank_score(s) > 0.1][:5]
                for signal in top:
                    if not svc.can_send_signal(user, 1):
                        break
                    market = markets_by_id.get(int(signal.market_id or 0))
                    if not market:
                        _skip("market_missing")
                        continue
                    if _market_is_resolved(market, now=now):
                        _skip("market_resolved_or_closed")
                        continue
                    ex = signal.execution_analysis or {}
                    utility = float(ex.get("utility_score") or 0.0)
                    expected_edge = float(ex.get("expected_edge") or 0.0)
                    slippage_edge = float(ex.get("slippage_adjusted_edge") or 0.0)
                    if slippage_edge == 0.0 and expected_edge != 0.0:
                        slippage_edge = expected_edge
                    min_edge_after_costs = 0.005
                    min_utility = max(float(settings.signal_top_min_utility_score), 0.01)
                    if slippage_edge <= min_edge_after_costs:
                        _skip("edge_after_costs_too_low")
                        continue
                    if utility <= min_utility:
                        _skip("utility_too_low")
                        continue
                    cost_impact = max(0.0, expected_edge - slippage_edge)
                    assumptions = str(ex.get("assumptions_version") or "n/a")
                    if signal.signal_type == SignalType.ARBITRAGE_CANDIDATE:
                        metric_label = "Recent move"
                        metric_value = float((signal.divergence_score if signal.divergence_score is not None else 0.0) * 100.0)
                        if metric_value <= 0.0:
                            _skip("recent_move_zero")
                            continue
                    else:
                        metric_label = "Divergence"
                        metric_value = float((signal.divergence_score if signal.divergence_score is not None else 0.0) * 100.0)
                    prepared += 1

                    text = (
                        f"🔥 *{_escape_markdown_v2(signal.signal_type.value)}*\n"
                        f"{_escape_markdown_v2(signal.title)}\n"
                        f"Confidence: {_escape_markdown_v2(f'{signal.confidence_score or 0:.2f}')}\n"
                        f"{_escape_markdown_v2(metric_label)}: {_escape_markdown_v2(f'{metric_value:.1f}%')}\n"
                        f"Utility \\(exec\\): {_escape_markdown_v2(f'{utility:.3f}')}\n"
                        f"Edge after costs: {_escape_markdown_v2(f'{slippage_edge:.3f}')} \\(cost impact: {_escape_markdown_v2(f'{cost_impact:.3f}')}\\)\n"
                        f"Execution assumptions: `{_escape_markdown_v2(assumptions)}`\n"
                        f"_Disclaimer: {_escape_markdown_v2(settings.research_ethics_disclaimer_text)}_"
                    )
                    resp = client.post(
                        tg_url,
                        json={
                            "chat_id": user.telegram_user_id,
                            "text": text,
                            "parse_mode": "MarkdownV2",
                            "disable_web_page_preview": True,
                        },
                    )
                    if resp.status_code == 200:
                        svc.record_signal_sent(user, signal, commit=False)
                        sent += 1
        if sent > 0:
            db.commit()
        result = {"signals_prepared": prepared, "signals_sent": sent, "skipped_by_reason": skipped_by_reason}
        return result

    return _run_job_with_guard(db, job_name="signal_push", stale_minutes=25, run_fn=_run)


def cleanup_old_signals_job(db: Session, keep_days: int = 30) -> dict:
    def _run() -> dict:
        cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(days=keep_days)
        has_stage7_ref = exists(
            select(Stage7AgentDecision.id).where(Stage7AgentDecision.signal_id == Signal.id)
        )
        has_stage8_ref = exists(
            select(Stage8Decision.id).where(Stage8Decision.signal_id == Signal.id)
        )
        has_stage8_position_ref = exists(
            select(Stage8Position.id).where(Stage8Position.signal_id == Signal.id)
        )
        has_signal_history_ref = exists(
            select(SignalHistory.id).where(SignalHistory.signal_id == Signal.id)
        )
        has_stage10_ref = exists(
            select(Stage10ReplayRow.id).where(Stage10ReplayRow.signal_id == Signal.id)
        )
        has_stage11_ref = exists(
            select(Stage11Order.id).where(Stage11Order.signal_id == Signal.id)
        )
        has_dryrun_ref = exists(
            select(DryrunPosition.id).where(DryrunPosition.signal_id == Signal.id)
        )
        has_stage17_ref = exists(
            select(Stage17TailPosition.id).where(Stage17TailPosition.signal_id == Signal.id)
        )
        stmt = delete(Signal).where(
            Signal.created_at < cutoff,
            ~has_stage7_ref,
            ~has_stage8_ref,
            ~has_stage8_position_ref,
            ~has_signal_history_ref,
            ~has_stage10_ref,
            ~has_stage11_ref,
            ~has_dryrun_ref,
            ~has_stage17_ref,
        )
        deleted = db.execute(stmt).rowcount or 0
        db.commit()
        return {"deleted": deleted}

    return _run_job_with_guard(db, job_name="cleanup_old_signals", stale_minutes=1380, run_fn=_run)


def update_watchlists_job(db: Session) -> dict:
    def _run() -> dict:
        # Placeholder for future diff-based watchlist alerts.
        return {"watchlists_checked": True}

    return _run_job(db, job_name="update_watchlists", run_fn=_run)


def provider_contract_checks_job(db: Session) -> dict:
    settings = get_settings()

    def _check(client: httpx.Client, name: str, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        started = datetime.now(UTC)
        try:
            resp = client.get(url, params=params, headers=headers or {})
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
                "detail": _safe_error(exc)[:200],
            }

    def _run() -> dict:
        checks: list[dict] = []
        with httpx.Client(timeout=15.0) as client:
            checks.append(
                _check(
                    client,
                    "MANIFOLD",
                    f"{settings.manifold_api_base_url}/markets",
                    params={"limit": 1},
                )
            )
            checks.append(
                _check(
                    client,
                    "POLYMARKET",
                    f"{settings.polymarket_api_base_url}/markets",
                    params={"limit": 1},
                )
            )
            if settings.kalshi_enabled:
                checks.append(
                    _check(
                        client,
                        "KALSHI",
                        f"{settings.kalshi_api_base_url}/markets",
                        params={"limit": 1, "status": "open"},
                        headers={"Authorization": f"Bearer {settings.kalshi_api_key}"} if settings.kalshi_api_key else None,
                    )
                )
            if settings.metaculus_api_token:
                checks.append(
                    _check(
                        client,
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
        return {
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "checks_passed": len(checks) - len(failed),
            "providers": checks,
            "has_blocking_issues": len(failed) > 0,
        }

    return _run_job_with_status_from_result(
        db,
        job_name="provider_contract_checks",
        run_fn=_run,
        status_fn=lambda summary: "FAILED" if bool(summary.get("has_blocking_issues")) else "SUCCESS",
        response_fn=lambda summary, status: {"status": ("ok" if status == "SUCCESS" else "error"), "result": summary},
    )


def stage7_evaluate_job(db: Session, *, lookback_days: int = 7, limit: int = 200) -> dict:
    """Evaluate recent signals via Stage 7 LLM agent and store decisions."""
    settings = get_settings()
    return _run_job(
        db,
        job_name="stage7_evaluate",
        stale_minutes=25,
        run_fn=lambda: build_stage7_shadow_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        ),
        details_fn=lambda report: {
            "rows_total": len(list(report.get("rows") or [])),
            "llm_calls": int((report.get("cost_control") or {}).get("llm_calls_run") or 0),
            "cache_hits": int((report.get("cost_control") or {}).get("cache_hits_run") or 0),
            "decision_counts": report.get("decision_counts") or {},
        },
        response_fn=lambda report: {
            "status": "ok",
            "result": {
                "rows_total": len(list(report.get("rows") or [])),
                "llm_calls": int((report.get("cost_control") or {}).get("llm_calls_run") or 0),
                "cache_hits": int((report.get("cost_control") or {}).get("cache_hits_run") or 0),
                "decision_counts": report.get("decision_counts") or {},
            },
        },
    )


def stage8_shadow_ledger_job(db: Session, *, lookback_days: int = 14, limit: int = 300) -> dict:
    settings = get_settings()
    return _run_job(
        db,
        job_name="stage8_shadow_ledger",
        run_fn=lambda: build_stage8_shadow_ledger_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        ),
        details_fn=lambda report: {
            "tracking": record_stage5_experiment(
                run_name="stage8_shadow_ledger",
                params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage8_shadow_ledger"},
                metrics=extract_stage8_shadow_ledger_metrics(report),
                tags={"policy_profile": settings.stage8_policy_profile},
            ),
            "rows": report.get("rows_total"),
        },
    )


def stage8_final_report_job(db: Session, *, lookback_days: int = 14, limit: int = 300) -> dict:
    settings = get_settings()
    def _run() -> dict:
        shadow = build_stage8_shadow_ledger_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        )
        return build_stage8_final_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
            shadow_report=shadow,
        )
    return _run_job(
        db,
        job_name="stage8_final_report",
        run_fn=_run,
        details_fn=lambda report: {
            "tracking": record_stage5_experiment(
                run_name="stage8_final_report",
                params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage8_final_report"},
                metrics=extract_stage8_final_report_metrics(report),
                tags={"final_decision": str(report.get("final_decision") or "")},
            ),
            "final_decision": report.get("final_decision"),
        },
    )


def stage9_track_job(
    db: Session,
    *,
    days_consensus: int = 14,
    days_labeling: int = 30,
    days_execution: int = 14,
) -> dict:
    settings = get_settings()
    return _run_job(
        db,
        job_name="stage9_track",
        run_fn=lambda: build_stage9_batch_report(
            db,
            settings=settings,
            days_consensus=days_consensus,
            days_labeling=days_labeling,
            days_execution=days_execution,
        ),
        details_fn=lambda report: {
            "tracked_runs": len(dict(report.get("tracking") or {})),
            "consensus_3source_share": float(
                ((report.get("reports") or {}).get("stage9_consensus_quality") or {}).get("consensus_3source_share")
                or 0.0
            ),
            "non_zero_edge_share": float(
                ((report.get("reports") or {}).get("stage9_execution_realism") or {}).get("non_zero_edge_share")
                or 0.0
            ),
        },
    )


def stage10_track_job(
    db: Session,
    *,
    days: int = 365,
    limit: int = 5000,
    event_target: int = 100,
) -> dict:
    settings = get_settings()
    return _run_job(
        db,
        job_name="stage10_track",
        run_fn=lambda: build_stage10_batch_report(
            db,
            settings=settings,
            days=days,
            limit=limit,
            event_target=event_target,
        ),
        details_fn=lambda report: {
            "tracked_runs": len(dict(report.get("tracking") or {})),
            "events_total": int(
                (((report.get("reports") or {}).get("stage10_replay") or {}).get("summary") or {}).get("events_total")
                or 0
            ),
            "leakage_violations_count": int(
                (((report.get("reports") or {}).get("stage10_replay") or {}).get("summary") or {}).get(
                    "leakage_violations_count"
                )
                or 0
            ),
        },
    )


def stage10_timeline_backfill_job(
    db: Session,
    *,
    days: int = 730,
    limit: int = 500,
    per_platform_limit: int = 100,
) -> dict:
    settings = get_settings()
    return _run_job(
        db,
        job_name="stage10_timeline_backfill",
        run_fn=lambda: run_stage10_timeline_backfill(
            db,
            settings=settings,
            days=days,
            limit=limit,
            per_platform_limit=per_platform_limit,
            dry_run=False,
        ),
        details_fn=lambda report: {
            "updated_rows": int(report.get("updated_rows") or 0),
            "total_candidates": int(report.get("total_candidates") or 0),
        },
    )


def stage11_track_job(
    db: Session,
    *,
    days_execution: int = 14,
    days_client: int = 7,
) -> dict:
    def _run() -> dict:
        settings = get_settings()
        return build_stage11_track_report(
            db,
            settings=settings,
            days_execution=days_execution,
            days_client=days_client,
        )

    def _details(report: dict) -> dict:
        summary = dict(report.get("summary") or {})
        return {
            "final_decision": str(report.get("final_decision") or ""),
            "orders_total": int(summary.get("orders_total") or 0),
            "global_circuit_breaker_level": str(summary.get("global_circuit_breaker_level") or "OK"),
        }

    return _run_job(
        db,
        job_name="stage11_track",
        run_fn=_run,
        details_fn=_details,
    )


def stage11_reconcile_job(
    db: Session,
    *,
    max_unknown_recovery_sec: int | None = None,
) -> dict:
    def _run() -> dict:
        settings = get_settings()
        report = reconcile_orders(
            db,
            settings=settings,
            max_unknown_recovery_sec=int(
                max_unknown_recovery_sec
                if max_unknown_recovery_sec is not None
                else settings.stage11_max_unknown_recovery_sec
            ),
        )
        db.commit()
        return report

    def _details(report: dict) -> dict:
        return {
            "recovered": int(report.get("recovered") or 0),
            "filled": int(report.get("filled") or 0),
            "safe_cancelled": int(report.get("safe_cancelled") or 0),
            "still_unknown": int(report.get("still_unknown") or 0),
        }

    return _run_job(
        db,
        job_name="stage11_reconcile",
        run_fn=_run,
        details_fn=_details,
    )


def stage17_track_job(
    db: Session,
    *,
    days: int = 60,
) -> dict:
    def _run() -> dict:
        settings = get_settings()
        report = build_stage17_tail_report(db, settings=settings, days=days, persist=True)
        tracking = record_stage5_experiment(
            run_name="stage17_tail_report",
            params={"report_type": "stage17_tail_report", "days": days},
            metrics=extract_stage17_tail_report_metrics(report),
            tags={"stage": "stage17", "final_decision": str(report.get("final_decision") or "")},
        )
        return {"report": report, "tracking": tracking}

    def _details(payload: dict) -> dict:
        report = dict(payload.get("report") or {})
        tracking = dict(payload.get("tracking") or {})
        summary = dict(report.get("summary") or {})
        return {
            "final_decision": str(report.get("final_decision") or ""),
            "closed_positions": int(summary.get("closed_positions") or 0),
            "payout_skew_ci_low_80": float(summary.get("payout_skew_ci_low_80") or 0.0),
            "tracking_recorded": bool(tracking.get("recorded")),
        }

    def _response(payload: dict) -> dict:
        report = dict(payload.get("report") or {})
        tracking = dict(payload.get("tracking") or {})
        return {"status": "ok", "result": report, "tracking": tracking}

    return _run_job(
        db,
        job_name="stage17_track",
        run_fn=_run,
        details_fn=_details,
        response_fn=_response,
    )


def stage17_cycle_job(
    db: Session,
    *,
    limit: int = 20,
) -> dict:
    def _run() -> dict:
        settings = get_settings()
        result = run_stage17_tail_cycle(db, settings=settings, limit=limit)
        opened_alerts = list(result.get("opened_alerts") or [])
        win_alerts = list(result.get("win_alerts") or [])
        messages: list[str] = []
        for item in opened_alerts:
            messages.append(_build_stage17_open_message(item))
        for item in win_alerts:
            messages.append(_build_stage17_win_message(item))
        telegram_sent = _send_stage17_telegram_messages(settings, messages)
        result["telegram_sent"] = int(telegram_sent)
        return result

    return _run_job_with_guard(
        db,
        job_name="stage17_cycle",
        stale_minutes=30,
        run_fn=_run,
    )


def stage17_batch_job(
    db: Session,
    *,
    days: int = 60,
    cycle_limit: int = 20,
) -> dict:
    def _run() -> dict:
        settings = get_settings()
        report = build_stage17_batch_report(
            db,
            settings=settings,
            days=days,
            cycle_limit=cycle_limit,
        )
        summary = ((report.get("reports") or {}).get("stage17_tail_report") or {}).get("summary") or {}
        digest_text = (
            "📊 STAGE17 DAILY\n"
            f"hit_rate={summary.get('hit_rate_tail')}\n"
            f"roi={summary.get('roi_total')}\n"
            f"open_positions={summary.get('open_positions')}\n"
            f"avg_koef={summary.get('avg_koef')}\n"
            f"final={summary.get('final_decision')}"
        )
        telegram_sent = _send_stage17_telegram_messages(settings, [digest_text])
        report["telegram_sent"] = bool(telegram_sent > 0)
        return report

    def _details(report: dict) -> dict:
        summary = ((report.get("reports") or {}).get("stage17_tail_report") or {}).get("summary") or {}
        return {
            "final_decision": str(((report.get("reports") or {}).get("stage17_tail_report") or {}).get("final_decision") or ""),
            "closed_positions": int(summary.get("closed_positions") or 0),
            "payout_skew_ci_low_80": float(summary.get("payout_skew_ci_low_80") or 0.0),
            "telegram_sent": bool(report.get("telegram_sent")),
        }

    return _run_job(
        db,
        job_name="stage17_batch",
        run_fn=_run,
        details_fn=_details,
    )


def stage18_canonicalize_job(db: Session) -> dict:
    """Backfill event_group_id for all markets missing canonicalization."""
    def _run() -> dict:
        from app.services.stage18.canonicalizer import backfill_canonical_keys
        return backfill_canonical_keys(db)
    return _run_job_with_guard(db, job_name="stage18_canonicalize", stale_minutes=60, run_fn=_run)


def stage18_track_job(db: Session) -> dict:
    """Run all Stage18 research reports and persist artifacts."""
    def _run() -> dict:
        from app.services.research.stage18_report import build_stage18_final_report
        settings = get_settings()
        return build_stage18_final_report(db, settings=settings)
    return _run_job_with_guard(db, job_name="stage18_track", stale_minutes=180, run_fn=_run)


def quality_snapshot_job(db: Session) -> dict:
    def _run() -> dict:
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
        return result

    return _run_job(db, job_name="quality_snapshot", run_fn=_run)


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)



def _extract_resolved_probability(market: Market) -> float | None:
    payload = _as_obj_dict(market.source_payload)
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


def _extract_resolved_outcome(market: Market, resolved_probability: float | None) -> str:
    payload = _as_obj_dict(market.source_payload)
    for key in ("isVoid", "voided", "isCancelled", "cancelled", "isNull", "nullified"):
        value = payload.get(key)
        if value in (True, 1, "1", "true", "yes", "YES"):
            return "VOID"
    for key in ("resolution", "resolvedOutcome", "outcome", "result"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().upper() in {"VOID", "N/A", "NA", "CANCELLED", "CANCELED", "NULL"}:
            return "VOID"
    if isinstance(resolved_probability, (int, float)):
        return "YES" if float(resolved_probability) >= 0.5 else "NO"
    return "PENDING"


def _normalize_signal_direction(value: str | None) -> str | None:
    token = str(value or "").strip().upper()
    if token in {"YES", "NO"}:
        return token
    return None


def _run_guarded_label_job(
    db: Session,
    *,
    job_name: str,
    stale_minutes: int,
    run_fn,
    result_to_status_fn=None,
) -> dict:
    _cleanup_stale_running_jobs(db, job_name=job_name, stale_minutes=stale_minutes)
    if _is_recent_running_job(db, job_name=job_name, stale_minutes=stale_minutes):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, job_name)
    try:
        result = run_fn()
        status = "SUCCESS"
        error_message: str | None = None
        if callable(result_to_status_fn):
            status, error_message = result_to_status_fn(result)
        _finish_job(db, job, status, result)
        if status == "SUCCESS":
            return {"status": "ok", "result": result}
        return {"status": "error", "error": error_message or "job_failed", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": _safe_error(exc)})
        return {"status": "error", "error": _safe_error(exc)}


def _label_signal_history_horizon(db: Session, *, hours: int) -> dict:
    job_name = f"label_signal_history_{hours}h"
    def _run() -> dict:
        horizon = f"{hours}h"
        return label_signal_history_from_snapshots(
            db,
            horizon=horizon,
            batch_size=5000,
            max_snapshot_lag_hours=max(0.5, float(get_settings().signal_labeling_horizon_lag_hours)),
            dry_run=False,
        )

    def _status_from_result(result: dict) -> tuple[str, str | None]:
        if str(result.get("status") or "").lower() == "ok":
            return "SUCCESS", None
        return "FAILED", str(result.get("error") or "labeling_failed")

    return _run_guarded_label_job(
        db,
        job_name=job_name,
        stale_minutes=40,
        run_fn=_run,
        result_to_status_fn=_status_from_result,
    )


def _label_signal_history_subhour(db: Session, *, minutes: int, key_name: str, batch_size: int = 2000) -> dict:
    job_name = f"label_signal_history_{minutes}m"
    def _run() -> dict:
        now = datetime.now(UTC)
        target = now - timedelta(minutes=minutes)
        # Only look back 7 days — older rows will never get a matching snapshot
        lookback_cutoff = now - timedelta(days=7)
        tolerance = max(1, int(get_settings().signal_labeling_tolerance_minutes))

        rows = list(
            db.scalars(
                select(SignalHistory)
                .where(
                    SignalHistory.timestamp >= lookback_cutoff,
                    SignalHistory.timestamp <= target,
                )
                .order_by(SignalHistory.timestamp.asc())
                .limit(batch_size)
            )
        )
        market_ids = sorted({int(r.market_id) for r in rows if r.market_id is not None})
        markets_by_id: dict[int, Market] = {}
        if market_ids:
            markets_by_id = {
                int(m.id): m
                for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))
            }
        targets: list[datetime] = []
        for row in rows:
            ts = _as_utc(row.timestamp)
            if ts is not None:
                targets.append(ts + timedelta(minutes=minutes))
        snapshots_by_market: dict[int, dict[str, list]] = {}
        if market_ids and targets:
            min_target = min(targets)
            max_target = max(targets) + timedelta(minutes=tolerance)
            snap_rows = list(
                db.scalars(
                    select(MarketSnapshot)
                    .where(MarketSnapshot.market_id.in_(market_ids))
                    .where(MarketSnapshot.fetched_at >= min_target)
                    .where(MarketSnapshot.fetched_at <= max_target)
                    .order_by(MarketSnapshot.market_id.asc(), MarketSnapshot.fetched_at.asc())
                )
            )
            for snap in snap_rows:
                sid = int(snap.market_id)
                fetched = _as_utc(snap.fetched_at)
                if fetched is None:
                    continue
                buf = snapshots_by_market.setdefault(sid, {"times": [], "snaps": []})
                buf["times"].append(fetched)
                buf["snaps"].append(snap)

        updated = 0
        skipped_market_missing = 0
        skipped_snapshot_missing = 0
        for row in rows:
            payload = dict(_as_obj_dict(row.simulated_trade))
            if payload.get(key_name) is not None:
                continue
            market = markets_by_id.get(int(row.market_id or 0))
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
            snap = None
            window_end = target_ts + timedelta(minutes=tolerance)
            market_snaps = snapshots_by_market.get(int(row.market_id or 0))
            if market_snaps is not None:
                times: list[datetime] = market_snaps["times"]
                snaps: list[MarketSnapshot] = market_snaps["snaps"]
                idx = bisect_left(times, target_ts)
                if idx < len(times) and times[idx] <= window_end:
                    snap = snaps[idx]
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
        return result

    return _run_guarded_label_job(
        db,
        job_name=job_name,
        stale_minutes=40,
        run_fn=_run,
    )


def label_signal_history_15m_job(db: Session) -> dict:
    return _label_signal_history_subhour(db, minutes=15, key_name="probability_after_15m")


def label_signal_history_30m_job(db: Session) -> dict:
    return _label_signal_history_subhour(db, minutes=30, key_name="probability_after_30m")


def label_signal_history_1h_job(db: Session) -> dict:
    return _label_signal_history_horizon(db, hours=1)


def label_signal_history_6h_job(db: Session) -> dict:
    return _label_signal_history_horizon(db, hours=6)


def label_signal_history_24h_job(db: Session) -> dict:
    return _label_signal_history_horizon(db, hours=24)


def label_signal_history_job(
    db: Session,
    *,
    batch_size: int = 1000,
    max_snapshot_lag_hours: float = 2.0,
    dry_run: bool = False,
) -> dict:
    def _run() -> dict:
        results: dict[str, dict] = {}
        for horizon in ("1h", "6h", "24h"):
            results[horizon] = label_signal_history_from_snapshots(
                db,
                horizon=horizon,
                batch_size=batch_size,
                max_snapshot_lag_hours=max_snapshot_lag_hours,
                dry_run=dry_run,
            )
        payload = {
            "dry_run": bool(dry_run),
            "batch_size": int(batch_size),
            "max_snapshot_lag_hours": float(max_snapshot_lag_hours),
            "updated_total": int(sum(int((results.get(h) or {}).get("updated") or 0) for h in results)),
            "candidates_total": int(sum(int((results.get(h) or {}).get("candidates") or 0) for h in results)),
            "by_horizon": results,
        }
        return payload

    return _run_job_with_guard(
        db,
        job_name="label_signal_history",
        stale_minutes=40,
        run_fn=_run,
    )


def label_signal_history_resolution_job(
    db: Session,
    *,
    batch_size: int = 5000,
) -> dict:
    def _run() -> dict:
        now = datetime.now(UTC)
        limit_n = max(1, int(batch_size))
        rows = list(
            db.scalars(
                select(SignalHistory)
                .where(SignalHistory.resolution_checked_at.is_(None))
                .order_by(SignalHistory.timestamp.asc())
                .limit(limit_n)
            )
        )
        market_ids = sorted({int(r.market_id) for r in rows if r.market_id is not None})
        signal_ids = sorted({int(r.signal_id) for r in rows if r.signal_id is not None})
        markets_by_id: dict[int, Market] = {}
        if market_ids:
            markets_by_id = {
                int(m.id): m
                for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))
            }
        signals_by_id: dict[int, Signal] = {}
        if signal_ids:
            signals_by_id = {
                int(s.id): s
                for s in db.scalars(select(Signal).where(Signal.id.in_(signal_ids)))
            }
        checked = 0
        updated = 0
        skipped_not_resolved = 0
        skipped_no_probability = 0
        for row in rows:
            market = markets_by_id.get(int(row.market_id or 0))
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
                row.resolved_outcome = "PENDING"
                row.missing_label_reason = "resolved_probability_unavailable"
                continue

            row.resolved_probability = float(resolved_probability)
            row.resolved_outcome = _extract_resolved_outcome(market, resolved_probability)
            payload = _as_obj_dict(market.source_payload)
            is_disputed = bool(payload.get("disputed")) or bool(payload.get("isDisputed"))
            if is_disputed:
                row.resolved_success = None
                row.missing_label_reason = "oracle_dispute_risk"
                row.resolution_checked_at = now
                updated += 1
                continue
            direction = _normalize_signal_direction(row.signal_direction)
            if direction is None and row.signal_id:
                signal = signals_by_id.get(int(row.signal_id))
                if signal:
                    direction = _normalize_signal_direction(signal.signal_direction)
                    row.signal_direction = direction

            if row.resolved_outcome == "VOID":
                row.resolved_success = None
                row.missing_label_reason = "void_resolution"
            elif row.probability_at_signal is not None and direction in {"YES", "NO"}:
                if direction == "YES":
                    row.resolved_success = bool(float(resolved_probability) > float(row.probability_at_signal))
                else:
                    row.resolved_success = bool(float(resolved_probability) < float(row.probability_at_signal))
            else:
                row.resolved_success = None
                if direction not in {"YES", "NO"}:
                    row.missing_label_reason = "direction_missing"
            row.resolution_checked_at = now
            if row.resolved_success is not None:
                row.missing_label_reason = None
            updated += 1

        db.commit()
        result = {
            "batch_size": limit_n,
            "candidates": len(rows),
            "checked_resolved": checked,
            "updated": updated,
            "skipped_not_resolved": skipped_not_resolved,
            "skipped_no_probability": skipped_no_probability,
        }
        return result

    return _run_job_with_guard(db, job_name="label_signal_history_resolution", stale_minutes=50, run_fn=_run)


def cleanup_signal_history_job(db: Session) -> dict:
    def _run() -> dict:
        settings = get_settings()
        cutoff = datetime.now(UTC) - timedelta(days=max(1, settings.signal_history_retention_days))
        deleted = (
            db.execute(
                delete(SignalHistory).where(SignalHistory.timestamp < _as_utc(cutoff))
            ).rowcount
            or 0
        )
        db.commit()
        return {"deleted": int(deleted), "retention_days": settings.signal_history_retention_days}

    return _run_job(db, job_name="cleanup_signal_history", run_fn=_run)
