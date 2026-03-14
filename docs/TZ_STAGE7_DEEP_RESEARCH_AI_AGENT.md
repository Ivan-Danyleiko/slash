# ТЗ Stage 7: Deep Research AI-Agent Layer

## 1. Контекст і мотивація

Проєкт має сильну інфраструктуру Stage 5/6 (збір даних, signal engine, execution-aware оцінка, governance), але бізнес-результат Stage 6 наразі `NO_GO`.

Ціль Stage 7: додати AI-agent verification layer, який:
1. аналізує внутрішні фактори (дані сервісу, research-метрики, guardrails);
2. виконує зовнішню верифікацію (контекст, правила, cross-source checks);
3. підвищує якість рішень `KEEP/MODIFY/REMOVE` без деградації risk-контролю.

Ключовий принцип: AI-агент не замінює policy-core, а працює як надбудова після внутрішньої валідації.

## 2. Мета Stage 7

1. Підвищити стабільний `post-cost edge` через кращу верифікацію сигналів.
2. Зменшити false-KEEP і false-REMOVE.
3. Отримати кандидат на `LIMITED_GO` у контрольованому режимі.
4. Побудувати відтворюваний процес deep research по агентних технологіях.

## 3. In/Out of Scope

### In Scope

1. Дослідження і порівняння агентних платформ:
   - `LangGraph`,
   - `LlamaIndex Workflows`,
   - `CrewAI`,
   - `AutoGen`,
   - plain LLM/API cloud стек (GPT/Claude + tool-calling, без окремого framework),
   - `n8n` як workflow-orchestration кандидат.
2. Проєктування AI-agent verification layer поверх поточного Stage 6.
3. PoC інтеграції мінімум 2 стеків.
4. Shadow-режим агента (без впливу на прод-рішення).
5. Метрики якості, ризику, вартості та операційної стабільності.

### Out of Scope

1. Повна заміна signal engine.
2. Автономна торгівля без governance.
3. Зовнішні інтеграції без audit trail.

## 4. Поточна база (використати як фундамент)

1. Stage 6 policy-core з рішеннями `KEEP/MODIFY/REMOVE/SKIP`.
2. Stage 6 risk-guardrails і governance verdict.
3. Stage 5/6 batch artifacts (`json/csv/jsonl`) як джерело тренувальної/валідаційної інформації.
4. Existing research endpoints для ознак і метрик.

## 5. Цільова архітектура Stage 7

```text
[Signal Engine + Stage6 Policy]
            |
            v
   [Internal Validation Gate]
            |
   (pass only if quality/risk gates OK)
            |
            v
   [AI Verification Agent Layer]
      - Internal factor analysis
      - External context checks
      - Contradiction detection
      - Confidence adjustment
            |
            v
 [Final Decision Composer + Guardrails]
            |
            v
 [Shadow Report -> LIMITED_GO Candidate -> Rollout]
```

## 6. Обов'язковий Deep Research блок (Agent Tech Study)

Для кожного кандидата (`LangGraph`, `LlamaIndex Workflows`, `CrewAI`, `AutoGen`, `n8n`, plain GPT/Claude API) виконати однаковий протокол оцінки:

1. Інтеграція з поточним Python/FastAPI/Celery стеком.
2. Підтримка tool-calling і контрольованих workflows.
3. Спостережуваність:
   - structured logs,
   - trace of decisions,
   - reproducibility.
4. Governance fit:
   - deterministic fallback,
   - guardrail hooks,
   - auditability.
5. Безпека:
   - key management,
   - access isolation,
   - prompt/data leakage controls.
6. Операційні параметри:
   - latency,
   - reliability,
   - failover behavior.
7. Вартість:
   - інфраструктурна,
   - API usage,
   - support/maintenance cost.

Обов'язковий deliverable:
`agent_stack_scorecard` з порівнянням усіх варіантів і рекомендацією:
1. `primary`,
2. `secondary/fallback`,
3. `reject` (з причинами).

## 6.1 Agent stack scoring matrix (обов'язкова)

Кожен стек оцінюється за шкалою 0..5 по осях:
1. Integration Fit (Python/FastAPI/Celery/DB).
2. Tooling & Control (tool-calling, determinism, state handling).
3. Observability (structured logs, traces, replayability).
4. Governance Fit (guardrails hooks, fallback, auditability).
5. Security (key isolation, data boundaries, least-privilege).
6. Reliability/Latency (SLA fit, timeout behavior, retries).
7. Cost (monthly TCO).
8. Vendor Risk (lock-in, portability).

Підсумок:
1. `weighted_score` (з вагами, визначеними до початку PoC).
2. Обов'язкова рекомендація `adopt / pilot / reject`.

Фіксовані ваги для Stage 7 PoC:
1. `Integration Fit`: `0.15`
2. `Tooling & Control`: `0.15`
3. `Observability`: `0.15`
4. `Governance Fit`: `0.20`
5. `Security`: `0.15`
6. `Reliability/Latency`: `0.10`
7. `Cost`: `0.05`
8. `Vendor Risk`: `0.05`

## 6.2 Initial stack priors (до PoC)

Попередня позиція (підтверджується/спростовується тільки після PoC):
1. `LangGraph` — primary candidate для verification decision path.
2. `plain GPT/Claude API` — secondary/fallback baseline (мінімум framework-overhead).
3. `LlamaIndex Workflows` — secondary candidate (event-driven orchestration).
4. `n8n` — orchestration/ops candidate, не primary decision path.
5. `AutoGen` — pilot-only через vendor trajectory risk.
6. `CrewAI` — pilot-only з жорсткими обмеженнями автономії/пам'яті.

## 7. Роль AI-агента в рішенні

### 7.1 Internal-first policy

AI-агент працює лише якщо внутрішні гейти пройдено:
1. data quality,
2. min labeled coverage,
3. risk-guardrails,
4. walk-forward validity.

### 7.2 Функції агента

1. Internal factor synthesis:
   - перевірка узгодженості execution/risk/research сигналів.
2. External verification:
   - перевірка контексту (event/rules consistency),
   - cross-source consistency flags.
3. Decision explanation:
   - reason codes,
   - evidence bundle.
4. Recommendation:
   - `KEEP/MODIFY/REMOVE` adjustment proposal (не прямий auto-override без guardrails).

### 7.3 Заборони

1. Заборонено використовувати LLM як єдине джерело probability/EV.
2. Заборонено bypass risk-guardrails.
3. Заборонено прод-override без shadow performance.
4. Заборонено non-deterministic inference для однакового input у production decision path:
   - `temperature=0`,
   - фіксована версія prompt/template.
5. Заборонено повторний LLM-call для однакового input без cache lookup.

## 7.4 External verification boundaries (Stage 7)

### In Scope

1. Manifold market description cross-check.
2. Metaculus community forecast cross-check для того ж event.
3. Resolution criteria ambiguity detection (LLM flag + structured reason).
4. Cross-platform spread consistency (`Polymarket vs Manifold vs Metaculus`).

### Out of Scope

1. Live news/social media APIs.
2. On-chain data beyond execution-related Polymarket context.
3. Sentiment analysis.
4. Forward-looking macro feeds.

## 7.5 Tool Interface Specification (обов'язкова)

Tools available to agent:
1. `get_signal_context(signal_id) -> {signal_type, confidence, liquidity, ev_v2, risk_flags}`
2. `get_signal_history_metrics(signal_type, horizon) -> {hit_rate, avg_win, avg_loss, n_samples}`
3. `get_market_snapshot(market_id) -> {platform, probability, volume_24h, resolution_time}`
4. `get_cross_platform_consensus(event_id) -> {polymarket_prob, manifold_prob, metaculus_median}`
5. `get_readiness_gate_status() -> {stage5_gate, data_quality_gate}`
6. `get_research_decision(signal_type) -> {walk_forward_verdict, overfit_flags}`

Tool response contract:
1. Тільки structured JSON.
2. Без вільного тексту як primary output.

Agent output contract:
`{decision, confidence_adjustment, reason_codes[], evidence_bundle, input_hash, model_id, model_version, prompt_template_version}`

`evidence_bundle` (мінімальна обов'язкова структура):
```json
{
  "internal_metrics_snapshot": {},
  "external_consensus": {
    "polymarket_prob": null,
    "manifold_prob": null,
    "metaculus_median": null
  },
  "contradictions": [],
  "resolution_ambiguity_flags": [],
  "fetched_at": "ISO-8601 timestamp"
}
```

## 8. Експериментальний дизайн

## 8.1 Фази

### Phase A (Week 1): Research Setup

1. Підготувати unified evaluation harness.
2. Зафіксувати datasets/артефакти для порівняння.
3. Визначити cost/latency budget.

### Phase B (Week 2): Multi-Stack PoC

1. Реалізувати PoC мінімум для 2 стеків (наприклад, LangGraph + plain GPT/API cloud).
2. Прогнати однаковий test suite.
3. Зібрати scorecard.
4. Виконати scenario-based threshold sweeps:
   - `position_size in [50, 100, 500]`,
   - `spread in [0.01, 0.03, 0.05]`,
   - `fee in [0.02, 0.025]`,
   - разом `18` сценаріїв.
5. Кандидат у `LIMITED_GO` формується тільки якщо позитивний у `>=12/18` сценаріях.

### Phase C (Week 3): Shadow Mode

1. Увімкнути shadow-агента на прод-потоці сигналів.
2. Мінімум 14 днів comparative logging:
   - base policy vs agent-verified policy.
3. Оцінити policy deltas і їх impact на post-cost EV proxy.
4. Обов'язково рахувати shadow comparison metrics:
   - `delta_keep_rate = (agent_keep_cnt - policy_keep_cnt) / policy_total`,
   - `post_hoc_precision = keep_resolved_correctly / total_keeps`,
   - `reason_code_stability` при повторному run того ж input,
   - `latency_p95`.

### Phase D (Week 4): Decision Gate

1. Побудувати Stage 7 final report.
2. Вердикт:
   - `GO` (для limited rollout),
   - `LIMITED_GO` (20% traffic),
   - `NO_GO` (залишити Stage 6 baseline).
3. `LIMITED_GO` можливий лише якщо:
   - `delta_keep_rate <= +15%`,
   - `post_hoc_precision >= baseline`,
   - `reason_code_stability >= 90%`,
   - `latency_p95 <= STAGE7_AGENT_MAX_LATENCY_MS`.

## 8.2 Метрики успіху

1. Якість:
   - зниження false-positives (KEEP -> REMOVE post-hoc),
   - покращення hit rate/EV у shadow-порівнянні.
   - позитивна lower bound bootstrap CI для key returns (див. Acceptance).
2. Ризик:
   - не гірші drawdown/risk-of-ruin проти baseline.
3. Операційність:
   - p95 latency в межах SLA,
   - error rate не вище baseline.
4. Прозорість:
   - 100% рішень мають reason/evidence trace.
5. Калібрація:
   - Brier score / reliability diagnostics як додаткова вісь до EV.
6. Anti-selection-bias:
   - при порівнянні >=3 стеків використовувати Deflated Sharpe Ratio (або еквівалентний correction).

## 8.3 PoC failure-mode test suite (обов'язковий)

PoC має явно тестувати:
1. `resolution_ambiguity_case`:
   - неоднозначні/суперечливі правила резолюції;
   - очікування: стабільний reason code + conservative adjustment.
2. `cross_source_mismatch_case`:
   - суттєве розходження між платформами для того ж event;
   - очікування: inconsistency flag + контроль confidence adjustment.
3. `provider_drift_case`:
   - зміни/нестабільність endpoint behavior;
   - очікування: graceful degradation + fallback without unsafe override.
4. `idempotency_case`:
   - той самий input_hash проганяється багато разів;
   - очікування: identical payload через cache-first policy.
5. `latency_budget_case`:
   - перевірка p95 latency під навантаженням;
   - очікування: SLA дотримано або безпечний fallback.

## 9. Acceptance Criteria Stage 7

Stage 7 вважається виконаним, якщо:
1. Є завершений `agent_stack_scorecard` по всіх обов'язкових платформах.
2. Є продемонстрований shadow-run мінімум 14 днів.
3. Є формальний Stage 7 final report з verdict.
4. Агент не порушує guardrails і не погіршує ризиковий профіль.
5. Є мінімум один інтеграційний стек, рекомендований до limited rollout.
6. KEEP-рішення проходять confidence criterion:
   - lower bound bootstrap CI для post-cost return > 0 з confidence >= 80%.
7. Walk-forward stability criterion:
   - не більше 30% windows з від'ємним post-cost return.
8. Використовується єдиний bootstrap protocol для всіх стеків:
   - фіксований `n_bootstrap`,
   - однаковий confidence level,
   - однакові правила min_n/low_confidence,
   - однаковий resampling policy.

## 10. Deliverables

1. `docs/STAGE7_AGENT_STACK_SCORECARD.md`
2. `docs/STAGE7_AGENT_ARCHITECTURE.md`
3. `docs/STAGE7_SHADOW_RESULTS.md`
4. `docs/TZ_STAGE7_STATUS.md`
5. `artifacts/research/stage7_batch_<timestamp>.json`
6. `artifacts/research/stage7_export_<timestamp>.csv`
7. `artifacts/research/stage7_agent_decisions_<timestamp>.jsonl`
8. `artifacts/research/stage7_final_report_<timestamp>.md`

## 11. Ризики та контроль

1. Overfit через надмірний prompt/threshold tuning.
   - Контроль: fixed weekly experiment cycle + locked evaluation windows.
2. Hallucination/невідтворюваність агентних рішень.
   - Контроль: evidence-first outputs + deterministic fallback.
3. API/vendor lock-in.
   - Контроль: multi-stack scorecard + fallback architecture.
4. Cost explosion.
   - Контроль: budget caps + cached analysis + tiered usage policy.
5. Scope creep через зовнішні контекстні джерела.
   - Контроль: жорсткий Stage 7 boundary (тільки cross-platform + rules context).
6. Provider API drift (особливо зміни Manifold endpoint/domain).
   - Контроль: versioned `base_url` у tool adapters + graceful fallback до partial-consensus замість hard fail.

## 12. Орієнтир реалізації (технічний)

1. Додати `app/services/agent_stage7/`:
   - `internal_gate.py`,
   - `external_verifier.py`,
   - `decision_composer.py`,
   - `stack_adapters/`.
2. Додати endpoints:
   - `GET /analytics/research/stage7/stack-scorecard`
   - `GET /analytics/research/stage7/shadow`
   - `GET /analytics/research/stage7/final-report`
3. Додати batch runner:
   - `scripts/stage7_track_batch.py`
4. Додати env:
   - `STAGE7_AGENT_PROVIDER`,
   - `STAGE7_AGENT_SHADOW_ENABLED`,
   - `STAGE7_AGENT_MAX_LATENCY_MS`,
   - `STAGE7_AGENT_MONTHLY_BUDGET_USD`,
   - `STAGE7_AGENT_INTERNAL_GATE_PROFILE`.

## 12.6 Observability and audit trail (обов'язково)

1. Кожен Stage 7 decision run має мати trace id.
2. Structured logs мають містити:
   - `trace_id`,
   - `signal_id`,
   - `input_hash`,
   - `stack_provider`,
   - `decision`,
   - `reason_codes`.
3. Кожен tool-call має окремий span/event у trace.
4. Має бути можливість повного replay decision chain по `input_hash`.

## 12.4 Cost circuit breaker (обов'язково)

1. Якщо `monthly_spend > budget * 0.80`:
   - alert + switch to `cached-only` mode.
2. Якщо `monthly_spend > budget * 1.00`:
   - hard cutoff для AI layer (`agent -> SKIP`),
   - Stage 6 policy-core продовжує роботу без AI-надбудови.

## 12.5 Agent idempotency and caching (обов'язково)

1. Кожне рішення зберігає:
   - `input_hash` (sha256 feature vector),
   - `model_id`,
   - `model_version`,
   - `prompt_template_version`,
   - `tool_snapshot_version`,
   - `provider_fingerprint` (де доступно, напр. system fingerprint).
2. Якщо `input_hash` вже існує:
   - повернути cached decision без нового LLM-call.
3. Повторний запуск на однаковому `input_hash` має давати identical decision payload.

## 12.7 Security and safety baseline (обов'язково)

1. Всі tools у Stage 7 — read-only.
2. Output з моделі проходить strict schema validation до decision composer.
3. Prompt/input sanitization для зовнішніх текстових полів (rules/description).
4. Будь-який schema violation => `safe_fail` (SKIP + reason code), без bypass.

## 13. Definition of Done

1. Технічно:
   - Stage 7 контур інтегровано без регресій Stage 6.
2. Науково/аналітично:
   - є доказова база порівняння агентних стеків.
3. Бізнесово:
   - є обґрунтований шлях до `LIMITED_GO` з кращим edge-профілем.
