from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import SignalType
from app.models.models import (
    JobRun,
    Market,
    MarketSnapshot,
    Signal,
    SignalHistory,
    SignalQualityMetrics,
    Stage7AgentDecision,
    Stage8Decision,
    Stage8Position,
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
from app.services.research.signal_history_labeler import label_signal_history_from_snapshots
from app.services.stage11.reports import build_stage11_track_report
from app.services.stage11.order_manager import reconcile_orders
from app.services.research.tracking import record_stage5_experiment
from app.services.signals.engine import SignalEngine
from app.services.signals.ranking import rank_score, select_top_signals
from app.services.telegram_product import TelegramProductService


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


def sync_all_platforms_job(db: Session, platform: str | None = None) -> dict:
    _cleanup_stale_running_jobs(db, job_name="sync_all_platforms", stale_minutes=20)
    if _is_recent_running_job(db, job_name="sync_all_platforms", stale_minutes=20):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, "sync_all_platforms")
    try:
        result = CollectorSyncService(db).sync_all(platform=platform)
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def analyze_markets_job(db: Session) -> dict:
    _cleanup_stale_running_jobs(db, job_name="analyze_markets", stale_minutes=30)
    if _is_recent_running_job(db, job_name="analyze_markets", stale_minutes=30):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, "analyze_markets")
    try:
        result = SignalEngine(db).run()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def detect_duplicates_job(db: Session) -> dict:
    _cleanup_stale_running_jobs(db, job_name="detect_duplicates", stale_minutes=45)
    if _is_recent_running_job(db, job_name="detect_duplicates", stale_minutes=45):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, "detect_duplicates")
    try:
        result = SignalEngine(db).detect_duplicates()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def analyze_rules_job(db: Session) -> dict:
    _cleanup_stale_running_jobs(db, job_name="analyze_rules", stale_minutes=25)
    if _is_recent_running_job(db, job_name="analyze_rules", stale_minutes=25):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, "analyze_rules")
    try:
        result = SignalEngine(db).analyze_rules()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def detect_divergence_job(db: Session) -> dict:
    _cleanup_stale_running_jobs(db, job_name="detect_divergence", stale_minutes=30)
    if _is_recent_running_job(db, job_name="detect_divergence", stale_minutes=30):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, "detect_divergence")
    try:
        result = SignalEngine(db).detect_divergence()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def generate_signals_job(db: Session) -> dict:
    _cleanup_stale_running_jobs(db, job_name="generate_signals", stale_minutes=30)
    if _is_recent_running_job(db, job_name="generate_signals", stale_minutes=30):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, "generate_signals")
    try:
        result = SignalEngine(db).generate_signals()
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def send_test_signal_job(db: Session) -> dict:
    latest = db.scalar(select(Signal).order_by(Signal.id.desc()))
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
        skipped_by_reason: dict[str, int] = {}
        now = datetime.now(UTC)

        def _skip(reason: str) -> None:
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

        # Pre-load signal pool once — avoids 200-row query × N users
        signal_pool = svc.load_signal_pool()

        for user in users:
            top = svc.top_ranked_signals(user=user, limit=5, pool=signal_pool)
            top = [s for s in top if rank_score(s) > 0.1][:5]
            for signal in top:
                if not svc.can_send_signal(user, 1):
                    break
                market = db.get(Market, signal.market_id)
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
                def _e(s: str) -> str:
                    for ch in r"\_*[]()~`>#+-=|{}.!":
                        s = s.replace(ch, f"\\{ch}")
                    return s

                text = (
                    f"🔥 *{_e(signal.signal_type.value)}*\n"
                    f"{_e(signal.title)}\n"
                    f"Confidence: {_e(f'{signal.confidence_score or 0:.2f}')}\n"
                    f"{_e(metric_label)}: {_e(f'{metric_value:.1f}%')}\n"
                    f"Utility \\(exec\\): {_e(f'{utility:.3f}')}\n"
                    f"Edge after costs: {_e(f'{slippage_edge:.3f}')} \\(cost impact: {_e(f'{cost_impact:.3f}')}\\)\n"
                    f"Execution assumptions: `{_e(assumptions)}`\n"
                    f"_Disclaimer: {_e(settings.research_ethics_disclaimer_text)}_"
                )
                resp = httpx.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": user.telegram_user_id,
                        "text": text,
                        "parse_mode": "MarkdownV2",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    svc.record_signal_sent(user, signal)
                    sent += 1
        result = {"signals_prepared": prepared, "signals_sent": sent, "skipped_by_reason": skipped_by_reason}
        _finish_job(db, job, "SUCCESS", result)
        return {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def cleanup_old_signals_job(db: Session, keep_days: int = 30) -> dict:
    job = _start_job(db, "cleanup_old_signals")
    try:
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
        stmt = delete(Signal).where(
            Signal.created_at < cutoff,
            ~has_stage7_ref,
            ~has_stage8_ref,
            ~has_stage8_position_ref,
            ~has_signal_history_ref,
        )
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
    if settings.kalshi_enabled:
        checks.append(
            _check(
                "KALSHI",
                f"{settings.kalshi_api_base_url}/markets",
                params={"limit": 1, "status": "open"},
                headers={"Authorization": f"Bearer {settings.kalshi_api_key}"} if settings.kalshi_api_key else None,
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


def stage7_evaluate_job(db: Session, *, lookback_days: int = 7, limit: int = 200) -> dict:
    """Evaluate recent signals via Stage 7 LLM agent and store decisions."""
    job = _start_job(db, "stage7_evaluate")
    try:
        settings = get_settings()
        report = build_stage7_shadow_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        )
        rows_total = len(list(report.get("rows") or []))
        llm_calls = int((report.get("cost_control") or {}).get("llm_calls_run") or 0)
        cache_hits = int((report.get("cost_control") or {}).get("cache_hits_run") or 0)
        decision_counts = report.get("decision_counts") or {}
        _finish_job(db, job, "SUCCESS", {
            "rows_total": rows_total,
            "llm_calls": llm_calls,
            "cache_hits": cache_hits,
            "decision_counts": decision_counts,
        })
        return {"status": "ok", "result": {
            "rows_total": rows_total,
            "llm_calls": llm_calls,
            "cache_hits": cache_hits,
            "decision_counts": decision_counts,
        }}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage8_shadow_ledger_job(db: Session, *, lookback_days: int = 14, limit: int = 300) -> dict:
    job = _start_job(db, "stage8_shadow_ledger")
    try:
        settings = get_settings()
        report = build_stage8_shadow_ledger_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        )
        tracking = record_stage5_experiment(
            run_name="stage8_shadow_ledger",
            params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage8_shadow_ledger"},
            metrics=extract_stage8_shadow_ledger_metrics(report),
            tags={"policy_profile": settings.stage8_policy_profile},
        )
        _finish_job(db, job, "SUCCESS", {"tracking": tracking, "rows": report.get("rows_total")})
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage8_final_report_job(db: Session, *, lookback_days: int = 14, limit: int = 300) -> dict:
    job = _start_job(db, "stage8_final_report")
    try:
        settings = get_settings()
        shadow = build_stage8_shadow_ledger_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        )
        report = build_stage8_final_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
            shadow_report=shadow,
        )
        tracking = record_stage5_experiment(
            run_name="stage8_final_report",
            params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage8_final_report"},
            metrics=extract_stage8_final_report_metrics(report),
            tags={"final_decision": str(report.get("final_decision") or "")},
        )
        _finish_job(db, job, "SUCCESS", {"tracking": tracking, "final_decision": report.get("final_decision")})
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage9_track_job(
    db: Session,
    *,
    days_consensus: int = 14,
    days_labeling: int = 30,
    days_execution: int = 14,
) -> dict:
    job = _start_job(db, "stage9_track")
    try:
        settings = get_settings()
        report = build_stage9_batch_report(
            db,
            settings=settings,
            days_consensus=days_consensus,
            days_labeling=days_labeling,
            days_execution=days_execution,
        )
        _finish_job(
            db,
            job,
            "SUCCESS",
            {
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
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage10_track_job(
    db: Session,
    *,
    days: int = 365,
    limit: int = 5000,
    event_target: int = 100,
) -> dict:
    job = _start_job(db, "stage10_track")
    try:
        settings = get_settings()
        report = build_stage10_batch_report(
            db,
            settings=settings,
            days=days,
            limit=limit,
            event_target=event_target,
        )
        _finish_job(
            db,
            job,
            "SUCCESS",
            {
                "tracked_runs": len(dict(report.get("tracking") or {})),
                "events_total": int(
                    (((report.get("reports") or {}).get("stage10_replay") or {}).get("summary") or {}).get(
                        "events_total"
                    )
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
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage10_timeline_backfill_job(
    db: Session,
    *,
    days: int = 730,
    limit: int = 500,
    per_platform_limit: int = 100,
) -> dict:
    job = _start_job(db, "stage10_timeline_backfill")
    try:
        settings = get_settings()
        report = run_stage10_timeline_backfill(
            db,
            settings=settings,
            days=days,
            limit=limit,
            per_platform_limit=per_platform_limit,
            dry_run=False,
        )
        _finish_job(
            db,
            job,
            "SUCCESS",
            {
                "updated_rows": int(report.get("updated_rows") or 0),
                "total_candidates": int(report.get("total_candidates") or 0),
            },
        )
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage11_track_job(
    db: Session,
    *,
    days_execution: int = 14,
    days_client: int = 7,
) -> dict:
    job = _start_job(db, "stage11_track")
    try:
        settings = get_settings()
        report = build_stage11_track_report(
            db,
            settings=settings,
            days_execution=days_execution,
            days_client=days_client,
        )
        _finish_job(
            db,
            job,
            "SUCCESS",
            {
                "final_decision": str(report.get("final_decision") or ""),
                "orders_total": int((report.get("summary") or {}).get("orders_total") or 0),
                "global_circuit_breaker_level": str((report.get("summary") or {}).get("global_circuit_breaker_level") or "OK"),
            },
        )
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def stage11_reconcile_job(
    db: Session,
    *,
    max_unknown_recovery_sec: int | None = None,
) -> dict:
    job = _start_job(db, "stage11_reconcile")
    try:
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
        _finish_job(
            db,
            job,
            "SUCCESS",
            {
                "recovered": int(report.get("recovered") or 0),
                "filled": int(report.get("filled") or 0),
                "safe_cancelled": int(report.get("safe_cancelled") or 0),
                "still_unknown": int(report.get("still_unknown") or 0),
            },
        )
        return {"status": "ok", "result": report}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


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
    payload = market.source_payload or {}
    status = (market.status or "").strip().lower()
    # Some providers (notably Kalshi) can be CLOSED before final settlement outcome is available.
    if "closed" in status:
        settlement_timer = payload.get("settlement_timer_seconds")
        has_outcome = any(
            payload.get(k) is not None for k in ("resolution", "resolvedOutcome", "outcome", "result", "resolutionProbability")
        )
        if settlement_timer is not None and not has_outcome:
            return False
    if _as_utc(market.resolution_time) and _as_utc(market.resolution_time) <= now:
        return True
    if any(token in status for token in ("resolved", "settled", "final", "ended")):
        return True
    if "closed" in status:
        return True
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


def _extract_resolved_outcome(market: Market, resolved_probability: float | None) -> str:
    payload = market.source_payload or {}
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


def _label_signal_history_horizon(db: Session, *, hours: int, field_name: str) -> dict:
    job_name = f"label_signal_history_{hours}h"
    _cleanup_stale_running_jobs(db, job_name=job_name, stale_minutes=40)
    if _is_recent_running_job(db, job_name=job_name, stale_minutes=40):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, job_name)
    try:
        horizon = f"{hours}h"
        result = label_signal_history_from_snapshots(
            db,
            horizon=horizon,
            batch_size=5000,
            max_snapshot_lag_hours=max(0.5, float(get_settings().signal_labeling_horizon_lag_hours)),
            dry_run=False,
        )
        if result.get("status") == "ok":
            _finish_job(db, job, "SUCCESS", result)
            return {"status": "ok", "result": result}
        _finish_job(db, job, "FAILED", result)
        return {"status": "error", "error": result.get("error", "labeling_failed"), "result": result}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def _label_signal_history_subhour(db: Session, *, minutes: int, key_name: str, batch_size: int = 2000) -> dict:
    job_name = f"label_signal_history_{minutes}m"
    _cleanup_stale_running_jobs(db, job_name=job_name, stale_minutes=40)
    if _is_recent_running_job(db, job_name=job_name, stale_minutes=40):
        return {"status": "ok", "result": {"skipped": True, "reason": "already_running"}}
    job = _start_job(db, job_name)
    try:
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


def label_signal_history_job(
    db: Session,
    *,
    batch_size: int = 1000,
    max_snapshot_lag_hours: float = 2.0,
    dry_run: bool = False,
) -> dict:
    job = _start_job(db, "label_signal_history")
    try:
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
        _finish_job(db, job, "SUCCESS", payload)
        return {"status": "ok", "result": payload}
    except Exception as exc:  # noqa: BLE001
        _finish_job(db, job, "FAILED", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}


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
                row.resolved_outcome = "PENDING"
                row.missing_label_reason = "resolved_probability_unavailable"
                continue

            row.resolved_probability = float(resolved_probability)
            row.resolved_outcome = _extract_resolved_outcome(market, resolved_probability)
            payload = market.source_payload or {}
            is_disputed = bool(payload.get("disputed")) or bool(payload.get("isDisputed"))
            if is_disputed:
                row.resolved_success = None
                row.missing_label_reason = "oracle_dispute_risk"
                row.resolution_checked_at = now
                updated += 1
                continue
            direction = _normalize_signal_direction(row.signal_direction)
            if direction is None and row.signal_id:
                signal = db.get(Signal, row.signal_id)
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
