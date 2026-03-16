from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import (
    Stage11Client,
    Stage11ClientPosition,
    Stage11Fill,
    Stage11Order,
    Stage11TradingAuditEvent,
)
from app.services.stage11.reports import build_stage11_track_report


def build_stage11_tenant_isolation_report(db: Session) -> dict[str, Any]:
    clients_active = int(
        db.scalar(select(func.count()).select_from(Stage11Client).where(Stage11Client.is_active.is_(True))) or 0
    )
    orders_missing_client = int(
        db.scalar(select(func.count()).select_from(Stage11Order).where(Stage11Order.client_id.is_(None))) or 0
    )
    fills_missing_client = int(
        db.scalar(select(func.count()).select_from(Stage11Fill).where(Stage11Fill.client_id.is_(None))) or 0
    )
    positions_missing_client = int(
        db.scalar(select(func.count()).select_from(Stage11ClientPosition).where(Stage11ClientPosition.client_id.is_(None)))
        or 0
    )
    audit_missing_client = int(
        db.scalar(
            select(func.count()).select_from(Stage11TradingAuditEvent).where(Stage11TradingAuditEvent.client_id.is_(None))
        )
        or 0
    )

    fills_client_mismatch = int(
        db.scalar(
            select(func.count())
            .select_from(Stage11Fill)
            .join(Stage11Order, Stage11Fill.order_id == Stage11Order.id)
            .where(Stage11Fill.client_id != Stage11Order.client_id)
        )
        or 0
    )
    audit_client_mismatch = int(
        db.scalar(
            select(func.count())
            .select_from(Stage11TradingAuditEvent)
            .join(Stage11Order, Stage11TradingAuditEvent.order_id == Stage11Order.id)
            .where(Stage11TradingAuditEvent.order_id.is_not(None))
            .where(Stage11TradingAuditEvent.client_id != Stage11Order.client_id)
        )
        or 0
    )
    positions_invalid_side = int(
        db.scalar(
            select(func.count())
            .select_from(Stage11ClientPosition)
            .where(Stage11ClientPosition.side.notin_(["YES", "NO"]))
        )
        or 0
    )

    checks = {
        "clients_active_ge_1": clients_active >= 1,
        "orders_missing_client_eq_0": orders_missing_client == 0,
        "fills_missing_client_eq_0": fills_missing_client == 0,
        "positions_missing_client_eq_0": positions_missing_client == 0,
        "audit_missing_client_eq_0": audit_missing_client == 0,
        "fills_client_mismatch_eq_0": fills_client_mismatch == 0,
        "audit_client_mismatch_eq_0": audit_client_mismatch == 0,
        "positions_invalid_side_eq_0": positions_invalid_side == 0,
    }
    failed_checks = [k for k, v in checks.items() if not bool(v)]
    final_decision = "PASS" if not failed_checks else "WARN"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "final_decision": final_decision,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": {
            "clients_active": clients_active,
            "orders_missing_client": orders_missing_client,
            "fills_missing_client": fills_missing_client,
            "positions_missing_client": positions_missing_client,
            "audit_missing_client": audit_missing_client,
            "fills_client_mismatch": fills_client_mismatch,
            "audit_client_mismatch": audit_client_mismatch,
            "positions_invalid_side": positions_invalid_side,
        },
    }


def build_stage11_final_readiness_report(
    db: Session,
    *,
    settings: Settings,
    days_execution: int = 14,
    days_client: int = 7,
) -> dict[str, Any]:
    track = build_stage11_track_report(
        db,
        settings=settings,
        days_execution=days_execution,
        days_client=days_client,
    )
    tenant = build_stage11_tenant_isolation_report(db)
    track_checks = dict(track.get("checks") or {})
    tenant_checks = dict(tenant.get("checks") or {})

    checks = {
        "stage11_acceptance_passed": str(track.get("final_decision") or "NO_GO").upper() == "GO",
        "risk_engine_stable": bool(track_checks.get("shadow_stable_14d"))
        and bool(track_checks.get("execution_error_rate_below_threshold"))
        and bool(track_checks.get("reconciliation_completeness_ge_95pct")),
        "multi_tenant_isolation_passed": all(bool(v) for v in tenant_checks.values()),
    }
    failed_checks = [k for k, v in checks.items() if not bool(v)]
    if all(bool(v) for v in checks.values()):
        final_decision = "GO"
        action = "ready_for_stage12"
    elif bool(checks["multi_tenant_isolation_passed"]) and bool(checks["risk_engine_stable"]):
        final_decision = "LIMITED_GO"
        action = "continue_stage11_acceptance_window"
    else:
        final_decision = "NO_GO"
        action = "fix_stage11_core_gaps"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "final_decision": final_decision,
        "recommended_action": action,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": {
            "stage11_track_final_decision": str(track.get("final_decision") or "NO_GO"),
            "tenant_isolation_final_decision": str(tenant.get("final_decision") or "WARN"),
        },
        "sections": {
            "stage11_track": track,
            "tenant_isolation": tenant,
        },
    }

