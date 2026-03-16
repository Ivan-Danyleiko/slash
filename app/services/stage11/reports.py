from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import (
    Market,
    Signal,
    Stage8Decision,
    Stage11Client,
    Stage11ClientPosition,
    Stage11Fill,
    Stage11Order,
    Stage11TradingAuditEvent,
)
from app.services.research.stage10_final_report import build_stage10_final_report
from app.services.research.provider_reliability import build_provider_reliability_report
from app.services.stage11.order_manager import append_audit_event, create_or_reuse_order, reconcile_unknown_submits
from app.services.stage11.risk_engine import Stage11RiskInput, resolve_circuit_breaker_level


def _ensure_default_client(db: Session) -> Stage11Client:
    row = db.scalar(select(Stage11Client).where(Stage11Client.code == "default").limit(1))
    if row is not None:
        return row
    row = Stage11Client(
        code="default",
        name="Default Client",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="SHADOW",
        is_active=True,
        risk_profile={"starting_capital_usd": 1000.0, "max_open_exposure_usd": 300.0},
    )
    db.add(row)
    db.flush()
    return row


def _size_bucket_from_notional(notional_usd: float) -> str:
    n = float(notional_usd)
    if n < 100:
        return "small"
    if n <= 500:
        return "medium"
    return "large"


def _pretrade_hard_block_reasons(
    *,
    data_sufficient_for_acceptance: bool,
    market: Market | None,
    now: datetime,
    rules_ambiguity_score: float | None,
    edge_after_costs: float | None,
    min_edge_threshold: float,
    client_open_exposure_usd: float,
    max_open_exposure_usd: float,
    provider_health_degraded: bool,
) -> list[str]:
    reasons: list[str] = []
    if not data_sufficient_for_acceptance:
        reasons.append("data_sufficient_for_acceptance_false")
    if market is None:
        reasons.append("market_missing")
    else:
        status = str(market.status or "").upper()
        if status in {"CLOSED", "RESOLVED", "SETTLED", "ENDED"}:
            reasons.append("market_inactive_or_resolved")
        if market.resolution_time is not None and market.resolution_time <= (now + timedelta(hours=1)):
            reasons.append("market_resolving_soon")
    if rules_ambiguity_score is not None and float(rules_ambiguity_score) > 0.50:
        reasons.append("rules_ambiguity_above_hard_limit")
    if edge_after_costs is None or float(edge_after_costs) < float(min_edge_threshold):
        reasons.append("expected_edge_after_costs_below_min")
    if float(client_open_exposure_usd) > float(max_open_exposure_usd):
        reasons.append("client_exposure_limit_exceeded")
    if provider_health_degraded:
        reasons.append("provider_health_degraded")
    return reasons


def _allowed_custody_modes(settings: Settings) -> set[str]:
    raw = str(settings.stage11_allowed_custody_modes or "").strip()
    if not raw:
        return {"CLIENT_SIGNED", "MANAGED_HOT_WALLET"}
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = int(math.ceil(0.95 * len(ordered))) - 1
    idx = max(0, min(idx, len(ordered) - 1))
    return float(ordered[idx])


def _as_utc_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def build_stage11_execution_report(
    db: Session,
    *,
    settings: Settings,
    days: int = 14,
    limit: int = 200,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, int(days)))
    _ensure_default_client(db)
    clients = list(db.scalars(select(Stage11Client).where(Stage11Client.is_active.is_(True)).order_by(Stage11Client.id.asc())))
    policy_version = "stage11_v1"
    stage10 = build_stage10_final_report(db, settings=settings, days=365, limit=5000, event_target=100)
    data_sufficient = str(stage10.get("final_decision") or "WARN").upper() in {"PASS", "WARN"}
    provider = build_provider_reliability_report(db, days=7)
    degraded = bool((provider.get("summary") or {}).get("has_provider_with_gt25pct_failures", False))

    rows = list(
        db.scalars(select(Stage8Decision).where(Stage8Decision.created_at >= cutoff).order_by(Stage8Decision.created_at.desc()).limit(limit))
    )
    created_orders = 0
    blocked = 0
    shadow_skipped = 0
    reasons_count: dict[str, int] = {}
    clients_breakdown: list[dict[str, Any]] = []

    for client in clients:
        client_created = 0
        client_blocked = 0
        client_shadow_skipped = 0
        custody_allowed = str(client.custody_mode or "").upper() in _allowed_custody_modes(settings)
        max_open_exposure = float((client.risk_profile or {}).get("max_open_exposure_usd", 300.0))

        for row in rows:
            if str(row.execution_action or "").upper() != "EXECUTE_ALLOWED":
                shadow_skipped += 1
                client_shadow_skipped += 1
                continue
            signal = db.get(Signal, int(row.signal_id))
            market = db.get(Market, int(signal.market_id)) if signal else None
            open_exposure = float(
                db.scalar(
                    select(func.coalesce(func.sum(Stage11ClientPosition.notional_usd), 0.0))
                    .where(Stage11ClientPosition.client_id == client.id)
                    .where(Stage11ClientPosition.status == "OPEN")
                )
                or 0.0
            )
            hard_reasons = _pretrade_hard_block_reasons(
                data_sufficient_for_acceptance=data_sufficient,
                market=market,
                now=now,
                rules_ambiguity_score=row.rules_ambiguity_score,
                edge_after_costs=row.edge_after_costs,
                min_edge_threshold=0.01,
                client_open_exposure_usd=open_exposure,
                max_open_exposure_usd=max_open_exposure,
                provider_health_degraded=degraded,
            )
            if not custody_allowed:
                hard_reasons.append("custody_mode_not_approved")
            if hard_reasons:
                blocked += 1
                client_blocked += 1
                for code in hard_reasons:
                    reasons_count[code] = reasons_count.get(code, 0) + 1
                append_audit_event(
                    db,
                    client_id=client.id,
                    order_id=None,
                    event_type="PRETRADE_BLOCK",
                    severity="WARN",
                    payload={"signal_id": int(row.signal_id), "reasons": hard_reasons},
                )
                continue

            mode = str(client.runtime_mode or "SHADOW").upper()
            ex = (signal.execution_analysis if signal and isinstance(signal.execution_analysis, dict) else {}) or {}
            notional = float(ex.get("position_size_usd") or settings.signal_execution_position_size_usd or 100.0)
            bucket = _size_bucket_from_notional(notional)
            side = "YES" if (str(signal.signal_direction or "YES").upper() != "NO") else "NO"
            create_or_reuse_order(
                db,
                settings=settings,
                client_id=int(client.id),
                signal_id=int(row.signal_id),
                market_id=int(signal.market_id if signal else row.signal_id),
                policy_version=policy_version,
                side=side,
                size_bucket=bucket,
                notional_usd=notional,
                requested_price=(market.probability_yes if market else None),
                runtime_mode=mode,
                unknown_recovery_sec=int(settings.stage11_max_unknown_recovery_sec),
            )
            created_orders += 1
            client_created += 1

        client_orders_window = int(
            db.scalar(
                select(func.count())
                .select_from(Stage11Order)
                .where(Stage11Order.client_id == client.id)
                .where(Stage11Order.created_at >= cutoff)
            )
            or 0
        )
        clients_breakdown.append(
            {
                "client_id": int(client.id),
                "client_code": str(client.code),
                "runtime_mode": str(client.runtime_mode),
                "custody_mode": str(client.custody_mode),
                "custody_mode_approved": custody_allowed,
                "orders_created_or_reused": client_created,
                "blocked_count": client_blocked,
                "shadow_skipped_count": client_shadow_skipped,
                "orders_total_in_window": client_orders_window,
            }
        )

    recon = reconcile_unknown_submits(
        db,
        settings=settings,
        max_unknown_recovery_sec=int(settings.stage11_max_unknown_recovery_sec),
    )
    db.commit()

    orders_total = int(
        db.scalar(
            select(func.count()).select_from(Stage11Order).where(Stage11Order.created_at >= cutoff)
        )
        or 0
    )
    unknown_total = int(
        db.scalar(
            select(func.count()).select_from(Stage11Order).where(Stage11Order.status == "UNKNOWN_SUBMIT")
        )
        or 0
    )
    live_orders = list(
        db.scalars(
            select(Stage11Order)
            .where(Stage11Order.created_at >= cutoff)
            .where(Stage11Order.status != "SHADOW_SKIPPED")
            .order_by(Stage11Order.created_at.desc())
        )
    )
    live_total = len(live_orders)
    success_total = sum(1 for o in live_orders if str(o.status).upper() in {"SUBMITTED", "FILLED", "CANCELLED_SAFE"})
    fills = list(
        db.scalars(select(Stage11Fill).where(Stage11Fill.filled_at >= cutoff).order_by(Stage11Fill.filled_at.desc()))
    )
    fills_by_order: dict[int, list[Stage11Fill]] = defaultdict(list)
    for f in fills:
        fills_by_order[int(f.order_id)].append(f)
    fill_stats: dict[int, dict[str, float]] = {}
    for oid, arr in fills_by_order.items():
        total_size = sum(max(0.0, float(x.fill_size_usd or 0.0)) for x in arr)
        total_pnl = sum(float(x.realized_pnl_usd or 0.0) for x in arr)
        total_fee = sum(float(x.fee_usd or 0.0) for x in arr)
        weighted_price_num = sum((float(x.fill_price or 0.0) * max(0.0, float(x.fill_size_usd or 0.0))) for x in arr)
        avg_price = (weighted_price_num / total_size) if total_size > 0 else 0.0
        fill_stats[oid] = {
            "total_size": total_size,
            "total_pnl": total_pnl,
            "total_fee": total_fee,
            "avg_price": avg_price,
        }
    order_ids = [int(o.id) for o in live_orders]
    audit_rows = (
        list(
            db.scalars(
                select(Stage11TradingAuditEvent)
                .where(Stage11TradingAuditEvent.order_id.in_(order_ids))
                .where(Stage11TradingAuditEvent.event_type.in_(["ORDER_SUBMITTED", "ORDER_INTENT_CREATED"]))
                .order_by(Stage11TradingAuditEvent.created_at.asc())
            )
        )
        if order_ids
        else []
    )
    submitted_ts_map: dict[int, datetime] = {}
    for ev in audit_rows:
        oid = int(ev.order_id or 0)
        if oid <= 0:
            continue
        if oid not in submitted_ts_map:
            submitted_ts_map[oid] = ev.created_at
    latencies_ms: list[float] = []
    slippage_pct_values: list[float] = []
    pnl_values: list[float] = []
    notional_values: list[float] = []
    for o in live_orders:
        oid = int(o.id)
        stats = fill_stats.get(oid)
        order_fills = fills_by_order.get(oid) or []
        if stats and order_fills:
            fill_end_ts = max(x.filled_at for x in order_fills if x.filled_at is not None)
            submit_ts = submitted_ts_map.get(oid) or o.created_at
            fill_end_ts = _as_utc_dt(fill_end_ts)
            submit_ts = _as_utc_dt(submit_ts)
            if submit_ts and fill_end_ts:
                latencies_ms.append(max(0.0, (fill_end_ts - submit_ts).total_seconds() * 1000.0))
            if o.requested_price is not None and float(o.requested_price) > 0 and float(stats.get("avg_price") or 0.0) > 0.0:
                slippage_pct_values.append((float(stats.get("avg_price") or 0.0) - float(o.requested_price)) / float(o.requested_price))
            pnl_values.append(float(stats.get("total_pnl") or 0.0))
            notional_values.append(max(0.0, float(stats.get("total_size") or 0.0)))
    reconciliation_completeness = 1.0 - (unknown_total / live_total) if live_total > 0 else 1.0
    realized_post_cost_return = (
        (sum(pnl_values) / sum(notional_values)) if sum(notional_values) > 0 else 0.0
    )
    # precision@K on executed trades by notional size proxy.
    executed_ranked = sorted(
        [fill_stats.get(int(o.id), {}) for o in live_orders if int(o.id) in fill_stats],
        key=lambda x: float(x.get("total_size") or 0.0),
        reverse=True,
    )
    topk = executed_ranked[:10]
    precision_at_10 = (
        sum(1 for x in topk if float(x.get("total_pnl") or 0.0) > 0.0) / len(topk)
        if topk
        else 0.0
    )
    slippage_drift_mean_pct = (sum(slippage_pct_values) / len(slippage_pct_values)) if slippage_pct_values else 0.0

    return {
        "generated_at": now.isoformat(),
        "summary": {
            "clients_processed": len(clients_breakdown),
            "signals_considered": len(rows),
            "orders_created_or_reused": created_orders,
            "orders_total_in_window": orders_total,
            "blocked_count": blocked,
            "shadow_skipped_count": shadow_skipped,
            "unknown_submit_open": unknown_total,
            "provider_health_degraded": degraded,
            "custody_mode_approved": all(bool(x.get("custody_mode_approved")) for x in clients_breakdown),
            "order_placement_success_rate": (success_total / live_total) if live_total else 1.0,
            "p95_execution_latency_ms": _p95(latencies_ms),
            "reconciliation_completeness": reconciliation_completeness,
            "realized_post_cost_return": realized_post_cost_return,
            "precision_at_10_executed": precision_at_10,
            "slippage_drift_mean_pct": slippage_drift_mean_pct,
        },
        "clients": clients_breakdown,
        "pretrade_block_reasons": reasons_count,
        "reconciliation": recon,
    }


def build_stage11_risk_report(db: Session, *, settings: Settings, days: int = 14) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff_day = now - timedelta(days=1)
    cutoff_week = now - timedelta(days=7)
    clients = list(db.scalars(select(Stage11Client).where(Stage11Client.is_active.is_(True)).order_by(Stage11Client.id.asc())))
    rows: list[dict[str, Any]] = []
    for client in clients:
        start_cap = float((client.risk_profile or {}).get("starting_capital_usd", 1000.0))
        day_pnl = float(
            db.scalar(
                select(func.coalesce(func.sum(Stage11Fill.realized_pnl_usd), 0.0))
                .where(Stage11Fill.client_id == client.id)
                .where(Stage11Fill.filled_at >= cutoff_day)
            )
            or 0.0
        )
        week_pnl = float(
            db.scalar(
                select(func.coalesce(func.sum(Stage11Fill.realized_pnl_usd), 0.0))
                .where(Stage11Fill.client_id == client.id)
                .where(Stage11Fill.filled_at >= cutoff_week)
            )
            or 0.0
        )
        day_drawdown_pct = (day_pnl / start_cap) * 100.0 if start_cap > 0 else 0.0
        week_drawdown_pct = (week_pnl / start_cap) * 100.0 if start_cap > 0 else 0.0

        recent_fills = list(
            db.scalars(
                select(Stage11Fill)
                .where(Stage11Fill.client_id == client.id)
                .order_by(Stage11Fill.filled_at.desc())
                .limit(30)
            )
        )
        consecutive_losses = 0
        for f in recent_fills:
            if float(f.realized_pnl_usd or 0.0) < 0.0:
                consecutive_losses += 1
            else:
                break

        one_hour_ago = now - timedelta(hours=1)
        orders_1h = int(
            db.scalar(
                select(func.count()).select_from(Stage11Order).where(Stage11Order.client_id == client.id).where(Stage11Order.created_at >= one_hour_ago)
            )
            or 0
        )
        errors_1h = int(
            db.scalar(
                select(func.count()).select_from(Stage11Order).where(Stage11Order.client_id == client.id).where(Stage11Order.created_at >= one_hour_ago).where(Stage11Order.status.in_(["FAILED", "UNKNOWN_SUBMIT"]))
            )
            or 0
        )
        error_rate_1h = (errors_1h / orders_1h) if orders_1h > 0 else 0.0
        recon_gap = float(
            db.scalar(
                select(func.coalesce(func.sum(Stage11Order.notional_usd), 0.0))
                .where(Stage11Order.client_id == client.id)
                .where(Stage11Order.status == "UNKNOWN_SUBMIT")
            )
            or 0.0
        )
        level = resolve_circuit_breaker_level(
            Stage11RiskInput(
                daily_drawdown_pct=day_drawdown_pct,
                weekly_drawdown_pct=week_drawdown_pct,
                consecutive_losses=consecutive_losses,
                execution_error_rate_1h=error_rate_1h,
                reconciliation_gap_usd=recon_gap,
            ),
            soft_daily_drawdown_pct=float(settings.stage11_soft_daily_drawdown_pct),
            soft_consecutive_losses=int(settings.stage11_soft_consecutive_losses),
            hard_daily_drawdown_pct=float(settings.stage11_hard_daily_drawdown_pct),
            hard_weekly_drawdown_pct=float(settings.stage11_hard_weekly_drawdown_pct),
            hard_consecutive_losses=int(settings.stage11_hard_consecutive_losses),
            panic_daily_drawdown_pct=float(settings.stage11_panic_daily_drawdown_pct),
            panic_execution_error_rate_1h=float(settings.stage11_panic_execution_error_rate_1h),
            panic_reconciliation_gap_usd=float(settings.stage11_panic_reconciliation_gap_usd),
        )
        if level in {"PANIC", "HARD"} and str(client.runtime_mode or "").upper() != "SHADOW":
            client.runtime_mode = "SHADOW"
            client.updated_at = now
            append_audit_event(
                db,
                client_id=client.id,
                order_id=None,
                event_type="AUTO_ROLLBACK_TO_SHADOW",
                severity="WARN",
                payload={"circuit_breaker_level": level},
            )
        rows.append(
            {
                "client_id": int(client.id),
                "client_code": str(client.code),
                "runtime_mode": str(client.runtime_mode),
                "daily_drawdown_pct": round(day_drawdown_pct, 6),
                "weekly_drawdown_pct": round(week_drawdown_pct, 6),
                "consecutive_losses": int(consecutive_losses),
                "execution_error_rate_1h": round(error_rate_1h, 6),
                "reconciliation_gap_usd": round(recon_gap, 6),
                "circuit_breaker_level": level,
            }
        )
    db.commit()
    severity_rank = {"OK": 0, "SOFT": 1, "HARD": 2, "PANIC": 3}
    global_level = "OK"
    for row in rows:
        if severity_rank.get(row["circuit_breaker_level"], 0) > severity_rank.get(global_level, 0):
            global_level = row["circuit_breaker_level"]
    return {
        "generated_at": now.isoformat(),
        "summary": {
            "clients_active": len(rows),
            "global_circuit_breaker_level": global_level,
        },
        "rows": rows,
    }


def build_stage11_client_report(db: Session, *, days: int = 7) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, int(days)))
    clients = list(db.scalars(select(Stage11Client).where(Stage11Client.is_active.is_(True)).order_by(Stage11Client.id.asc())))
    rows: list[dict[str, Any]] = []
    trade_details: list[dict[str, Any]] = []
    for client in clients:
        orders = int(
            db.scalar(
                select(func.count()).select_from(Stage11Order).where(Stage11Order.client_id == client.id).where(Stage11Order.created_at >= cutoff)
            )
            or 0
        )
        fills = int(
            db.scalar(
                select(func.count()).select_from(Stage11Fill).where(Stage11Fill.client_id == client.id).where(Stage11Fill.filled_at >= cutoff)
            )
            or 0
        )
        realized = float(
            db.scalar(
                select(func.coalesce(func.sum(Stage11Fill.realized_pnl_usd), 0.0))
                .where(Stage11Fill.client_id == client.id)
                .where(Stage11Fill.filled_at >= cutoff)
            )
            or 0.0
        )
        rows.append(
            {
                "client_id": int(client.id),
                "client_code": str(client.code),
                "runtime_mode": str(client.runtime_mode),
                "orders": orders,
                "fills": fills,
                "realized_pnl_usd": round(realized, 6),
            }
        )
        recent_orders = list(
            db.scalars(
                select(Stage11Order)
                .where(Stage11Order.client_id == client.id)
                .where(Stage11Order.created_at >= cutoff)
                .order_by(Stage11Order.created_at.desc())
                .limit(25)
            )
        )
        for order in recent_orders:
            events = list(
                db.scalars(
                    select(Stage11TradingAuditEvent)
                    .where(Stage11TradingAuditEvent.order_id == order.id)
                    .order_by(Stage11TradingAuditEvent.created_at.asc())
                )
            )
            event_types = [str(e.event_type) for e in events]
            trade_details.append(
                {
                    "client_id": int(client.id),
                    "client_code": str(client.code),
                    "order_id": int(order.id),
                    "signal_id": int(order.signal_id) if order.signal_id is not None else None,
                    "market_id": int(order.market_id),
                    "status": str(order.status),
                    "side": str(order.side),
                    "size_bucket": str(order.size_bucket),
                    "notional_usd": float(order.notional_usd or 0.0),
                    "requested_price": float(order.requested_price) if order.requested_price is not None else None,
                    "last_error": str(order.last_error) if order.last_error else None,
                    "event_types": event_types,
                    "audit_event_count": len(event_types),
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                }
            )
    return {
        "generated_at": now.isoformat(),
        "window_days": int(days),
        "rows": rows,
        "trade_details": trade_details,
        "summary": {
            "clients": len(rows),
            "orders_total": sum(int(r["orders"]) for r in rows),
            "fills_total": sum(int(r["fills"]) for r in rows),
            "realized_pnl_usd_total": round(sum(float(r["realized_pnl_usd"]) for r in rows), 6),
            "trade_details_total": len(trade_details),
        },
    }


def build_stage11_track_report(
    db: Session,
    *,
    settings: Settings,
    days_execution: int = 14,
    days_client: int = 7,
) -> dict[str, Any]:
    execution = build_stage11_execution_report(db, settings=settings, days=days_execution, limit=400)
    risk = build_stage11_risk_report(db, settings=settings, days=days_execution)
    client_report = build_stage11_client_report(db, days=days_client)
    stage10 = build_stage10_final_report(db, settings=settings, days=365, limit=5000, event_target=100)

    exec_summary = execution.get("summary") or {}
    risk_summary = risk.get("summary") or {}
    client_summary = client_report.get("summary") or {}
    stage10_summary = stage10.get("summary") or {}

    now = datetime.now(UTC)
    execution_cutoff = now - timedelta(days=max(1, int(days_execution)))
    min_order_ts = db.scalar(select(func.min(Stage11Order.created_at)))
    shadow_days_observed = (
        max(0.0, (now - min_order_ts).total_seconds() / 86400.0) if isinstance(min_order_ts, datetime) else 0.0
    )
    orders_total = int(exec_summary.get("orders_total_in_window") or 0)
    fills_total = int(
        db.scalar(
            select(func.count()).select_from(Stage11Fill).where(Stage11Fill.filled_at >= execution_cutoff)
        )
        or 0
    )
    security_incidents = int(
        db.scalar(
            select(func.count())
            .select_from(Stage11TradingAuditEvent)
            .where(Stage11TradingAuditEvent.created_at >= execution_cutoff)
            .where(
                Stage11TradingAuditEvent.event_type.in_(
                    ["SECURITY_INCIDENT", "SECRETS_INCIDENT", "KEY_EXPOSURE", "SECRETS_LEAK"]
                )
            )
        )
        or 0
    )
    critical_incidents = int(
        db.scalar(
            select(func.count())
            .select_from(Stage11TradingAuditEvent)
            .where(Stage11TradingAuditEvent.created_at >= execution_cutoff)
            .where(Stage11TradingAuditEvent.severity == "CRITICAL")
        )
        or 0
    )
    distinct_orders_with_audit = int(
        db.scalar(
            select(func.count(func.distinct(Stage11TradingAuditEvent.order_id)))
            .where(Stage11TradingAuditEvent.order_id.is_not(None))
        )
        or 0
    )
    stage10_baseline = float(stage10_summary.get("post_cost_ev_mean_pct") or 0.0)
    active_clients = list(db.scalars(select(Stage11Client).where(Stage11Client.is_active.is_(True))))
    start_cap_total = sum(float((c.risk_profile or {}).get("starting_capital_usd", 1000.0)) for c in active_clients)
    realized_total = float(client_summary.get("realized_pnl_usd_total") or 0.0)
    realized_return_pct = (realized_total / start_cap_total) if start_cap_total > 0 else 0.0

    checks = {
        "custody_mode_approved": bool(exec_summary.get("custody_mode_approved")),
        "shadow_stable_14d": (
            shadow_days_observed >= float(settings.stage11_min_shadow_days)
            and str(risk_summary.get("global_circuit_breaker_level") or "OK") in {"OK", "SOFT"}
            and critical_incidents == 0
        ),
        "limited_execution_min_30d_or_100_trades": (
            shadow_days_observed >= float(settings.stage11_limited_min_days)
            or fills_total >= int(settings.stage11_limited_min_trades)
        ),
        "execution_error_rate_below_threshold": all(
            float(r.get("execution_error_rate_1h") or 0.0) < float(settings.stage11_panic_execution_error_rate_1h)
            for r in (risk.get("rows") or [])
        ),
        "reconciliation_completeness_ge_95pct": float(exec_summary.get("reconciliation_completeness") or 0.0) >= 0.95,
        "no_security_incident": security_incidents == 0,
        "realized_post_cost_return_not_below_stage10_baseline": (
            realized_return_pct >= (stage10_baseline - float(settings.stage11_realized_return_tolerance_pct))
        ),
        "audit_trail_coverage_100pct": distinct_orders_with_audit >= orders_total,
    }
    limited_eligible = (
        bool(checks["custody_mode_approved"])
        and bool(checks["shadow_stable_14d"])
        and bool(checks["execution_error_rate_below_threshold"])
        and bool(checks["reconciliation_completeness_ge_95pct"])
        and bool(checks["no_security_incident"])
        and bool(checks["audit_trail_coverage_100pct"])
    )
    stage11_accepted = (
        limited_eligible
        and bool(checks["limited_execution_min_30d_or_100_trades"])
        and bool(checks["realized_post_cost_return_not_below_stage10_baseline"])
    )
    if stage11_accepted:
        decision = "GO"
        action = "stage11_complete_ready_for_stage12"
    elif limited_eligible:
        decision = "LIMITED_GO"
        action = "keep_shadow_or_move_to_limited_with_manual_approval"
    else:
        decision = "NO_GO"
        action = "keep_shadow_and_fix_stage11_checks"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "final_decision": decision,
        "recommended_action": action,
        "checks": checks,
        "failed_checks": [k for k, v in checks.items() if not bool(v)],
        "summary": {
            "orders_total": int(exec_summary.get("orders_total_in_window") or 0),
            "fills_total": fills_total,
            "blocked_count": int(exec_summary.get("blocked_count") or 0),
            "shadow_skipped_count": int(exec_summary.get("shadow_skipped_count") or 0),
            "global_circuit_breaker_level": str(risk_summary.get("global_circuit_breaker_level") or "OK"),
            "clients": int(client_summary.get("clients") or 0),
            "realized_pnl_usd_total": float(client_summary.get("realized_pnl_usd_total") or 0.0),
            "realized_return_pct": realized_return_pct,
            "stage10_baseline_post_cost_ev_mean_pct": stage10_baseline,
            "shadow_days_observed": shadow_days_observed,
            "security_incidents_14d": security_incidents,
            "critical_incidents_14d": critical_incidents,
            "order_placement_success_rate": float(exec_summary.get("order_placement_success_rate") or 0.0),
            "p95_execution_latency_ms": float(exec_summary.get("p95_execution_latency_ms") or 0.0),
            "reconciliation_completeness": float(exec_summary.get("reconciliation_completeness") or 0.0),
            "precision_at_10_executed": float(exec_summary.get("precision_at_10_executed") or 0.0),
            "slippage_drift_mean_pct": float(exec_summary.get("slippage_drift_mean_pct") or 0.0),
        },
        "sections": {
            "execution": execution,
            "risk": risk,
            "client_report": client_report,
            "stage10_final_report": stage10,
        },
    }
