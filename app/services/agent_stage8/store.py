from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Stage8Decision


def get_latest_stage8_decision(db: Session, *, signal_id: int) -> dict[str, Any] | None:
    row = db.scalar(
        select(Stage8Decision)
        .where(Stage8Decision.signal_id == signal_id)
        .order_by(Stage8Decision.id.desc())
        .limit(1)
    )
    if not row:
        return None
    return {
        "signal_id": row.signal_id,
        "stage7_decision_id": row.stage7_decision_id,
        "category": row.category,
        "category_confidence": float(row.category_confidence or 0.0),
        "policy_version": row.policy_version,
        "rules_ambiguity_score": float(row.rules_ambiguity_score or 0.0),
        "resolution_source_confidence": float(row.resolution_source_confidence or 0.0),
        "dispute_risk_flag": bool(row.dispute_risk_flag),
        "edge_after_costs": float(row.edge_after_costs or 0.0),
        "base_decision": row.base_decision,
        "decision": row.decision,
        "execution_action": row.execution_action,
        "reason_codes": list(row.reason_codes or []),
        "hard_block_reason": row.hard_block_reason,
        "evidence_bundle": dict(row.evidence_bundle or {}),
        "kelly_fraction": float(row.kelly_fraction or 0.0),
        "pnl_proxy_usd_100": float(row.pnl_proxy_usd_100 or 0.0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def load_stage8_today_map(
    db: Session,
    *,
    signal_ids: list[int],
    policy_version: str,
) -> dict[int, Stage8Decision]:
    if not signal_ids:
        return {}
    today_utc = datetime.now(UTC).date()
    rows = list(
        db.scalars(
            select(Stage8Decision)
            .where(Stage8Decision.signal_id.in_(signal_ids))
            .where(Stage8Decision.policy_version == policy_version)
            .order_by(Stage8Decision.id.asc())
        )
    )
    out: dict[int, Stage8Decision] = {}
    for row in rows:
        if row.created_at and row.created_at.astimezone(UTC).date() == today_utc:
            out[int(row.signal_id)] = row
    return out


def save_stage8_decision(
    db: Session,
    *,
    payload: dict[str, Any],
    existing_row: Stage8Decision | None = None,
) -> dict[str, Any]:
    signal_id = int(payload.get("signal_id") or 0)
    policy_version = str(payload.get("policy_version") or "stage8_bootstrap_v1")
    row = existing_row
    if row is None:
        row = db.scalar(
            select(Stage8Decision)
            .where(Stage8Decision.signal_id == signal_id)
            .where(Stage8Decision.policy_version == policy_version)
            .order_by(Stage8Decision.id.desc())
            .limit(1)
        )
        today_utc = datetime.now(UTC).date()
        if not row or not row.created_at or row.created_at.astimezone(UTC).date() != today_utc:
            row = Stage8Decision(signal_id=signal_id, policy_version=policy_version)
            db.add(row)

    row.stage7_decision_id = payload.get("stage7_decision_id")
    row.category = str(payload.get("category") or "other")
    row.category_confidence = float(payload.get("category_confidence") or 0.0)
    row.rules_ambiguity_score = float(payload.get("rules_ambiguity_score") or 0.0)
    row.resolution_source_confidence = float(payload.get("resolution_source_confidence") or 0.0)
    row.dispute_risk_flag = bool(payload.get("dispute_risk_flag") or False)
    row.edge_after_costs = float(payload.get("edge_after_costs") or 0.0)
    row.base_decision = str(payload.get("base_decision") or "SKIP")
    row.decision = str(payload.get("decision") or "SKIP")
    row.execution_action = str(payload.get("execution_action") or "BLOCK")
    row.reason_codes = list(payload.get("reason_codes") or [])
    row.hard_block_reason = payload.get("hard_block_reason")
    row.evidence_bundle = dict(payload.get("evidence_bundle") or {})
    row.kelly_fraction = float(payload.get("kelly_fraction") or 0.0)
    row.pnl_proxy_usd_100 = float(payload.get("pnl_proxy_usd_100") or 0.0)
    db.flush()
    return payload
