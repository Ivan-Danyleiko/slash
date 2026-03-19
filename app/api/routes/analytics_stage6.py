from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.db.session import get_db
from app.models.enums import SignalType
from app.services.research.stage6_final_report import (
    build_stage6_final_report,
    extract_stage6_final_report_metrics,
)
from app.services.research.stage6_governance import (
    build_stage6_governance_report,
    extract_stage6_governance_metrics,
)
from app.services.research.stage6_risk_guardrails import (
    build_stage6_risk_guardrails_report,
    extract_stage6_risk_guardrails_metrics,
)
from app.services.research.stage6_type35 import (
    build_stage6_type35_report,
    extract_stage6_type35_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def register_stage6_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    _ = cached_heavy_get

    @router.get("/research/stage6-governance", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage6_governance(
        days: int = Query(default=30, ge=1, le=365),
        horizon: str = Query(default="6h"),
        min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        walkforward_days: int = Query(default=90, ge=14, le=365),
        walkforward_train_days: int = Query(default=30, ge=1, le=180),
        walkforward_test_days: int = Query(default=14, ge=1, le=90),
        walkforward_step_days: int = Query(default=14, ge=1, le=90),
        walkforward_embargo_hours: int = Query(default=24, ge=0, le=24 * 7),
        walkforward_min_samples: int = Query(default=100, ge=10, le=100000),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage6_governance_report(
            db,
            days=days,
            horizon=horizon,
            min_labeled_returns=min_labeled_returns,
            walkforward_days=walkforward_days,
            walkforward_train_days=walkforward_train_days,
            walkforward_test_days=walkforward_test_days,
            walkforward_step_days=walkforward_step_days,
            walkforward_embargo_hours=walkforward_embargo_hours,
            walkforward_min_samples=walkforward_min_samples,
        )

    @router.post("/research/stage6-governance/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage6_governance_track(
        days: int = Query(default=30, ge=1, le=365),
        horizon: str = Query(default="6h"),
        min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        walkforward_days: int = Query(default=90, ge=14, le=365),
        walkforward_train_days: int = Query(default=30, ge=1, le=180),
        walkforward_test_days: int = Query(default=14, ge=1, le=90),
        walkforward_step_days: int = Query(default=14, ge=1, le=90),
        walkforward_embargo_hours: int = Query(default=24, ge=0, le=24 * 7),
        walkforward_min_samples: int = Query(default=100, ge=10, le=100000),
        run_name: str = Query(default="stage6_governance"),
        db: Session = Depends(get_db),
    ) -> dict:
        report = build_stage6_governance_report(
            db,
            days=days,
            horizon=horizon,
            min_labeled_returns=min_labeled_returns,
            walkforward_days=walkforward_days,
            walkforward_train_days=walkforward_train_days,
            walkforward_test_days=walkforward_test_days,
            walkforward_step_days=walkforward_step_days,
            walkforward_embargo_hours=walkforward_embargo_hours,
            walkforward_min_samples=walkforward_min_samples,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "days": days,
                "horizon": horizon,
                "min_labeled_returns": min_labeled_returns,
                "walkforward_days": walkforward_days,
                "walkforward_train_days": walkforward_train_days,
                "walkforward_test_days": walkforward_test_days,
                "walkforward_step_days": walkforward_step_days,
                "walkforward_embargo_hours": walkforward_embargo_hours,
                "walkforward_min_samples": walkforward_min_samples,
                "report_type": "stage6_governance",
            },
            metrics=extract_stage6_governance_metrics(report),
            tags={
                "decision": str(report.get("decision")),
                "overfit_flags": str(len(report.get("overfit_flags") or [])),
            },
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage6-risk-guardrails", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage6_risk_guardrails(
        days: int = Query(default=7, ge=1, le=60),
        horizon: str = Query(default="6h"),
        signal_type: str = Query(default=SignalType.DIVERGENCE.value),
        nav_usd: float = Query(default=10000.0, ge=100.0, le=100000000.0),
        rollback_min_samples: int = Query(default=30, ge=10, le=100000),
        rollback_pvalue_threshold: float = Query(default=0.10, ge=0.001, le=0.5),
        rollback_cooldown_days: int = Query(default=7, ge=1, le=60),
        db: Session = Depends(get_db),
    ) -> dict:
        report = build_stage6_risk_guardrails_report(
            db,
            days=days,
            horizon=horizon,
            signal_type=signal_type,
            nav_usd=nav_usd,
            rollback_min_samples=rollback_min_samples,
            rollback_pvalue_threshold=rollback_pvalue_threshold,
            rollback_cooldown_days=rollback_cooldown_days,
        )
        if "error" in report:
            raise HTTPException(status_code=400, detail=report)
        return report

    @router.post("/research/stage6-risk-guardrails/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage6_risk_guardrails_track(
        days: int = Query(default=7, ge=1, le=60),
        horizon: str = Query(default="6h"),
        signal_type: str = Query(default=SignalType.DIVERGENCE.value),
        nav_usd: float = Query(default=10000.0, ge=100.0, le=100000000.0),
        rollback_min_samples: int = Query(default=30, ge=10, le=100000),
        rollback_pvalue_threshold: float = Query(default=0.10, ge=0.001, le=0.5),
        rollback_cooldown_days: int = Query(default=7, ge=1, le=60),
        run_name: str = Query(default="stage6_risk_guardrails"),
        db: Session = Depends(get_db),
    ) -> dict:
        report = build_stage6_risk_guardrails_report(
            db,
            days=days,
            horizon=horizon,
            signal_type=signal_type,
            nav_usd=nav_usd,
            rollback_min_samples=rollback_min_samples,
            rollback_pvalue_threshold=rollback_pvalue_threshold,
            rollback_cooldown_days=rollback_cooldown_days,
        )
        if "error" in report:
            raise HTTPException(status_code=400, detail=report)
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "days": days,
                "horizon": horizon,
                "signal_type": signal_type,
                "nav_usd": nav_usd,
                "rollback_min_samples": rollback_min_samples,
                "rollback_pvalue_threshold": rollback_pvalue_threshold,
                "rollback_cooldown_days": rollback_cooldown_days,
                "report_type": "stage6_risk_guardrails",
            },
            metrics=extract_stage6_risk_guardrails_metrics(report),
            tags={
                "circuit_breaker_level": str(report.get("circuit_breaker_level")),
                "rollback_triggered": str(((report.get("rollback") or {}).get("triggered"))),
            },
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage6-type35", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage6_type35(
        days: int = Query(default=30, ge=1, le=365),
        horizon: str = Query(default="6h"),
        min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
        keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
        keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
        keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
        modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
        min_subhour_coverage: float = Query(default=0.20, ge=0.0, le=1.0),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage6_type35_report(
            db,
            days=days,
            horizon=horizon,
            min_labeled_returns=min_labeled_returns,
            keep_ev_min=keep_ev_min,
            keep_hit_rate_min=keep_hit_rate_min,
            keep_sharpe_like_min=keep_sharpe_like_min,
            keep_risk_of_ruin_max=keep_risk_of_ruin_max,
            modify_ev_min=modify_ev_min,
            min_subhour_coverage=min_subhour_coverage,
        )

    @router.post("/research/stage6-type35/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage6_type35_track(
        days: int = Query(default=30, ge=1, le=365),
        horizon: str = Query(default="6h"),
        min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
        keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
        keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
        keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
        modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
        min_subhour_coverage: float = Query(default=0.20, ge=0.0, le=1.0),
        run_name: str = Query(default="stage6_type35"),
        db: Session = Depends(get_db),
    ) -> dict:
        report = build_stage6_type35_report(
            db,
            days=days,
            horizon=horizon,
            min_labeled_returns=min_labeled_returns,
            keep_ev_min=keep_ev_min,
            keep_hit_rate_min=keep_hit_rate_min,
            keep_sharpe_like_min=keep_sharpe_like_min,
            keep_risk_of_ruin_max=keep_risk_of_ruin_max,
            modify_ev_min=modify_ev_min,
            min_subhour_coverage=min_subhour_coverage,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "days": days,
                "horizon": horizon,
                "min_labeled_returns": min_labeled_returns,
                "keep_ev_min": keep_ev_min,
                "keep_hit_rate_min": keep_hit_rate_min,
                "keep_sharpe_like_min": keep_sharpe_like_min,
                "keep_risk_of_ruin_max": keep_risk_of_ruin_max,
                "modify_ev_min": modify_ev_min,
                "min_subhour_coverage": min_subhour_coverage,
                "report_type": "stage6_type35",
            },
            metrics=extract_stage6_type35_metrics(report),
            tags={"decision_counts": str(report.get("decision_counts"))},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage6-final-report", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage6_final_report(
        days: int = Query(default=30, ge=1, le=365),
        horizon: str = Query(default="6h"),
        min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        db: Session = Depends(get_db),
    ) -> dict:
        return build_stage6_final_report(
            db,
            days=days,
            horizon=horizon,
            min_labeled_returns=min_labeled_returns,
        )

    @router.post("/research/stage6-final-report/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage6_final_report_track(
        days: int = Query(default=30, ge=1, le=365),
        horizon: str = Query(default="6h"),
        min_labeled_returns: int = Query(default=30, ge=1, le=100000),
        run_name: str = Query(default="stage6_final_report"),
        db: Session = Depends(get_db),
    ) -> dict:
        report = build_stage6_final_report(
            db,
            days=days,
            horizon=horizon,
            min_labeled_returns=min_labeled_returns,
        )
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "days": days,
                "horizon": horizon,
                "min_labeled_returns": min_labeled_returns,
                "report_type": "stage6_final_report",
            },
            metrics=extract_stage6_final_report_metrics(report),
            tags={
                "final_decision": str(report.get("final_decision")),
                "recommended_action": str(report.get("recommended_action")),
            },
        )
        return {"report": report, "tracking": tracking}
