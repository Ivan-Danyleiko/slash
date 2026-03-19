from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.models.models import Market, Platform
from app.services.agent_stage8 import (
    classify_market_category,
    evaluate_rules_fields,
    get_category_policy,
    get_category_policy_profile,
    profile_summary,
)
from app.services.research.stage8_batch import build_stage8_batch_report
from app.services.research.stage8_final_report import (
    build_stage8_final_report,
    extract_stage8_final_report_metrics,
)
from app.services.research.stage8_shadow_ledger import (
    build_stage8_shadow_ledger_report,
    extract_stage8_shadow_ledger_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def register_stage8_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    @router.get("/research/stage8/shadow-ledger", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage8_shadow_ledger(
        lookback_days: int = Query(default=14, ge=1, le=365),
        limit: int = Query(default=300, ge=50, le=5000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage8:shadow:{lookback_days}:{limit}:"
            f"{settings.stage8_policy_profile}:{settings.stage8_policy_version}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage8_shadow_ledger_report(
                db,
                settings=settings,
                lookback_days=lookback_days,
                limit=limit,
            ),
        )

    @router.get("/research/stage8/category-policies", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage8_category_policies() -> dict:
        settings = get_settings()
        profile_name, profile = get_category_policy_profile(settings.stage8_policy_profile)
        return {
            "profile_name": profile_name,
            "policy_version": settings.stage8_policy_version,
            "profile": profile_summary(profile),
        }

    @router.get("/research/stage8/rules-field", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage8_rules_field(
        market_id: int = Query(..., ge=1),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        market = db.get(Market, market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Market not found")
        platform = db.get(Platform, market.platform_id)
        _, profile = get_category_policy_profile(settings.stage8_policy_profile)
        category_result = classify_market_category(
            market,
            confidence_floor=float(settings.stage8_category_confidence_floor),
        )
        category_policy = get_category_policy(category_result.category, profile)
        rules = evaluate_rules_fields(
            market,
            platform=platform,
            category_policy=category_policy,
        )
        return {
            "market_id": market.id,
            "category": category_result.category,
            "category_confidence": category_result.confidence,
            "rules_ambiguity_score": rules.ambiguity_score,
            "resolution_source_confidence": rules.resolution_source_confidence,
            "dispute_risk_flag": rules.dispute_risk_flag,
            "reason_codes": rules.reason_codes,
            "policy_thresholds": category_policy,
        }

    @router.post("/research/stage8/shadow-ledger/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage8_shadow_ledger_track(
        lookback_days: int = Query(default=14, ge=1, le=365),
        limit: int = Query(default=300, ge=50, le=5000),
        run_name: str = Query(default="stage8_shadow_ledger"),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage8_shadow_ledger_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage8_shadow_ledger"},
            metrics=extract_stage8_shadow_ledger_metrics(report),
            tags={"policy_profile": settings.stage8_policy_profile},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage8/final-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage8_final_report(
        lookback_days: int = Query(default=14, ge=1, le=365),
        limit: int = Query(default=300, ge=50, le=5000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage8:final:{lookback_days}:{limit}:"
            f"{settings.stage8_policy_profile}:{settings.stage8_policy_version}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage8_final_report(
                db,
                settings=settings,
                lookback_days=lookback_days,
                limit=limit,
                shadow_report=build_stage8_shadow_ledger_report(
                    db,
                    settings=settings,
                    lookback_days=lookback_days,
                    limit=limit,
                ),
            ),
        )

    @router.post("/research/stage8/final-report/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage8_final_report_track(
        lookback_days: int = Query(default=14, ge=1, le=365),
        limit: int = Query(default=300, ge=50, le=5000),
        run_name: str = Query(default="stage8_final_report"),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        shadow = build_stage8_shadow_ledger_report(db, settings=settings, lookback_days=lookback_days, limit=limit)
        report = build_stage8_final_report(
            db,
            settings=settings,
            lookback_days=lookback_days,
            limit=limit,
            shadow_report=shadow,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage8_final_report"},
            metrics=extract_stage8_final_report_metrics(report),
            tags={"final_decision": str(report.get("final_decision") or "")},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage8/batch", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage8_batch(
        lookback_days: int = Query(default=14, ge=1, le=365),
        limit: int = Query(default=300, ge=50, le=5000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = (
            f"stage8:batch:{lookback_days}:{limit}:"
            f"{settings.stage8_policy_profile}:{settings.stage8_policy_version}"
        )
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage8_batch_report(
                db,
                settings=settings,
                lookback_days=lookback_days,
                limit=limit,
            ),
        )
