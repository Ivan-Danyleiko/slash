from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.services.research.stage17_batch import build_stage17_batch_report
from app.services.research.stage17_tail_report import (
    build_stage17_tail_report,
    extract_stage17_tail_report_metrics,
)
from app.services.research.tracking import record_stage5_experiment
from app.tasks.jobs import stage17_batch_job, stage17_cycle_job, stage17_track_job


def register_stage17_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    @router.get("/research/stage17/tail-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage17_tail_report(
        days: int = Query(default=60, ge=7, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage17:tail_report:{days}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage17_tail_report(
                db,
                settings=settings,
                days=days,
                persist=True,
            ),
        )

    @router.post("/research/stage17/tail-report/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage17_tail_report_track(
        run_name: str = Query(default="stage17_tail_report"),
        days: int = Query(default=60, ge=7, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage17_tail_report(db, settings=settings, days=days, persist=True)
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={"report_type": "stage17_tail_report", "days": days},
            metrics=extract_stage17_tail_report_metrics(report),
            tags={"stage": "stage17", "final_decision": str(report.get("final_decision") or "")},
        )
        return {"report": report, "tracking": tracking}

    @router.post("/research/stage17/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage17_track(
        days: int = Query(default=60, ge=7, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        return stage17_track_job(db, days=days)

    @router.post("/research/stage17/cycle", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage17_cycle(
        limit: int = Query(default=20, ge=1, le=200),
        db: Session = Depends(get_db),
    ) -> dict:
        return stage17_cycle_job(db, limit=limit)

    @router.get("/research/stage17/batch", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage17_batch(
        days: int = Query(default=60, ge=7, le=365),
        cycle_limit: int = Query(default=20, ge=1, le=200),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage17:batch:{days}:{cycle_limit}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage17_batch_report(
                db,
                settings=settings,
                days=days,
                cycle_limit=cycle_limit,
            ),
        )

    @router.post("/research/stage17/batch/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage17_batch_track(
        days: int = Query(default=60, ge=7, le=365),
        cycle_limit: int = Query(default=20, ge=1, le=200),
        db: Session = Depends(get_db),
    ) -> dict:
        return stage17_batch_job(db, days=days, cycle_limit=cycle_limit)

