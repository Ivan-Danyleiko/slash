from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Stage7AgentDecision


def get_cached_stage7_decision(db: Session, *, input_hash: str) -> dict[str, Any] | None:
    row = db.scalar(
        select(Stage7AgentDecision)
        .where(Stage7AgentDecision.input_hash == input_hash)
        .order_by(Stage7AgentDecision.id.desc())
        .limit(1)
    )
    if not row:
        return None
    return {
        "signal_id": row.signal_id,
        "base_decision": row.base_decision,
        "decision": row.decision,
        "confidence_adjustment": float(row.confidence_adjustment or 0.0),
        "reason_codes": list(row.reason_codes or []),
        "evidence_bundle": dict(row.evidence_bundle or {}),
        "input_hash": row.input_hash,
        "model_id": row.model_id or "",
        "model_version": row.model_version or "",
        "prompt_template_version": row.prompt_template_version or "",
        "provider": row.provider or "",
        "provider_fingerprint": row.provider_fingerprint or "",
        "tool_snapshot_version": row.tool_snapshot_version or "",
        "llm_cost_usd": float(row.llm_cost_usd or 0.0),
        "cache_hit": True,
    }


def save_stage7_decision(
    db: Session,
    *,
    payload: dict[str, Any],
    llm_cost_usd: float,
    tool_snapshot_version: str | None = None,
) -> dict[str, Any]:
    resolved_tool_snapshot = str(
        tool_snapshot_version
        or payload.get("tool_snapshot_version")
        or "v1"
    )
    row = Stage7AgentDecision(
        signal_id=int(payload.get("signal_id") or 0),
        input_hash=str(payload.get("input_hash") or ""),
        base_decision=str(payload.get("base_decision") or "SKIP"),
        decision=str(payload.get("decision") or "SKIP"),
        confidence_adjustment=float(payload.get("confidence_adjustment") or 0.0),
        reason_codes=list(payload.get("reason_codes") or []),
        evidence_bundle=dict(payload.get("evidence_bundle") or {}),
        model_id=str(payload.get("model_id") or ""),
        model_version=str(payload.get("model_version") or ""),
        prompt_template_version=str(payload.get("prompt_template_version") or ""),
        provider=str(payload.get("provider") or ""),
        provider_fingerprint=str(payload.get("provider_fingerprint") or ""),
        tool_snapshot_version=resolved_tool_snapshot,
        llm_cost_usd=float(llm_cost_usd or 0.0),
    )
    db.add(row)
    db.commit()
    return {**payload, "llm_cost_usd": float(llm_cost_usd or 0.0), "cache_hit": False}
