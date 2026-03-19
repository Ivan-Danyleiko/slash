from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.services.research.stage10_batch import build_stage10_batch_report
from app.services.research.stage10_final_report import (
    build_stage10_final_report,
    extract_stage10_final_report_metrics,
)
from app.services.research.stage10_module_audit import (
    build_stage10_module_audit_report,
    extract_stage10_module_audit_metrics,
)
from app.services.research.stage10_replay import (
    build_stage10_replay_report,
    extract_stage10_replay_metrics,
)
from app.services.research.stage10_timeline_backfill import build_stage10_timeline_backfill_plan
from app.services.research.stage10_timeline_backfill_run import run_stage10_timeline_backfill
from app.services.research.stage10_timeline_quality import build_stage10_timeline_quality_report
from app.services.research.tracking import record_stage5_experiment


def register_stage10_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    @router.get("/research/stage10/replay", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage10_replay(
        days: int = Query(default=365, ge=1, le=1825),
        limit: int = Query(default=5000, ge=100, le=50000),
        event_target: int = Query(default=100, ge=10, le=1000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage10:replay:{days}:{limit}:{event_target}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage10_replay_report(
                db,
                settings=settings,
                days=days,
                limit=limit,
                event_target=event_target,
                persist_rows=True,
            ),
        )

    @router.get("/research/stage10/module-audit", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage10_module_audit(db: Session = Depends(get_db)) -> dict:
        settings = get_settings()
        return build_stage10_module_audit_report(db, settings=settings)

    @router.get("/research/stage10/timeline-quality", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage10_timeline_quality(
        days: int = Query(default=365, ge=1, le=1825),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage10:timeline_quality:{days}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage10_timeline_quality_report(db, days=days),
        )

    @router.get("/research/stage10/timeline-backfill-plan", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage10_timeline_backfill_plan(
        days: int = Query(default=730, ge=1, le=3650),
        limit: int = Query(default=500, ge=50, le=50000),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage10_timeline_backfill_plan(db, days=days, limit=limit)

    @router.post("/research/stage10/timeline-backfill-run", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage10_timeline_backfill_run(
        days: int = Query(default=730, ge=1, le=3650),
        limit: int = Query(default=500, ge=50, le=50000),
        per_platform_limit: int = Query(default=100, ge=1, le=2000),
        dry_run: bool = Query(default=True),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = run_stage10_timeline_backfill(
            db,
            settings=settings,
            days=days,
            limit=limit,
            per_platform_limit=per_platform_limit,
            dry_run=dry_run,
        )
        tracking = record_stage5_experiment(
            run_name="stage10_timeline_backfill",
            params={
                "report_type": "stage10_timeline_backfill",
                "days": days,
                "limit": limit,
                "per_platform_limit": per_platform_limit,
                "dry_run": dry_run,
            },
            metrics={
                "updated_rows": float(report.get("updated_rows") or 0.0),
                "total_candidates": float(report.get("total_candidates") or 0.0),
                "updated_manifold": float((report.get("updated_by_platform") or {}).get("MANIFOLD") or 0.0),
                "updated_metaculus": float((report.get("updated_by_platform") or {}).get("METACULUS") or 0.0),
            },
            tags={"stage": "stage10"},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage10/final-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage10_final_report(
        days: int = Query(default=365, ge=1, le=1825),
        limit: int = Query(default=5000, ge=100, le=50000),
        event_target: int = Query(default=100, ge=10, le=1000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage10:final:{days}:{limit}:{event_target}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage10_final_report(
                db,
                settings=settings,
                days=days,
                limit=limit,
                event_target=event_target,
            ),
        )

    @router.get("/research/stage10/batch", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage10_batch(
        days: int = Query(default=365, ge=1, le=1825),
        limit: int = Query(default=5000, ge=100, le=50000),
        event_target: int = Query(default=100, ge=10, le=1000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage10:batch:{days}:{limit}:{event_target}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage10_batch_report(
                db,
                settings=settings,
                days=days,
                limit=limit,
                event_target=event_target,
            ),
        )

    @router.post("/research/stage10/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage10_track(
        run_name: str = Query(default="stage10_replay_and_security"),
        days: int = Query(default=365, ge=1, le=1825),
        limit: int = Query(default=5000, ge=100, le=50000),
        event_target: int = Query(default=100, ge=10, le=1000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage10_batch_report(
            db,
            settings=settings,
            days=days,
            limit=limit,
            event_target=event_target,
        )
        replay = (report.get("reports") or {}).get("stage10_replay") or {}
        audit = (report.get("reports") or {}).get("stage10_module_audit") or {}
        final = (report.get("reports") or {}).get("stage10_final_report") or {}
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "report_type": "stage10_track",
                "days": days,
                "limit": limit,
                "event_target": event_target,
            },
            metrics={
                **extract_stage10_replay_metrics(replay),
                **extract_stage10_module_audit_metrics(audit),
                **extract_stage10_final_report_metrics(final),
            },
            tags={"stage": "stage10"},
        )
        return {"report": report, "tracking": tracking}

