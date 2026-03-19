from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_read_throttle, require_admin_write_throttle
from app.core.config import get_settings
from app.db.session import get_db
from app.models.models import Stage11Client
from app.services.research.tracking import record_stage5_experiment
from app.services.stage11.order_manager import (
    append_audit_event,
    cancel_order_by_id,
    get_order_detail,
    refresh_order_status,
)
from app.services.stage11.readiness import (
    build_stage11_final_readiness_report,
    build_stage11_tenant_isolation_report,
)
from app.services.stage11.reports import (
    build_stage11_client_report,
    build_stage11_execution_report,
    build_stage11_risk_report,
    build_stage11_track_report,
)
from app.services.stage11.state_machine import can_transition
from app.tasks.jobs import stage11_reconcile_job


def register_stage11_routes(
    router: APIRouter,
    *,
    cached_heavy_get: Callable[..., Any],
) -> None:
    @router.get("/research/stage11/execution", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage11_execution(
        days: int = Query(default=14, ge=1, le=365),
        limit: int = Query(default=200, ge=10, le=2000),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage11:execution:{days}:{limit}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage11_execution_report(
                db,
                settings=settings,
                days=days,
                limit=limit,
            ),
        )

    @router.get("/research/stage11/risk", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage11_risk(
        days: int = Query(default=14, ge=1, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage11:risk:{days}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage11_risk_report(db, settings=settings, days=days),
        )

    @router.get(
        "/research/stage11/client-report",
        response_model=None,
        dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)],
    )
    def research_stage11_client_report(
        days: int = Query(default=7, ge=1, le=365),
        as_csv: bool = Query(default=False),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage11:client_report:{days}"
        report = cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage11_client_report(db, days=days),
        )
        if not as_csv:
            return report
        out = StringIO()
        writer = csv.writer(out)
        writer.writerow(["client_id", "client_code", "runtime_mode", "orders", "fills", "realized_pnl_usd"])
        for row in list(report.get("rows") or []):
            writer.writerow(
                [
                    row.get("client_id"),
                    row.get("client_code"),
                    row.get("runtime_mode"),
                    row.get("orders"),
                    row.get("fills"),
                    row.get("realized_pnl_usd"),
                ]
            )
        return Response(
            content=out.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="stage11_client_report.csv"'},
        )

    @router.post("/research/stage11/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage11_track(
        run_name: str = Query(default="stage11_track"),
        days_execution: int = Query(default=14, ge=1, le=365),
        days_client: int = Query(default=7, ge=1, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage11_track_report(
            db,
            settings=settings,
            days_execution=days_execution,
            days_client=days_client,
        )
        summary = dict(report.get("summary") or {})
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "report_type": "stage11_track",
                "days_execution": days_execution,
                "days_client": days_client,
            },
            metrics={
                "orders_total": float(summary.get("orders_total") or 0.0),
                "blocked_count": float(summary.get("blocked_count") or 0.0),
                "shadow_skipped_count": float(summary.get("shadow_skipped_count") or 0.0),
                "clients": float(summary.get("clients") or 0.0),
                "realized_pnl_usd_total": float(summary.get("realized_pnl_usd_total") or 0.0),
            },
            tags={"stage": "stage11", "final_decision": str(report.get("final_decision") or "")},
        )
        return {"report": report, "tracking": tracking}

    @router.get("/research/stage11/tenant-isolation", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage11_tenant_isolation(
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        return cached_heavy_get(
            key="stage11:tenant_isolation",
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage11_tenant_isolation_report(db),
        )

    @router.get("/research/stage11/final-readiness", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage11_final_readiness(
        days_execution: int = Query(default=14, ge=1, le=365),
        days_client: int = Query(default=7, ge=1, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        cache_key = f"stage11:final_readiness:{days_execution}:{days_client}"
        return cached_heavy_get(
            key=cache_key,
            ttl_sec=int(settings.admin_heavy_get_cache_ttl_sec),
            builder=lambda: build_stage11_final_readiness_report(
                db,
                settings=settings,
                days_execution=days_execution,
                days_client=days_client,
            ),
        )

    @router.post("/research/stage11/final-readiness/track", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage11_final_readiness_track(
        run_name: str = Query(default="stage11_final_readiness_track"),
        days_execution: int = Query(default=14, ge=1, le=365),
        days_client: int = Query(default=7, ge=1, le=365),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        report = build_stage11_final_readiness_report(
            db,
            settings=settings,
            days_execution=days_execution,
            days_client=days_client,
        )
        checks = dict(report.get("checks") or {})
        summary = dict(report.get("summary") or {})
        tracking = record_stage5_experiment(
            run_name=run_name,
            params={
                "report_type": "stage11_final_readiness",
                "days_execution": days_execution,
                "days_client": days_client,
            },
            metrics={
                "stage11_acceptance_passed": 1.0 if bool(checks.get("stage11_acceptance_passed")) else 0.0,
                "risk_engine_stable": 1.0 if bool(checks.get("risk_engine_stable")) else 0.0,
                "multi_tenant_isolation_passed": 1.0 if bool(checks.get("multi_tenant_isolation_passed")) else 0.0,
            },
            tags={
                "stage": "stage11",
                "report_type": "final_readiness",
                "final_decision": str(report.get("final_decision") or ""),
                "track_final_decision": str(summary.get("stage11_track_final_decision") or ""),
            },
        )
        return {"report": report, "tracking": tracking}

    @router.post("/research/stage11/reconcile", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage11_reconcile(
        max_unknown_recovery_sec: int | None = Query(default=None, ge=30, le=3600),
        db: Session = Depends(get_db),
    ) -> dict:
        return stage11_reconcile_job(db, max_unknown_recovery_sec=max_unknown_recovery_sec)

    @router.get("/research/stage11/orders/{order_id}", dependencies=[Depends(require_admin), Depends(require_admin_read_throttle)])
    def research_stage11_order_detail(
        order_id: int,
        db: Session = Depends(get_db),
    ) -> dict:
        detail = get_order_detail(db, order_id=order_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="order_not_found")
        return detail

    @router.post("/research/stage11/orders/{order_id}/refresh-status", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage11_order_refresh_status(
        order_id: int,
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        out = refresh_order_status(db, settings=settings, order_id=order_id)
        if out.get("status") == "error":
            raise HTTPException(status_code=404, detail=str(out.get("error") or "unknown_error"))
        return out

    @router.post("/research/stage11/orders/{order_id}/cancel", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage11_order_cancel(
        order_id: int,
        reason: str = Query(default="manual_cancel"),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        out = cancel_order_by_id(db, settings=settings, order_id=order_id, reason=reason)
        if out.get("status") == "error":
            raise HTTPException(status_code=404, detail=str(out.get("error") or "unknown_error"))
        return out

    @router.post("/research/stage11/runtime-mode", dependencies=[Depends(require_admin), Depends(require_admin_write_throttle)])
    def research_stage11_runtime_mode(
        client_code: str = Query(default="default"),
        target_mode: str = Query(..., description="SHADOW|LIMITED_EXECUTION|FULL_EXECUTION"),
        manual_approve: bool = Query(default=False),
        reason: str = Query(default="manual_transition"),
        db: Session = Depends(get_db),
    ) -> dict:
        settings = get_settings()
        client = db.scalar(select(Stage11Client).where(Stage11Client.code == client_code).limit(1))
        if client is None:
            raise HTTPException(status_code=404, detail="client_not_found")
        allowed, code = can_transition(str(client.runtime_mode or "SHADOW"), str(target_mode), manual_approve=manual_approve)
        if not allowed:
            return {
                "status": "rejected",
                "client_id": int(client.id),
                "client_code": str(client.code),
                "current_mode": str(client.runtime_mode),
                "target_mode": str(target_mode).upper(),
                "reason_code": code,
            }
        gate = build_stage11_track_report(db, settings=settings, days_execution=14, days_client=7)
        checks = dict(gate.get("checks") or {})
        target = str(target_mode).upper()
        if target == "LIMITED_EXECUTION" and not (
            bool(checks.get("custody_mode_approved"))
            and bool(checks.get("shadow_stable_14d"))
            and bool(checks.get("execution_error_rate_below_threshold"))
            and bool(checks.get("no_security_incident"))
            and bool(checks.get("audit_trail_coverage_100pct"))
        ):
            return {
                "status": "rejected",
                "client_id": int(client.id),
                "client_code": str(client.code),
                "current_mode": str(client.runtime_mode),
                "target_mode": target,
                "reason_code": "limited_gate_not_passed",
                "failed_checks": [k for k, v in checks.items() if not bool(v)],
            }
        if target == "FULL_EXECUTION" and not (
            bool(checks.get("custody_mode_approved"))
            and bool(checks.get("shadow_stable_14d"))
            and bool(checks.get("limited_execution_min_30d_or_100_trades"))
            and bool(checks.get("execution_error_rate_below_threshold"))
            and bool(checks.get("reconciliation_completeness_ge_95pct"))
            and bool(checks.get("no_security_incident"))
            and bool(checks.get("realized_post_cost_return_not_below_stage10_baseline"))
            and bool(checks.get("audit_trail_coverage_100pct"))
        ):
            return {
                "status": "rejected",
                "client_id": int(client.id),
                "client_code": str(client.code),
                "current_mode": str(client.runtime_mode),
                "target_mode": target,
                "reason_code": "full_gate_not_passed",
                "failed_checks": [k for k, v in checks.items() if not bool(v)],
            }
        previous = str(client.runtime_mode or "SHADOW")
        client.runtime_mode = str(target_mode).upper()
        client.updated_at = datetime.now(UTC)
        db.add(client)
        append_audit_event(
            db,
            client_id=int(client.id),
            order_id=None,
            event_type="MANUAL_RUNTIME_MODE_CHANGE",
            severity="INFO",
            payload={"from": previous, "to": str(client.runtime_mode), "reason": reason},
        )
        db.commit()
        return {
            "status": "ok",
            "client_id": int(client.id),
            "client_code": str(client.code),
            "from_mode": previous,
            "to_mode": str(client.runtime_mode),
            "reason_code": code,
        }

