import threading
import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.services.dryrun.reporter import build_report as dryrun_build_report
from app.services.dryrun.simulator import (
    check_resolutions as dryrun_check_resolutions,
    refresh_mark_prices as dryrun_refresh_mark_prices,
    reset_portfolio as dryrun_reset_portfolio,
    run_simulation_cycle as dryrun_run_cycle,
)
from app.services.research.signal_history_labeler import label_signal_history_from_snapshots
from app.tasks.jobs import (
    analyze_markets_job,
    cleanup_signal_history_job,
    label_signal_history_1h_job,
    label_signal_history_15m_job,
    label_signal_history_30m_job,
    label_signal_history_6h_job,
    label_signal_history_24h_job,
    label_signal_history_resolution_job,
    provider_contract_checks_job,
    quality_snapshot_job,
    send_test_signal_job,
    sync_all_platforms_job,
)

router = APIRouter(prefix="/admin", tags=["admin"])
_DRYRUN_RUNTIME_LOCK = threading.Lock()
_LAST_DRYRUN_MANUAL_RUN_TS: float = 0.0
_DRYRUN_REPORT_CACHE: tuple[float, dict] | None = None


@router.post("/sync-markets", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def sync_markets(
    platform: str | None = Query(default=None, description="manifold | metaculus | polymarket | kalshi"),
    db: Session = Depends(get_db),
) -> dict:
    return sync_all_platforms_job(db, platform=platform)


@router.post("/run-analysis", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def run_analysis(db: Session = Depends(get_db)) -> dict:
    return analyze_markets_job(db)


@router.post("/send-test-signal", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def send_test_signal(db: Session = Depends(get_db)) -> dict:
    return send_test_signal_job(db)


@router.post("/quality-snapshot", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def quality_snapshot(db: Session = Depends(get_db)) -> dict:
    return quality_snapshot_job(db)


@router.post("/label-signal-history/1h", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history_1h(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_1h_job(db)


@router.post("/label-signal-history/15m", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history_15m(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_15m_job(db)


@router.post("/label-signal-history/30m", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history_30m(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_30m_job(db)


@router.post("/label-signal-history/6h", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history_6h(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_6h_job(db)


@router.post("/label-signal-history/24h", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history_24h(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_24h_job(db)


@router.post("/label-signal-history/resolution", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history_resolution(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_resolution_job(db)


@router.post("/label-signal-history", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def label_signal_history(
    horizon: str = Query(default="6h", description="1h | 6h | 24h"),
    batch_size: int = Query(default=1000, ge=1, le=100000),
    max_snapshot_lag_hours: float = Query(default=2.0, ge=0.1, le=48.0),
    dry_run: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    return label_signal_history_from_snapshots(
        db,
        horizon=horizon,
        batch_size=batch_size,
        max_snapshot_lag_hours=max_snapshot_lag_hours,
        dry_run=dry_run,
    )


@router.post("/cleanup-signal-history", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def cleanup_signal_history(db: Session = Depends(get_db)) -> dict:
    return cleanup_signal_history_job(db)


@router.post("/provider-contract-checks", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def provider_contract_checks(db: Session = Depends(get_db)) -> dict:
    return provider_contract_checks_job(db)


# ---------------------------------------------------------------------------
# Dry-run paper trading simulator
# ---------------------------------------------------------------------------


@router.post("/dryrun/run", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def dryrun_run(db: Session = Depends(get_db)) -> dict:
    """Run one simulation cycle: open new paper positions for qualifying signals."""
    settings = get_settings()
    min_interval = max(0, int(settings.dryrun_manual_min_run_interval_sec))
    now = time.monotonic()
    global _LAST_DRYRUN_MANUAL_RUN_TS, _DRYRUN_REPORT_CACHE
    with _DRYRUN_RUNTIME_LOCK:
        since_last = now - _LAST_DRYRUN_MANUAL_RUN_TS
        if min_interval > 0 and since_last < float(min_interval):
            return {
                "opened": 0,
                "skipped": 0,
                "cash_remaining_usd": None,
                "skip_reasons": ["manual_run_cooldown"],
                "retry_after_sec": round(float(min_interval) - since_last, 2),
            }
        _LAST_DRYRUN_MANUAL_RUN_TS = now
    result = dryrun_run_cycle(db)
    db.commit()
    with _DRYRUN_RUNTIME_LOCK:
        _DRYRUN_REPORT_CACHE = None
    return result


@router.get("/dryrun/report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
def dryrun_report(db: Session = Depends(get_db)) -> dict:
    """Return full dry-run portfolio report with stats and AI summary."""
    settings = get_settings()
    ttl = max(0, int(settings.dryrun_report_cache_ttl_sec))
    now = time.monotonic()
    global _DRYRUN_REPORT_CACHE
    if ttl > 0:
        with _DRYRUN_RUNTIME_LOCK:
            cached = _DRYRUN_REPORT_CACHE
            if cached and (now - cached[0]) <= float(ttl):
                return cached[1]
    payload = dryrun_build_report(db)
    if ttl > 0:
        with _DRYRUN_RUNTIME_LOCK:
            _DRYRUN_REPORT_CACHE = (now, payload)
    return payload


@router.post("/dryrun/refresh-prices", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def dryrun_refresh_prices(db: Session = Depends(get_db)) -> dict:
    """Refresh CLOB mark prices for all open dry-run positions."""
    global _DRYRUN_REPORT_CACHE
    result = dryrun_refresh_mark_prices(db)
    db.commit()
    with _DRYRUN_RUNTIME_LOCK:
        _DRYRUN_REPORT_CACHE = None
    return result


@router.post("/dryrun/check-resolutions", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def dryrun_check_res(db: Session = Depends(get_db)) -> dict:
    """Close dry-run positions for markets that are now resolved."""
    global _DRYRUN_REPORT_CACHE
    result = dryrun_check_resolutions(db)
    db.commit()
    with _DRYRUN_RUNTIME_LOCK:
        _DRYRUN_REPORT_CACHE = None
    return result


@router.post("/dryrun/reset", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
def dryrun_reset(db: Session = Depends(get_db)) -> dict:
    """Reset dry-run portfolio to initial $100 balance."""
    global _DRYRUN_REPORT_CACHE
    portfolio = dryrun_reset_portfolio(db)
    db.commit()
    with _DRYRUN_RUNTIME_LOCK:
        _DRYRUN_REPORT_CACHE = None
    return {"reset": True, "cash_usd": portfolio.current_cash_usd}
