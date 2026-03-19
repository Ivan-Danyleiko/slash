from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.services.research.stage7_calibration import build_stage7_calibration_report
from app.services.research.stage7_final_report import (
    build_stage7_final_report,
    extract_stage7_final_report_metrics,
)
from app.services.research.stage7_harness import (
    build_stage7_harness_report,
    extract_stage7_harness_metrics,
)
from app.services.research.stage7_shadow import (
    build_stage7_shadow_report,
    extract_stage7_shadow_metrics,
)
from app.services.research.stage7_stack_scorecard import (
    build_stage7_stack_scorecard_report,
    extract_stage7_stack_scorecard_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def register_stage7_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    @router.get("/research/stage7/stack-scorecard", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage7_stack_scorecard(
        include_harness: bool = Query(default=True),
        max_latency_ms: int = Query(default=1200, ge=1, le=100000),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage7:stack_scorecard:{int(bool(include_harness))}:{max_latency_ms}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage7_stack_scorecard_report(
                harness_by_stack=((build_stage7_harness_report(max_latency_ms=max_latency_ms) if include_harness else {}) or {}).get(
                    "by_stack"
                ),
            ),
        )

    @router.post("/research/stage7/stack-scorecard/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage7_stack_scorecard_track(
        run_name: str = Query(default="stage7_stack_scorecard"),
        include_harness: bool = Query(default=True),
        max_latency_ms: int = Query(default=1200, ge=1, le=100000),
    ) -> dict:
        harness = build_stage7_harness_report(max_latency_ms=max_latency_ms) if include_harness else None
        report = build_stage7_stack_scorecard_report(
            harness_by_stack=(harness or {}).get("by_stack"),
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "report_type": "stage7_stack_scorecard",
                "include_harness": include_harness,
                "max_latency_ms": max_latency_ms,
            },
            metrics=extract_stage7_stack_scorecard_metrics(report),
            tags={"top_stack": str((report.get("summary") or {}).get("top_stack") or "")},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage7/harness", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage7_harness(
        max_latency_ms: int = Query(default=1200, ge=1, le=100000),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage7:harness:{max_latency_ms}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage7_harness_report(max_latency_ms=max_latency_ms),
        )

    @router.post("/research/stage7/harness/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage7_harness_track(
        max_latency_ms: int = Query(default=1200, ge=1, le=100000),
        run_name: str = Query(default="stage7_harness"),
    ) -> dict:
        report = build_stage7_harness_report(max_latency_ms=max_latency_ms)
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={"report_type": "stage7_harness", "max_latency_ms": max_latency_ms},
            metrics=extract_stage7_harness_metrics(report),
            tags={"all_pass_rate_gte_80pct": str((report.get("summary") or {}).get("all_pass_rate_gte_80pct"))},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage7/shadow", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage7_shadow(
        lookback_days: int = Query(default=14, ge=1, le=90),
        limit: int = Query(default=300, ge=1, le=2000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage7:shadow:{lookback_days}:{limit}:"
            f"{settings.stage7_agent_provider}:{settings.stage7_agent_provider_profile}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage7_shadow_report(
                db,
                settings=settings,
                lookback_days=lookback_days,
                limit=limit,
            ),
        )

    @router.get("/stage7-calibration")
    def stage7_calibration(
        days: int = Query(default=90, ge=1, le=730),
        horizon: str = Query(default="6h"),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage7_calibration_report(db, days=days, horizon=horizon)

    @router.post("/research/stage7/shadow/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage7_shadow_track(
        lookback_days: int = Query(default=14, ge=1, le=90),
        limit: int = Query(default=300, ge=1, le=2000),
        run_name: str = Query(default="stage7_shadow"),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage7_shadow_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage7_shadow"},
            metrics=extract_stage7_shadow_metrics(report),
            tags={"provider": settings.stage7_agent_provider},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage7/final-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage7_final_report(
        lookback_days: int = Query(default=14, ge=1, le=90),
        limit: int = Query(default=300, ge=1, le=2000),
        stage6_days: int = Query(default=30, ge=1, le=365),
        stage6_horizon: str = Query(default="6h"),
        stage6_min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage7:final:{lookback_days}:{limit}:{stage6_days}:{stage6_horizon}:{stage6_min_labeled_returns}:"
            f"{settings.stage7_agent_provider}:{settings.stage7_agent_provider_profile}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage7_final_report(
                db,
                settings=settings,
                lookback_days=lookback_days,
                limit=limit,
                stage6_days=stage6_days,
                stage6_horizon=stage6_horizon,
                stage6_min_labeled_returns=stage6_min_labeled_returns,
            ),
        )

    @router.post("/research/stage7/final-report/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage7_final_report_track(
        lookback_days: int = Query(default=14, ge=1, le=90),
        limit: int = Query(default=300, ge=1, le=2000),
        stage6_days: int = Query(default=30, ge=1, le=365),
        stage6_horizon: str = Query(default="6h"),
        stage6_min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        run_name: str = Query(default="stage7_final_report"),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage7_final_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
            stage6_days=stage6_days,
            stage6_horizon=stage6_horizon,
            stage6_min_labeled_returns=stage6_min_labeled_returns,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "lookback_days": lookback_days,
                "limit": limit,
                "stage6_days": stage6_days,
                "stage6_horizon": stage6_horizon,
                "stage6_min_labeled_returns": stage6_min_labeled_returns,
                "report_type": "stage7_final_report",
            },
            metrics=extract_stage7_final_report_metrics(report),
            tags={
                "final_decision": str(report.get("final_decision")),
                "recommended_action": str(report.get("recommended_action")),
            },
        )
        return {"report": report, "tracking": tracking}
