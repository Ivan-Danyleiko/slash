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
    label_signal_history_1h_job,
    label_signal_history_15m_job,
    label_signal_history_30m_job,
    label_signal_history_6h_job,
    label_signal_history_24h_job,
    label_signal_history_resolution_job,
    provider_contract_checks_job,
    signal_push_job,
    sync_all_platforms_job,
    update_watchlists_job,
)


@celery_app.task(name="sync_all_platforms")
def sync_all_platforms_task() -> dict:
    db = SessionLocal()
    try:
        return sync_all_platforms_job(db)
    finally:
        db.close()


@celery_app.task(name="analyze_markets")
def analyze_markets_task() -> dict:
    db = SessionLocal()
    try:
        return analyze_markets_job(db)
    finally:
        db.close()


@celery_app.task(name="detect_duplicates")
def detect_duplicates_task() -> dict:
    db = SessionLocal()
    try:
        return detect_duplicates_job(db)
    finally:
        db.close()


@celery_app.task(name="analyze_rules")
def analyze_rules_task() -> dict:
    db = SessionLocal()
    try:
        return analyze_rules_job(db)
    finally:
        db.close()


@celery_app.task(name="detect_divergence")
def detect_divergence_task() -> dict:
    db = SessionLocal()
    try:
        return detect_divergence_job(db)
    finally:
        db.close()


@celery_app.task(name="generate_signals")
def generate_signals_task() -> dict:
    db = SessionLocal()
    try:
        return generate_signals_job(db)
    finally:
        db.close()


@celery_app.task(name="daily_digest")
def daily_digest_task() -> dict:
    db = SessionLocal()
    try:
        return daily_digest_job(db)
    finally:
        db.close()


@celery_app.task(name="signal_push")
def signal_push_task() -> dict:
    db = SessionLocal()
    try:
        return signal_push_job(db)
    finally:
        db.close()


@celery_app.task(name="cleanup_old_signals")
def cleanup_old_signals_task() -> dict:
    db = SessionLocal()
    try:
        return cleanup_old_signals_job(db)
    finally:
        db.close()


@celery_app.task(name="update_watchlists")
def update_watchlists_task() -> dict:
    db = SessionLocal()
    try:
        return update_watchlists_job(db)
    finally:
        db.close()


@celery_app.task(name="quality_snapshot")
def quality_snapshot_task() -> dict:
    db = SessionLocal()
    try:
        return quality_snapshot_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_1h")
def label_signal_history_1h_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_1h_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_15m")
def label_signal_history_15m_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_15m_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_30m")
def label_signal_history_30m_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_30m_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_6h")
def label_signal_history_6h_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_6h_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_24h")
def label_signal_history_24h_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_24h_job(db)
    finally:
        db.close()


@celery_app.task(name="label_signal_history_resolution")
def label_signal_history_resolution_task() -> dict:
    db = SessionLocal()
    try:
        return label_signal_history_resolution_job(db)
    finally:
        db.close()


@celery_app.task(name="cleanup_signal_history")
def cleanup_signal_history_task() -> dict:
    db = SessionLocal()
    try:
        return cleanup_signal_history_job(db)
    finally:
        db.close()


@celery_app.task(name="provider_contract_checks")
def provider_contract_checks_task() -> dict:
    db = SessionLocal()
    try:
        return provider_contract_checks_job(db)
    finally:
        db.close()


celery_app.conf.beat_schedule = {
    "sync-platforms-every-15-min": {
        "task": "sync_all_platforms",
        "schedule": crontab(minute="*/15"),
    },
    "analyze-markets-every-15-min": {
        "task": "analyze_markets",
        "schedule": crontab(minute="2-59/15"),
    },
    "detect-duplicates-every-20-min": {
        "task": "detect_duplicates",
        "schedule": crontab(minute="*/20"),
    },
    "analyze-rules-every-20-min": {"task": "analyze_rules", "schedule": crontab(minute="*/20")},
    "detect-divergence-every-20-min": {"task": "detect_divergence", "schedule": crontab(minute="*/20")},
    "generate-signals-every-20-min": {"task": "generate_signals", "schedule": crontab(minute="*/20")},
    "update-watchlists-hourly": {"task": "update_watchlists", "schedule": crontab(minute=0)},
    "signal-push-every-30-min": {"task": "signal_push", "schedule": crontab(minute="*/30")},
    "daily-digest-once-day": {"task": "daily_digest", "schedule": crontab(hour=9, minute=0)},
    "quality-snapshot-daily": {"task": "quality_snapshot", "schedule": crontab(hour=0, minute=10)},
    "label-signal-history-15m-every-15-min": {"task": "label_signal_history_15m", "schedule": crontab(minute="*/15")},
    "label-signal-history-30m-every-30-min": {"task": "label_signal_history_30m", "schedule": crontab(minute="*/30")},
    "label-signal-history-1h-hourly": {"task": "label_signal_history_1h", "schedule": crontab(minute=12)},
    "label-signal-history-6h": {"task": "label_signal_history_6h", "schedule": crontab(hour="*/6", minute=18)},
    "label-signal-history-24h": {"task": "label_signal_history_24h", "schedule": crontab(hour=1, minute=25)},
    "label-signal-history-resolution-hourly": {
        "task": "label_signal_history_resolution",
        "schedule": crontab(minute=10),
    },
    "cleanup-old-signals-nightly": {"task": "cleanup_old_signals", "schedule": crontab(hour=3, minute=0)},
    "cleanup-signal-history-nightly": {"task": "cleanup_signal_history", "schedule": crontab(hour=3, minute=20)},
    "provider-contract-checks-hourly": {"task": "provider_contract_checks", "schedule": crontab(minute=40)},
}
