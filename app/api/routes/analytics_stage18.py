from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.tasks.jobs import stage18_canonicalize_job, stage18_track_job


def register_stage18_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:

    @router.get("/research/stage18/event-canonicalization", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage18_canonicalization(db: Session = Depends(get_db)) -> dict:
        from app.services.research.stage18_report import build_stage18_event_canonicalization_report
        settings = get_settings()
        return cached_heavy_get(
            key="stage18:canonicalization",
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage18_event_canonicalization_report(db, settings=settings),
        )

    @router.get("/research/stage18/topic-weights", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage18_topic_weights(db: Session = Depends(get_db)) -> dict:
        from app.services.research.stage18_report import build_stage18_topic_weights_report
        settings = get_settings()
        return cached_heavy_get(
            key="stage18:topic_weights",
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage18_topic_weights_report(db, settings=settings),
        )

    @router.get("/research/stage18/structural-arb", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage18_structural_arb(db: Session = Depends(get_db)) -> dict:
        from app.services.research.stage18_report import build_stage18_structural_arb_report
        settings = get_settings()
        return cached_heavy_get(
            key="stage18:structural_arb",
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage18_structural_arb_report(db, settings=settings),
        )

    @router.get("/research/stage18/final-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage18_final_report(db: Session = Depends(get_db)) -> dict:
        from app.services.research.stage18_report import build_stage18_final_report
        settings = get_settings()
        return cached_heavy_get(
            key="stage18:final_report",
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage18_final_report(db, settings=settings),
        )

    @router.post("/research/stage18/canonicalize", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def trigger_stage18_canonicalize(db: Session = Depends(get_db)) -> dict:
        return stage18_canonicalize_job(db)

    @router.post("/research/stage18/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def trigger_stage18_track(db: Session = Depends(get_db)) -> dict:
        return stage18_track_job(db)
