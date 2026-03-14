from __future__ import annotations

from app.services.agent_stage7.stack_adapters.base import Stage7AdapterInput


_DOWNGRADE = {
    "KEEP": "MODIFY",
    "MODIFY": "REMOVE",
    "REMOVE": "REMOVE",
    "SKIP": "SKIP",
}


class LangGraphAdapter:
    name = "langgraph"

    def decide(self, payload: Stage7AdapterInput) -> dict:
        base = str(payload.base_decision or "SKIP").upper()
        if base not in _DOWNGRADE:
            base = "SKIP"
        if not payload.internal_gate_passed:
            return {
                "decision": "SKIP",
                "reason_codes": ["adapter_internal_gate_failed"],
                "provider_fingerprint": "langgraph_sim_v1",
                "simulated_latency_ms": 11.0,
            }
        decision = base
        reasons: list[str] = []
        if payload.contradictions_count > 0:
            decision = _DOWNGRADE[decision]
            reasons.append("adapter_cross_source_contradiction")
        if payload.ambiguity_count > 0:
            decision = _DOWNGRADE[decision]
            reasons.append("adapter_resolution_ambiguity")
        if not reasons:
            reasons.append("adapter_no_override")
        return {
            "decision": decision,
            "reason_codes": reasons,
            "provider_fingerprint": "langgraph_sim_v1",
            "simulated_latency_ms": 11.0,
        }

