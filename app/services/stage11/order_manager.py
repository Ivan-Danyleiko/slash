from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Stage11ClientPosition, Stage11Fill, Stage11Order, Stage11TradingAuditEvent
from app.services.stage11.execution_router import get_stage11_venue_adapter
from app.services.stage11.idempotency import stage11_idempotency_key
from app.services.stage11.venues.base import Stage11PlaceRequest


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _checksum(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def append_audit_event(
    db: Session,
    *,
    client_id: int,
    order_id: int | None,
    event_type: str,
    severity: str = "INFO",
    payload: dict[str, Any] | None = None,
) -> Stage11TradingAuditEvent:
    payload_dict = dict(payload or {})
    ev = Stage11TradingAuditEvent(
        client_id=int(client_id),
        order_id=order_id,
        event_type=str(event_type),
        severity=str(severity).upper(),
        payload_json=payload_dict,
        payload_checksum=_checksum(payload_dict),
    )
    db.add(ev)
    return ev


def _ensure_fill_and_position(
    db: Session,
    *,
    order: Stage11Order,
    fill_price: float | None,
    fill_size_usd: float | None,
    fee_usd: float | None,
    realized_pnl_usd: float | None = None,
) -> None:
    existing_total = float(
        db.scalar(
            select(func.coalesce(func.sum(Stage11Fill.fill_size_usd), 0.0)).where(Stage11Fill.order_id == order.id)
        )
        or 0.0
    )
    target_total = float(fill_size_usd if fill_size_usd is not None else (order.notional_usd or 0.0))
    if target_total <= 0.0:
        target_total = float(order.notional_usd or 0.0)
    delta_fill = max(0.0, target_total - existing_total)
    if delta_fill > 0.0:
        fill = Stage11Fill(
            client_id=int(order.client_id),
            order_id=int(order.id),
            market_id=int(order.market_id),
            fill_price=fill_price,
            fill_size_usd=float(delta_fill),
            fee_usd=float(fee_usd or 0.0),
            realized_pnl_usd=float(realized_pnl_usd or 0.0),
            fill_payload={"from_order_manager": True, "cumulative_target_size_usd": target_total},
        )
        db.add(fill)

    position = db.scalar(
        select(Stage11ClientPosition)
        .where(Stage11ClientPosition.client_id == order.client_id)
        .where(Stage11ClientPosition.market_id == order.market_id)
        .where(Stage11ClientPosition.status == "OPEN")
        .limit(1)
    )
    if position is None:
        position = Stage11ClientPosition(
            client_id=int(order.client_id),
            market_id=int(order.market_id),
            side=str(order.side),
            status="OPEN",
            notional_usd=float(fill_size_usd or order.notional_usd or 0.0),
            avg_entry_price=fill_price,
            mark_price=fill_price,
            unrealized_pnl_usd=0.0,
            realized_pnl_usd=float(realized_pnl_usd or 0.0),
            updated_at=datetime.now(UTC),
        )
        db.add(position)
        return

    prior_notional = float(position.notional_usd or 0.0)
    new_notional = float(delta_fill)
    total_notional = prior_notional + new_notional
    if total_notional > 0 and fill_price is not None:
        prior_price = float(position.avg_entry_price or fill_price)
        position.avg_entry_price = ((prior_notional * prior_price) + (new_notional * float(fill_price))) / total_notional
    position.notional_usd = total_notional
    position.mark_price = float(fill_price) if fill_price is not None else position.mark_price
    position.realized_pnl_usd = float(position.realized_pnl_usd or 0.0) + float(realized_pnl_usd or 0.0)
    position.updated_at = datetime.now(UTC)


def create_or_reuse_order(
    db: Session,
    *,
    settings: Settings,
    client_id: int,
    signal_id: int | None,
    market_id: int,
    policy_version: str,
    side: str,
    size_bucket: str,
    notional_usd: float,
    requested_price: float | None,
    runtime_mode: str,
    unknown_recovery_sec: int = 120,
) -> Stage11Order:
    idem_key = stage11_idempotency_key(
        client_id=client_id,
        signal_id=signal_id,
        policy_version=policy_version,
        side=side,
        size_bucket=size_bucket,
    )
    existing = db.scalar(select(Stage11Order).where(Stage11Order.idempotency_key == idem_key).limit(1))
    if existing is not None:
        append_audit_event(
            db,
            client_id=client_id,
            order_id=existing.id,
            event_type="IDEMPOTENCY_HIT",
            payload={"idempotency_key": idem_key, "status": existing.status},
        )
        return existing

    mode = str(runtime_mode or "SHADOW").upper()
    status = "SHADOW_SKIPPED" if mode == "SHADOW" else "CREATED"
    order = Stage11Order(
        client_id=int(client_id),
        signal_id=int(signal_id) if signal_id else None,
        market_id=int(market_id),
        side=str(side).upper(),
        size_bucket=str(size_bucket).lower(),
        notional_usd=float(notional_usd),
        requested_price=requested_price,
        idempotency_key=idem_key,
        policy_version=str(policy_version),
        status=status,
        submit_attempts=0,
        order_payload={
            "runtime_mode": mode,
            "signal_id": int(signal_id) if signal_id else None,
            "market_id": int(market_id),
            "side": str(side).upper(),
            "size_bucket": str(size_bucket).lower(),
            "notional_usd": float(notional_usd),
            "requested_price": requested_price,
        },
    )
    db.add(order)
    db.flush()
    append_audit_event(
        db,
        client_id=client_id,
        order_id=order.id,
        event_type="ORDER_INTENT_CREATED",
        payload={"idempotency_key": idem_key, "status": status},
    )

    if mode != "SHADOW":
        adapter = get_stage11_venue_adapter(settings=settings)
        place = adapter.place_order(
            Stage11PlaceRequest(
                client_id=int(client_id),
                order_id=int(order.id),
                market_id=int(market_id),
                side=str(side).upper(),
                notional_usd=float(notional_usd),
                requested_price=requested_price,
                idempotency_key=idem_key,
            )
        )
        order.submit_attempts = int(order.submit_attempts or 0) + 1
        order.venue_order_id = place.venue_order_id
        order.response_payload = dict(place.response_payload or {})
        order.updated_at = datetime.now(UTC)
        if place.status == "SUBMITTED":
            order.status = "SUBMITTED"
            order.unknown_recovery_deadline = datetime.now(UTC) + timedelta(
                seconds=max(30, int(unknown_recovery_sec))
            )
            append_audit_event(
                db,
                client_id=client_id,
                order_id=order.id,
                event_type="ORDER_SUBMITTED",
                payload={
                    "venue_order_id": order.venue_order_id,
                    "response": order.response_payload,
                    "recovery_deadline": order.unknown_recovery_deadline.isoformat(),
                },
            )
        elif place.status == "FAILED":
            order.status = "FAILED"
            order.last_error = str(place.error or "submit_failed")
            append_audit_event(
                db,
                client_id=client_id,
                order_id=order.id,
                event_type="ORDER_SUBMIT_FAILED",
                severity="WARN",
                payload={"error": order.last_error, "response": order.response_payload},
            )
        else:
            order.status = "UNKNOWN_SUBMIT"
            order.last_error = str(place.error or "unknown_submit")
            order.unknown_recovery_deadline = datetime.now(UTC) + timedelta(
                seconds=max(30, int(unknown_recovery_sec))
            )
            append_audit_event(
                db,
                client_id=client_id,
                order_id=order.id,
                event_type="UNKNOWN_SUBMIT",
                severity="WARN",
                payload={
                    "recovery_deadline": order.unknown_recovery_deadline.isoformat(),
                    "error": order.last_error,
                    "response": order.response_payload,
                },
            )
    return order


def reconcile_orders(
    db: Session,
    *,
    settings: Settings,
    max_unknown_recovery_sec: int = 120,
) -> dict[str, int]:
    now = datetime.now(UTC)
    adapter = get_stage11_venue_adapter(settings=settings)
    rows = list(
        db.scalars(
            select(Stage11Order)
            .where(Stage11Order.status.in_(["UNKNOWN_SUBMIT", "SUBMITTED"]))
            .order_by(Stage11Order.created_at.asc())
        )
    )
    recovered = 0
    still_unknown = 0
    safe_cancelled = 0
    filled = 0
    for row in rows:
        created_at = _as_utc(row.created_at) or now
        deadline = _as_utc(row.unknown_recovery_deadline) or (
            created_at + timedelta(seconds=max_unknown_recovery_sec)
        )
        status = None
        if row.venue_order_id:
            status = adapter.fetch_order_status(str(row.venue_order_id))
        if status and status.status == "FILLED":
            row.status = "FILLED"
            row.updated_at = now
            row.response_payload = dict(status.response_payload or {})
            _ensure_fill_and_position(
                db,
                order=row,
                fill_price=status.fill_price,
                fill_size_usd=status.fill_size_usd,
                fee_usd=status.fee_usd,
                realized_pnl_usd=0.0,
            )
            append_audit_event(
                db,
                client_id=int(row.client_id),
                order_id=int(row.id),
                event_type="ORDER_RECOVERED_FILLED",
                payload={"response": row.response_payload},
            )
            recovered += 1
            filled += 1
            continue
        if status and status.status == "CANCELLED_SAFE":
            row.status = "CANCELLED_SAFE"
            row.updated_at = now
            row.response_payload = dict(status.response_payload or {})
            append_audit_event(
                db,
                client_id=int(row.client_id),
                order_id=int(row.id),
                event_type="ORDER_RECOVERED_CANCELLED",
                severity="WARN",
                payload={"response": row.response_payload},
            )
            recovered += 1
            continue
        if status and status.status == "SUBMITTED":
            row.status = "SUBMITTED"
            row.updated_at = now
            row.response_payload = dict(status.response_payload or {})
            if status.fill_size_usd is not None and float(status.fill_size_usd) > 0.0:
                _ensure_fill_and_position(
                    db,
                    order=row,
                    fill_price=status.fill_price,
                    fill_size_usd=status.fill_size_usd,
                    fee_usd=status.fee_usd,
                    realized_pnl_usd=0.0,
                )
                append_audit_event(
                    db,
                    client_id=int(row.client_id),
                    order_id=int(row.id),
                    event_type="ORDER_PARTIAL_FILL",
                    payload={
                        "is_partial": bool(status.is_partial),
                        "fill_size_usd": float(status.fill_size_usd),
                        "response": row.response_payload,
                    },
                )
        if deadline > now:
            still_unknown += 1
            continue
        if row.venue_order_id:
            cancel = adapter.cancel_order(str(row.venue_order_id))
            row.response_payload = dict(cancel.response_payload or {})
        row.status = "CANCELLED_SAFE"
        row.updated_at = now
        append_audit_event(
            db,
            client_id=int(row.client_id),
            order_id=int(row.id),
            event_type="SAFE_CANCEL_AFTER_UNKNOWN_TIMEOUT",
            severity="WARN",
            payload={"max_unknown_recovery_sec": int(max_unknown_recovery_sec), "response": row.response_payload},
        )
        safe_cancelled += 1
    return {
        "recovered": recovered,
        "filled": filled,
        "safe_cancelled": safe_cancelled,
        "still_unknown": still_unknown,
        "unknown_total": len(rows),
    }


def reconcile_unknown_submits(
    db: Session,
    *,
    settings: Settings,
    max_unknown_recovery_sec: int = 120,
) -> dict[str, int]:
    # Backward-compatible alias used in existing calls/tests.
    return reconcile_orders(
        db,
        settings=settings,
        max_unknown_recovery_sec=max_unknown_recovery_sec,
    )


def get_order_detail(db: Session, *, order_id: int) -> dict[str, Any] | None:
    order = db.get(Stage11Order, int(order_id))
    if order is None:
        return None
    fills = list(
        db.scalars(
            select(Stage11Fill).where(Stage11Fill.order_id == order.id).order_by(Stage11Fill.filled_at.asc())
        )
    )
    events = list(
        db.scalars(
            select(Stage11TradingAuditEvent)
            .where(Stage11TradingAuditEvent.order_id == order.id)
            .order_by(Stage11TradingAuditEvent.created_at.asc())
        )
    )
    return {
        "order": {
            "id": int(order.id),
            "client_id": int(order.client_id),
            "signal_id": int(order.signal_id) if order.signal_id is not None else None,
            "market_id": int(order.market_id),
            "platform": str(order.platform),
            "side": str(order.side),
            "size_bucket": str(order.size_bucket),
            "notional_usd": float(order.notional_usd or 0.0),
            "requested_price": float(order.requested_price) if order.requested_price is not None else None,
            "status": str(order.status),
            "venue_order_id": str(order.venue_order_id) if order.venue_order_id else None,
            "submit_attempts": int(order.submit_attempts or 0),
            "idempotency_key": str(order.idempotency_key),
            "policy_version": str(order.policy_version),
            "last_error": str(order.last_error) if order.last_error else None,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        },
        "fills": [
            {
                "id": int(f.id),
                "fill_price": float(f.fill_price) if f.fill_price is not None else None,
                "fill_size_usd": float(f.fill_size_usd or 0.0),
                "fee_usd": float(f.fee_usd or 0.0),
                "realized_pnl_usd": float(f.realized_pnl_usd or 0.0),
                "filled_at": f.filled_at.isoformat() if f.filled_at else None,
            }
            for f in fills
        ],
        "audit_events": [
            {
                "id": int(e.id),
                "event_type": str(e.event_type),
                "severity": str(e.severity),
                "payload_json": dict(e.payload_json or {}),
                "payload_checksum": str(e.payload_checksum or ""),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


def refresh_order_status(db: Session, *, settings: Settings, order_id: int) -> dict[str, Any]:
    order = db.get(Stage11Order, int(order_id))
    if order is None:
        return {"status": "error", "error": "order_not_found"}
    if not order.venue_order_id:
        return {"status": "ok", "result": {"order_id": int(order.id), "status": str(order.status), "note": "no_venue_order_id"}}
    adapter = get_stage11_venue_adapter(settings=settings)
    status = adapter.fetch_order_status(str(order.venue_order_id))
    order.response_payload = dict(status.response_payload or {})
    if status.status == "FILLED":
        order.status = "FILLED"
        _ensure_fill_and_position(
            db,
            order=order,
            fill_price=status.fill_price,
            fill_size_usd=status.fill_size_usd,
            fee_usd=status.fee_usd,
            realized_pnl_usd=0.0,
        )
        append_audit_event(
            db,
            client_id=int(order.client_id),
            order_id=int(order.id),
            event_type="ORDER_STATUS_FILLED",
            payload={"response": order.response_payload},
        )
    elif status.status == "CANCELLED_SAFE":
        order.status = "CANCELLED_SAFE"
        append_audit_event(
            db,
            client_id=int(order.client_id),
            order_id=int(order.id),
            event_type="ORDER_STATUS_CANCELLED",
            severity="WARN",
            payload={"response": order.response_payload},
        )
    elif status.status == "SUBMITTED":
        order.status = "SUBMITTED"
    elif status.status == "UNKNOWN_SUBMIT":
        order.status = "UNKNOWN_SUBMIT"
        order.last_error = str(status.error or "unknown_status")
    order.updated_at = datetime.now(UTC)
    db.commit()
    return {"status": "ok", "result": get_order_detail(db, order_id=int(order.id))}


def cancel_order_by_id(db: Session, *, settings: Settings, order_id: int, reason: str = "manual_cancel") -> dict[str, Any]:
    order = db.get(Stage11Order, int(order_id))
    if order is None:
        return {"status": "error", "error": "order_not_found"}
    if str(order.status or "").upper() in {"FILLED", "CANCELLED_SAFE", "FAILED"}:
        return {"status": "ok", "result": {"order_id": int(order.id), "status": str(order.status), "note": "already_terminal"}}
    if not order.venue_order_id:
        order.status = "CANCELLED_SAFE"
        order.updated_at = datetime.now(UTC)
        append_audit_event(
            db,
            client_id=int(order.client_id),
            order_id=int(order.id),
            event_type="MANUAL_CANCEL_NO_VENUE_ID",
            severity="WARN",
            payload={"reason": reason},
        )
        db.commit()
        return {"status": "ok", "result": get_order_detail(db, order_id=int(order.id))}
    adapter = get_stage11_venue_adapter(settings=settings)
    cancel = adapter.cancel_order(str(order.venue_order_id))
    order.response_payload = dict(cancel.response_payload or {})
    if cancel.status == "CANCELLED_SAFE":
        order.status = "CANCELLED_SAFE"
    else:
        order.status = "UNKNOWN_SUBMIT"
        order.last_error = str(cancel.error or "cancel_failed")
    order.updated_at = datetime.now(UTC)
    append_audit_event(
        db,
        client_id=int(order.client_id),
        order_id=int(order.id),
        event_type="MANUAL_CANCEL_REQUESTED",
        severity="WARN",
        payload={"reason": reason, "response": order.response_payload},
    )
    db.commit()
    return {"status": "ok", "result": get_order_detail(db, order_id=int(order.id))}
