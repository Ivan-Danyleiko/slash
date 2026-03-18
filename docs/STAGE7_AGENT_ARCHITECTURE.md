# Stage 7 Agent Architecture

## Призначення

Stage 7 — verification layer над Stage 6 policy-core.

Принцип:
1. Internal-first gate
2. External verification
3. Decision compose
4. Guardrails + governance
5. Cache-first idempotency

## Data Flow

```text
Stage6 policy row
  -> internal_gate (permissive/balanced/strict profile)
  -> external_verifier
  -> decision_composer
  -> input_hash lookup (stage7_agent_decisions)
     -> cache hit: return cached payload
     -> cache miss: LLM call via FallbackAdapter -> save payload + llm_cost_usd
  -> shadow metrics + final report
```

## LLM провайдери (FallbackAdapter)

При `STAGE7_AGENT_REAL_CALLS_ENABLED=true` система намагається провайдерів у порядку:

```
groq (llama-3.3-70b-versatile)
  → gemini (gemini-2.5-flash via googleapis OpenAI-compatible)
    → openrouter (google/gemini-2.5-flash)
      → SKIP (всі провайдери недоступні)
```

- Кожен провайдер обгорнутий в `OpenAICompatibleAdapter`
- `FallbackAdapter` пропускає провайдера при `adapter_http_error` / `adapter_transport_error` / `adapter_empty_output`
- Якщо вказано `Retry-After` ≤ 10 сек — чекає перед наступним провайдером

При `STAGE7_AGENT_REAL_CALLS_ENABLED=false`:
- Використовується `PlainApiAdapter` (детерміністичний, без HTTP)
- Пропускає `base_decision` як є, якщо немає протиріч і `internal_gate` пройшов

## Ключові компоненти

1. `app/services/agent_stage7/internal_gate.py` — попередній фільтр (confidence/liquidity/ev)
2. `app/services/agent_stage7/external_verifier.py` — зовнішні перевірки
3. `app/services/agent_stage7/decision_composer.py` — фінальне рішення
4. `app/services/agent_stage7/store.py` — кеш рішень по `input_hash`
5. `app/services/agent_stage7/stack_adapters/factory.py` — збирає FallbackAdapter
6. `app/services/agent_stage7/stack_adapters/openai_compatible_adapter.py` — HTTP-клієнт
7. `app/services/research/stage7_shadow.py` — оркестратор тіньового режиму
8. `app/services/research/stage7_harness.py` — harness для батчевої оцінки

## Контракти

Tool-output: structured JSON only.

Decision payload (мінімум):
1. `decision` (KEEP / MODIFY / REMOVE / SKIP)
2. `reason_codes[]`
3. `evidence_bundle`
4. `input_hash`
5. `model_id` / `model_version` / `prompt_template_version` / `provider_fingerprint`

## Internal Gate профілі

| Профіль | min_confidence | min_liquidity | min_ev |
|---------|---------------|--------------|--------|
| `strict` | 0.50 | 0.60 | 0.010 |
| `balanced` | 0.40 | 0.50 | 0.005 |
| `permissive` | 0.30 | 0.40 | 0.000 |

Рекомендований для продакшену: `permissive` (разом із зниженими `AGENT_POLICY_*` порогами).

## Agent Policy пороги (base_decision)

Base-decision будується з `build_agent_decision_report` у `app/services/agent/policy.py`:

- `AGENT_POLICY_MIN_CONFIDENCE=0.35` — нижче → `low_confidence` → SKIP
- `AGENT_POLICY_MIN_LIQUIDITY=0.50` — нижче → `low_liquidity` → SKIP
- `AGENT_POLICY_KEEP_EV_THRESHOLD_PCT=0.003` — вище → KEEP
- `AGENT_POLICY_MODIFY_EV_THRESHOLD_PCT=0.001` — між MODIFY і KEEP

## Cost control

1. `normal` — повні LLM-виклики
2. `cached_only` (budget > 80%) — тільки кешовані рішення
3. `hard_cutoff` (budget > 100%) — Stage 6 policy-core без AI override

У hard cutoff Stage 6 продовжує роботу без AI override.

## UNCERTAIN_SHADOW_MODE

Якщо primary LLM (Gemini) повертає SKIP, але сигнал є в shadow-режимі з `uncertainty_liquid` mode — внутрішній `stage7_verifier` може перекрити рішення на KEEP з reason `UNCERTAIN_SHADOW_MODE`. Це детерміністична логіка, не LLM.
