from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DecisionGateResult:
    decision: str
    execution_action: str
    reason_codes: list[str]
    hard_block_reason: str | None


def map_execution_action(
    *,
    decision: str,
    hard_block: bool,
    soft_block: bool,
) -> tuple[str, str | None]:
    if decision in {"SKIP", "REMOVE"}:
        return ("BLOCK", "decision_blocked")
    if decision == "MODIFY":
        return ("SHADOW_ONLY", None)
    if hard_block:
        return ("BLOCK", "hard_gate_failed")
    if soft_block:
        return ("SHADOW_ONLY", None)
    return ("EXECUTE_ALLOWED", None)


def resolve_stage8_decision(
    *,
    base_decision: str,
    hard_block: bool,
    soft_block: bool,
    reason_codes: list[str],
) -> DecisionGateResult:
    decision = (base_decision or "SKIP").upper()
    execution_action, hard_block_reason = map_execution_action(
        decision=decision,
        hard_block=hard_block,
        soft_block=soft_block,
    )
    return DecisionGateResult(
        decision=decision,
        execution_action=execution_action,
        reason_codes=reason_codes,
        hard_block_reason=hard_block_reason,
    )
