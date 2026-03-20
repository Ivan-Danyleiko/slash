from celery.schedules import crontab

from app.db.session import SessionLocal
from app.tasks.celery_app import celery_app
from app.tasks.jobs import (
    analyze_markets_job,
    analyze_rules_job,
    cleanup_old_signals_job,
    cleanup_signal_history_job,
    daily_digest_job,
    detect_divergence_job,
    detect_duplicates_job,
    generate_signals_job,
    quality_snapshot_job,
    stage9_track_job,
    stage10_track_job,
    stage10_timeline_backfill_job,
    stage11_track_job,
    stage11_reconcile_job,
    stage17_batch_job,
    stage17_cycle_job,
    stage17_track_job,
    stage7_evaluate_job,
    stage8_final_report_job,
    stage8_shadow_ledger_job,
    label_signal_history_job,
    label_signal_history_resolution_job,
    provider_contract_checks_job,
    signal_push_job,
    sync_all_platforms_job,
    update_watchlists_job,
)

# ---------------------------------------------------------------------------
# Shared task decorator defaults
# ---------------------------------------------------------------------------

_RETRY = {
    "autoretry_for": (Exception,),
    "max_retries": 2,
    "retry_backoff": True,
    "retry_backoff_max": 120,
    "retry_jitter": True,
}


def _db_task(job_fn):
    """Run a job function with a fresh DB session."""
    def wrapper(*args, **kwargs):
        db = SessionLocal()
        try:
            return job_fn(db, *args, **kwargs)
        finally:
            db.close()
    wrapper.__name__ = job_fn.__name__
    return wrapper


def _register_db_task(task_name: str, job_fn):
    task_obj = celery_app.task(name=task_name, **_RETRY)(_db_task(job_fn))
    globals()[f"{task_name}_task"] = task_obj
    return task_obj


# ---------------------------------------------------------------------------
# Task definitions (registered dynamically)
# ---------------------------------------------------------------------------
_TASK_JOB_MAP = {
    "sync_all_platforms": sync_all_platforms_job,
    "analyze_markets": analyze_markets_job,
    "detect_duplicates": detect_duplicates_job,
    "analyze_rules": analyze_rules_job,
    "detect_divergence": detect_divergence_job,
    "generate_signals": generate_signals_job,
    "daily_digest": daily_digest_job,
    "signal_push": signal_push_job,
    "cleanup_old_signals": cleanup_old_signals_job,
    "update_watchlists": update_watchlists_job,
    "quality_snapshot": quality_snapshot_job,
    "label_signal_history": label_signal_history_job,
    "label_signal_history_resolution": label_signal_history_resolution_job,
    "cleanup_signal_history": cleanup_signal_history_job,
    "provider_contract_checks": provider_contract_checks_job,
    "stage7_evaluate": stage7_evaluate_job,
    "stage8_shadow_ledger": stage8_shadow_ledger_job,
    "stage8_final_report": stage8_final_report_job,
    "stage9_track": stage9_track_job,
    "stage10_track": stage10_track_job,
    "stage10_timeline_backfill": stage10_timeline_backfill_job,
    "stage11_track": stage11_track_job,
    "stage11_reconcile": stage11_reconcile_job,
    "stage17_track": stage17_track_job,
    "stage17_cycle": stage17_cycle_job,
    "stage17_batch": stage17_batch_job,
}

for _task_name, _job_fn in _TASK_JOB_MAP.items():
    _register_db_task(_task_name, _job_fn)


# ---------------------------------------------------------------------------
# Beat schedule
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    # Core data pipeline — staggered to avoid DB contention
    "sync-platforms-every-30-min": {
        "task": "sync_all_platforms",
        "schedule": crontab(minute="*/30"),
    },
    "analyze-markets-every-15-min": {
        "task": "analyze_markets",
        "schedule": crontab(minute="2-59/15"),  # +2min offset after sync
    },
    "detect-duplicates-every-2h": {
        "task": "detect_duplicates",
        "schedule": crontab(minute=5, hour="*/2"),
    },
    # NOTE: analyze_rules, detect_divergence, generate_signals are already called
    # inside analyze_markets_job (SignalEngine.run()). Running them separately
    # doubled the work every cycle. Removed from beat — available as on-demand tasks.
    # User-facing
    "update-watchlists-hourly": {
        "task": "update_watchlists",
        "schedule": crontab(minute=0),
    },
    "signal-push-every-30-min": {
        "task": "signal_push",
        "schedule": crontab(minute="*/30"),
    },
    "daily-digest-once-day": {
        "task": "daily_digest",
        "schedule": crontab(hour=9, minute=0),
    },
    # Research / labeling — single batched job replaces 5 separate ones
    "label-signal-history-every-15-min": {
        "task": "label_signal_history",
        "schedule": crontab(minute="*/15"),
    },
    "label-signal-history-resolution-hourly": {
        "task": "label_signal_history_resolution",
        "schedule": crontab(minute=10),
    },
    # Maintenance
    "quality-snapshot-daily": {
        "task": "quality_snapshot",
        "schedule": crontab(hour=0, minute=10),
    },
    "cleanup-old-signals-nightly": {
        "task": "cleanup_old_signals",
        "schedule": crontab(hour=3, minute=0),
    },
    "cleanup-signal-history-nightly": {
        "task": "cleanup_signal_history",
        "schedule": crontab(hour=3, minute=20),
    },
    "provider-contract-checks-hourly": {
        "task": "provider_contract_checks",
        "schedule": crontab(minute=40),
    },
    # Stage pipeline
    "stage7-evaluate-every-30-min": {
        "task": "stage7_evaluate",
        "schedule": crontab(minute="*/30"),
    },
    "stage8-shadow-ledger-daily": {
        "task": "stage8_shadow_ledger",
        "schedule": crontab(hour=2, minute=45),
    },
    "stage8-final-report-daily": {
        "task": "stage8_final_report",
        "schedule": crontab(hour=2, minute=55),
    },
    "stage9-track-daily": {
        "task": "stage9_track",
        "schedule": crontab(hour=3, minute=5),
    },
    "stage10-timeline-backfill-daily": {
        "task": "stage10_timeline_backfill",
        "schedule": crontab(hour=3, minute=10),
    },
    "stage10-track-daily": {
        "task": "stage10_track",
        "schedule": crontab(hour=3, minute=15),
    },
    "stage11-reconcile-every-10-min": {
        "task": "stage11_reconcile",
        "schedule": crontab(minute="*/10"),
    },
    "stage11-track-daily": {
        "task": "stage11_track",
        "schedule": crontab(hour=3, minute=25),
    },
    "stage17-track-every-6h": {
        "task": "stage17_track",
        "schedule": crontab(hour="*/6", minute=35),
    },
    "stage17-batch-daily": {
        "task": "stage17_batch",
        "schedule": crontab(hour=3, minute=40),
    },
    "stage17-cycle-every-15-min": {
        "task": "stage17_cycle",
        "schedule": crontab(minute="*/15"),
    },
}
