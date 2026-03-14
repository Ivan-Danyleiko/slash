# Stage 7 Agent Architecture

## Призначення

Stage 7 — verification layer над Stage 6 policy-core.

Принцип:
1. Internal-first gate,
2. External verification,
3. Decision compose,
4. Guardrails + governance,
5. Cache-first idempotency.

## Data Flow

```text
Stage6 policy row
  -> internal_gate
  -> external_verifier
  -> decision_composer
  -> input_hash lookup (stage7_agent_decisions)
     -> cache hit: return cached payload
     -> cache miss: save payload + llm_cost_usd
  -> shadow metrics + final report
```

## Ключові компоненти

1. `app/services/agent_stage7/internal_gate.py`
2. `app/services/agent_stage7/external_verifier.py`
3. `app/services/agent_stage7/decision_composer.py`
4. `app/services/agent_stage7/store.py`
5. `app/services/research/stage7_harness.py`
6. `app/services/research/stage7_shadow.py`
7. `app/services/research/stage7_stack_scorecard.py`
8. `app/services/research/stage7_final_report.py`

## Контракти

Tool-output: structured JSON only.

Decision payload (мінімум):
1. `decision`
2. `reason_codes[]`
3. `evidence_bundle`
4. `input_hash`
5. `model_id/model_version/prompt_template_version/provider_fingerprint`

## Cost control

1. `normal`
2. `cached_only` (budget > 80%)
3. `hard_cutoff` (budget > 100%)

У hard cutoff Stage 6 policy-core продовжує роботу без AI override.

