from __future__ import annotations

from app.services.agent_stage7.stack_adapters.base import Stage7AdapterInput


_DOWNGRADE = {
    "KEEP": "MODIFY",
    "MODIFY": "REMOVE",
    "REMOVE": "REMOVE",
    "SKIP": "SKIP",
}


class PlainApiAdapter:
    name = "plain_llm_api"

    def decide(self, payload: Stage7AdapterInput) -> dict:
        base = str(payload.base_decision or "SKIP").upper()
        if base not in _DOWNGRADE:
            base = "SKIP"
        if not payload.internal_gate_passed:
            return {
                "decision": "SKIP",
                "reason_codes": ["adapter_internal_gate_failed"],
                "provider_fingerprint": "plain_api_sim_v1",
                "simulated_latency_ms": 6.5,
            }
        # Plain-API baseline: conservative only on hard contradictions.
        decision = base
        reasons: list[str] = []
        if payload.contradictions_count > 0:
            decision = _DOWNGRADE[decision]
            reasons.append("adapter_cross_source_contradiction")
        elif payload.ambiguity_count > 0:
            reasons.append("adapter_ambiguity_warn_only")
        else:
            reasons.append("adapter_no_override")
        return {
            "decision": decision,
            "reason_codes": reasons,
            "provider_fingerprint": "plain_api_sim_v1",
            "simulated_latency_ms": 6.5,
        }

