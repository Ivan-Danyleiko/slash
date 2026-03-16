from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
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


@router.post("/cleanup-signal-history", dependencies=[Depends(require_admin)])
def cleanup_signal_history(db: Session = Depends(get_db)) -> dict:
    return cleanup_signal_history_job(db)


@router.post("/provider-contract-checks", dependencies=[Depends(require_admin)])
def provider_contract_checks(db: Session = Depends(get_db)) -> dict:
    return provider_contract_checks_job(db)
