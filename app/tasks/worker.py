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


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

@celery_app.task(name="sync_all_platforms", **_RETRY)
def sync_all_platforms_task() -> dict:
    db = SessionLocal()
    try:
        return sync_all_platforms_job(db)
    finally:
        db.close()


@celery_app.task(name="analyze_markets", **_RETRY)
def analyze_markets_task() -> dict:
    db = SessionLocal()
    try:
        return analyze_markets_job(db)
    finally:
        db.close()


@celery_app.task(name="detect_duplicates", **_RETRY)
def detect_duplicates_task() -> dict:
    db = SessionLocal()
    try:
        return detect_duplicates_job(db)
    finally:
        db.close()


@celery_app.task(name="analyze_rules", **_RETRY)
def analyze_rules_task() -> dict:
    db = SessionLocal()
    try:
        return analyze_rules_job(db)
    finally:
        db.close()


@celery_app.task(name="detect_divergence", **_RETRY)
def detect_divergence_task() -> dict:
    db = SessionLocal()
    try:
        return detect_divergence_job(db)
    finally:
        db.close()


@celery_app.task(name="generate_signals", **_RETRY)
def generate_signals_task() -> dict:
    db = SessionLocal()
    try:
        return generate_signals_job(db)
    finally:
        db.close()


@celery_app.task(name="daily_digest", **_RETRY)
def daily_digest_task() -> dict:
    db = SessionLocal()
    try:
        return daily_digest_job(db)
    finally:
        db.close()


@celery_app.task(name="signal_push", **_RETRY)
def signal_push_task() -> dict:
    db = SessionLocal()
    try:
        return signal_push_job(db)
    finally:
        db.close()


@celery_app.task(name="cleanup_old_signals", **_RETRY)
def cleanup_old_signals_task() -> dict:
    db = SessionLocal()
    try:
        return cleanup_old_signals_job(db)
    finally:
        db.close()


@celery_app.task(name="update_watchlists", **_RETRY)
def update_watchlists_task() -> dict:
    db = SessionLocal()
    try:
        return update_watchlists_job(db)
    finally:
        db.close()


@celery_app.task(name="quality_snapshot", **_RETRY)
def quality_snapshot_task() -> dict:
    db = SessionLocal()
    try:
        return quality_snapshot_job(db)
    finally:
        db.close()


# Labeling: single batched task covers all horizons (15m/30m/1h/6h/24h)
@celery_app.task(name="label_signal_history", **_RETRY)
def label_signal_history_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_resolution", **_RETRY)
def label_signal_history_resolution_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_resolution_job(db)
    finally:
        db.close()


@celery_app.task(name="cleanup_signal_history", **_RETRY)
def cleanup_signal_history_task() -> dict:
    db = SessionLocal()
    try:
        return cleanup_signal_history_job(db)
    finally:
        db.close()


@celery_app.task(name="provider_contract_checks", **_RETRY)
def provider_contract_checks_task() -> dict:
    db = SessionLocal()
    try:
        return provider_contract_checks_job(db)
    finally:
        db.close()


@celery_app.task(name="stage7_evaluate", **_RETRY)
def stage7_evaluate_task() -> dict:
    db = SessionLocal()
    try:
        return stage7_evaluate_job(db)
    finally:
        db.close()


@celery_app.task(name="stage8_shadow_ledger", **_RETRY)
def stage8_shadow_ledger_task() -> dict:
    db = SessionLocal()
    try:
        return stage8_shadow_ledger_job(db)
    finally:
        db.close()


@celery_app.task(name="stage8_final_report", **_RETRY)
def stage8_final_report_task() -> dict:
    db = SessionLocal()
    try:
        return stage8_final_report_job(db)
    finally:
        db.close()


@celery_app.task(name="stage9_track", **_RETRY)
def stage9_track_task() -> dict:
    db = SessionLocal()
    try:
        return stage9_track_job(db)
    finally:
        db.close()


@celery_app.task(name="stage10_track", **_RETRY)
def stage10_track_task() -> dict:
    db = SessionLocal()
    try:
        return stage10_track_job(db)
    finally:
        db.close()


@celery_app.task(name="stage10_timeline_backfill", **_RETRY)
def stage10_timeline_backfill_task() -> dict:
    db = SessionLocal()
    try:
        return stage10_timeline_backfill_job(db)
    finally:
        db.close()


@celery_app.task(name="stage11_track", **_RETRY)
def stage11_track_task() -> dict:
    db = SessionLocal()
    try:
        return stage11_track_job(db)
    finally:
        db.close()


@celery_app.task(name="stage11_reconcile", **_RETRY)
def stage11_reconcile_task() -> dict:
    db = SessionLocal()
    try:
        return stage11_reconcile_job(db)
    finally:
        db.close()


@celery_app.task(name="stage17_track", **_RETRY)
def stage17_track_task() -> dict:
    db = SessionLocal()
    try:
        return stage17_track_job(db)
    finally:
        db.close()


@celery_app.task(name="stage17_cycle", **_RETRY)
def stage17_cycle_task() -> dict:
    db = SessionLocal()
    try:
        return stage17_cycle_job(db)
    finally:
        db.close()


@celery_app.task(name="stage17_batch", **_RETRY)
def stage17_batch_task() -> dict:
    db = SessionLocal()
    try:
        return stage17_batch_job(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Beat schedule
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    # Core data pipeline — staggered to avoid DB contention
    "sync-platforms-every-15-min": {
        "task": "sync_all_platforms",
        "schedule": crontab(minute="*/15"),
    },
    "analyze-markets-every-15-min": {
        "task": "analyze_markets",
        "schedule": crontab(minute="2-59/15"),  # +2min offset after sync
    },
    "detect-duplicates-every-2h": {
        "task": "detect_duplicates",
        "schedule": crontab(minute=5, hour="*/2"),
    },
    "analyze-rules-every-20-min": {
        "task": "analyze_rules",
        "schedule": crontab(minute="4-59/20"),  # +4min offset after sync
    },
    "detect-divergence-every-20-min": {
        "task": "detect_divergence",
        "schedule": crontab(minute="6-59/20"),  # +6min offset
    },
    "generate-signals-every-20-min": {
        "task": "generate_signals",
        "schedule": crontab(minute="8-59/20"),  # +8min offset, after divergence
    },
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
    "stage17-cycle-every-30-min": {
        "task": "stage17_cycle",
        "schedule": crontab(minute="20,50"),
    },
}
