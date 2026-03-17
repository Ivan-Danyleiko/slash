from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
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


@router.post("/sync-markets", dependencies=[Depends(require_admin)])
def sync_markets(
    platform: str | None = Query(default=None, description="manifold | metaculus | polymarket | kalshi"),
    db: Session = Depends(get_db),
) -> dict:
    return sync_all_platforms_job(db, platform=platform)


@router.post("/run-analysis", dependencies=[Depends(require_admin)])
def run_analysis(db: Session = Depends(get_db)) -> dict:
    return analyze_markets_job(db)


@router.post("/send-test-signal", dependencies=[Depends(require_admin)])
def send_test_signal(db: Session = Depends(get_db)) -> dict:
    return send_test_signal_job(db)


@router.post("/quality-snapshot", dependencies=[Depends(require_admin)])
def quality_snapshot(db: Session = Depends(get_db)) -> dict:
    return quality_snapshot_job(db)


@router.post("/label-signal-history/1h", dependencies=[Depends(require_admin)])
def label_signal_history_1h(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_1h_job(db)


@router.post("/label-signal-history/15m", dependencies=[Depends(require_admin)])
def label_signal_history_15m(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_15m_job(db)


@router.post("/label-signal-history/30m", dependencies=[Depends(require_admin)])
def label_signal_history_30m(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_30m_job(db)


@router.post("/label-signal-history/6h", dependencies=[Depends(require_admin)])
def label_signal_history_6h(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_6h_job(db)


@router.post("/label-signal-history/24h", dependencies=[Depends(require_admin)])
def label_signal_history_24h(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_24h_job(db)


@router.post("/label-signal-history/resolution", dependencies=[Depends(require_admin)])
def label_signal_history_resolution(db: Session = Depends(get_db)) -> dict:
    return label_signal_history_resolution_job(db)


@router.post("/label-signal-history", dependencies=[Depends(require_admin)])
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


@router.post("/cleanup-signal-history", dependencies=[Depends(require_admin)])
def cleanup_signal_history(db: Session = Depends(get_db)) -> dict:
    return cleanup_signal_history_job(db)


@router.post("/provider-contract-checks", dependencies=[Depends(require_admin)])
def provider_contract_checks(db: Session = Depends(get_db)) -> dict:
    return provider_contract_checks_job(db)


# ---------------------------------------------------------------------------
# Dry-run paper trading simulator
# ---------------------------------------------------------------------------


@router.post("/dryrun/run", dependencies=[Depends(require_admin)])
def dryrun_run(db: Session = Depends(get_db)) -> dict:
    """Run one simulation cycle: open new paper positions for qualifying signals."""
    result = dryrun_run_cycle(db)
    db.commit()
    return result


@router.get("/dryrun/report", dependencies=[Depends(require_admin)])
def dryrun_report(db: Session = Depends(get_db)) -> dict:
    """Return full dry-run portfolio report with stats and AI summary."""
    return dryrun_build_report(db)


@router.post("/dryrun/refresh-prices", dependencies=[Depends(require_admin)])
def dryrun_refresh_prices(db: Session = Depends(get_db)) -> dict:
    """Refresh CLOB mark prices for all open dry-run positions."""
    result = dryrun_refresh_mark_prices(db)
    db.commit()
    return result


@router.post("/dryrun/check-resolutions", dependencies=[Depends(require_admin)])
def dryrun_check_res(db: Session = Depends(get_db)) -> dict:
    """Close dry-run positions for markets that are now resolved."""
    result = dryrun_check_resolutions(db)
    db.commit()
    return result


@router.post("/dryrun/reset", dependencies=[Depends(require_admin)])
def dryrun_reset(db: Session = Depends(get_db)) -> dict:
    """Reset dry-run portfolio to initial $100 balance."""
    portfolio = dryrun_reset_portfolio(db)
    db.commit()
    return {"reset": True, "cash_usd": portfolio.current_cash_usd}
