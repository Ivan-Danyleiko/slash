from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.services.research.stage9_batch import build_stage9_batch_report
from app.services.research.stage9_final_report import build_stage9_final_report
from app.services.research.stage9_reports import (
    build_stage9_consensus_quality_report,
    build_stage9_directional_labeling_report,
    build_stage9_execution_realism_report,
)
from app.services.research.tracking import record_stage5_experiment


def register_stage9_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    @router.get("/research/stage9/consensus-quality", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage9_consensus_quality(
        days: int = Query(default=14, ge=1, le=180),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage9_consensus_quality_report(db, days=days)

    @router.get("/research/stage9/directional-labeling", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage9_directional_labeling(
        days: int = Query(default=30, ge=1, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage9_directional_labeling_report(db, days=days)

    @router.get("/research/stage9/execution-realism", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage9_execution_realism(
        days: int = Query(default=14, ge=1, le=180),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage9_execution_realism_report(db, days=days)

    @router.get("/research/stage9/batch", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage9_batch(
        days_consensus: int = Query(default=14, ge=1, le=180),
        days_labeling: int = Query(default=30, ge=1, le=365),
        days_execution: int = Query(default=14, ge=1, le=180),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage9:batch:{days_consensus}:{days_labeling}:{days_execution}:"
            f"{settings.stage9_consensus_weight_polymarket}:{settings.stage9_consensus_weight_manifold}:"
            f"{settings.stage9_consensus_weight_metaculus}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage9_batch_report(
                db,
                settings=settings,
                days_consensus=days_consensus,
                days_labeling=days_labeling,
                days_execution=days_execution,
            ),
        )

    @router.get("/research/stage9/final-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage9_final_report(
        days_consensus: int = Query(default=14, ge=1, le=180),
        days_labeling: int = Query(default=30, ge=1, le=365),
        days_execution: int = Query(default=14, ge=1, le=180),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage9:final:{days_consensus}:{days_labeling}:{days_execution}:"
            f"{settings.stage9_consensus_weight_polymarket}:{settings.stage9_consensus_weight_manifold}:"
            f"{settings.stage9_consensus_weight_metaculus}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage9_final_report(
                db,
                settings=settings,
                days_consensus=days_consensus,
                days_labeling=days_labeling,
                days_execution=days_execution,
            ),
        )

    @router.post("/research/stage9/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage9_track(
        run_name: str = Query(default="stage9_source_quality"),
        days_consensus: int = Query(default=14, ge=1, le=180),
        days_labeling: int = Query(default=30, ge=1, le=365),
        days_execution: int = Query(default=14, ge=1, le=180),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage9_batch_report(
            db,
            settings=settings,
            days_consensus=days_consensus,
            days_labeling=days_labeling,
            days_execution=days_execution,
        )
        consensus = (report.get("reports") or {}).get("stage9_consensus_quality") or {}
        labeling = (report.get("reports") or {}).get("stage9_directional_labeling") or {}
        execution = (report.get("reports") or {}).get("stage9_execution_realism") or {}
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "report_type": "stage9_track",
                "days_consensus": days_consensus,
                "days_labeling": days_labeling,
                "days_execution": days_execution,
            },
            metrics={
                "metaculus_median_fill_rate": float(consensus.get("metaculus_median_fill_rate") or 0.0),
                "consensus_3source_share": float(consensus.get("consensus_3source_share") or 0.0),
                "direction_labeled_share": float(labeling.get("direction_labeled_share") or 0.0),
                "direction_missing_label_share": float(labeling.get("direction_missing_label_share") or 0.0),
                "non_zero_edge_share": float(execution.get("non_zero_edge_share") or 0.0),
                "spread_coverage_share": float(execution.get("spread_coverage_share") or 0.0),
                "open_interest_coverage_share": float(execution.get("open_interest_coverage_share") or 0.0),
                "brier_skill_score": float(execution.get("brier_skill_score") or 0.0),
                "ece": float(execution.get("ece") or 0.0),
                "precision_at_25": float(execution.get("precision_at_25") or 0.0),
                "auprc": float(execution.get("auprc") or 0.0),
            },
            tags={"stage": "stage9"},
        )
        return {"report": report, "tracking": tracking}
