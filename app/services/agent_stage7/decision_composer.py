from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


_DECISION_DOWNGRADE = {
    "KEEP": "MODIFY",
    "MODIFY": "REMOVE",
    "REMOVE": "REMOVE",
    "SKIP": "SKIP",
}


def _decision_confidence_adjustment(
    *,
    contradictions_count: int,
    ambiguity_count: int,
    internal_gate_passed: bool,
) -> float:
    if not internal_gate_passed:
        return -0.30
    penalty = (contradictions_count * 0.07) + (ambiguity_count * 0.04)
    return round(max(-0.30, -penalty), 4)


def _input_hash(
    signal_id: int,
    base_decision: str,
    gate: dict[str, Any],
    evidence_bundle: dict[str, Any],
    *,
    provider: str,
    model_id: str,
    model_version: str,
    prompt_template_version: str,
) -> str:
    # Keep hash stable across re-runs: exclude volatile timestamps from evidence.
    stable_evidence = {
        "internal_metrics_snapshot": dict(evidence_bundle.get("internal_metrics_snapshot") or {}),
        "external_consensus": dict(evidence_bundle.get("external_consensus") or {}),
        "contradictions": list(evidence_bundle.get("contradictions") or []),
        "resolution_ambiguity_flags": list(evidence_bundle.get("resolution_ambiguity_flags") or []),
    }
    payload = {
        "signal_id": signal_id,
        "base_decision": base_decision,
        "provider": str(provider or ""),
        "model_id": str(model_id or ""),
        "model_version": str(model_version or ""),
        "prompt_template_version": str(prompt_template_version or ""),
        "gate": gate,
        "evidence_bundle": stable_evidence,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def compose_stage7_decision(
    *,
    signal_id: int,
    base_decision: str,
    internal_gate: dict[str, Any],
    evidence_bundle: dict[str, Any],
    provider: str,
    model_id: str,
    model_version: str,
    prompt_template_version: str,
    provider_fingerprint: str | None = None,
) -> dict[str, Any]:
    base = str(base_decision or "SKIP").upper()
    if base not in {"KEEP", "MODIFY", "REMOVE", "SKIP"}:
        base = "SKIP"

    contradictions = list(evidence_bundle.get("contradictions") or [])
    ambiguity = list(evidence_bundle.get("resolution_ambiguity_flags") or [])
    reason_codes: list[str] = []

    if not bool(internal_gate.get("passed")):
        decision = "SKIP"
        reason_codes.extend(list(internal_gate.get("reasons") or []))
        reason_codes.append("stage7_internal_gate_failed")
    else:
        decision = base
        if contradictions:
            decision = _DECISION_DOWNGRADE.get(decision, decision)
            reason_codes.append("stage7_cross_source_contradiction")
        if ambiguity:
            decision = _DECISION_DOWNGRADE.get(decision, decision)
            reason_codes.append("stage7_resolution_ambiguity")
        if decision == base:
            reason_codes.append("stage7_no_override")

    confidence_adjustment = _decision_confidence_adjustment(
        contradictions_count=len(contradictions),
        ambiguity_count=len(ambiguity),
        internal_gate_passed=bool(internal_gate.get("passed")),
    )
    decision_hash = _input_hash(
        signal_id,
        base,
        internal_gate,
        evidence_bundle,
        provider=provider,
        model_id=model_id,
        model_version=model_version,
        prompt_template_version=prompt_template_version,
    )

    return {
        "signal_id": signal_id,
        "base_decision": base,
        "decision": decision,
        "confidence_adjustment": confidence_adjustment,
        "reason_codes": reason_codes,
        "evidence_bundle": evidence_bundle,
        "input_hash": decision_hash,
        "model_id": model_id,
        "model_version": model_version,
        "prompt_template_version": prompt_template_version,
        "provider": provider,
        "provider_fingerprint": provider_fingerprint or "",
    }
