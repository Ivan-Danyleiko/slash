# TZ Stage 7 Status

## Поточний статус

- Статус: `Phase A started`
- Дата старту: `2026-03-14`
- Джерело вимог: `TZ_STAGE7_DEEP_RESEARCH_AI_AGENT.md`

## Виконано в першій ітерації

1. Додано Stage 7 agent layer каркас:
   - `app/services/agent_stage7/internal_gate.py`
   - `app/services/agent_stage7/external_verifier.py`
   - `app/services/agent_stage7/decision_composer.py`
2. Додано Stage 7 research services:
   - `stage7_stack_scorecard.py`
   - `stage7_shadow.py`
   - `stage7_final_report.py`
3. Додано Stage 7 API endpoints:
   - `GET/POST /analytics/research/stage7/stack-scorecard(/track)`
   - `GET/POST /analytics/research/stage7/shadow(/track)`
   - `GET/POST /analytics/research/stage7/final-report(/track)`
4. Додано batch runner:
   - `scripts/stage7_track_batch.py`
   - генерує `json/csv/jsonl/md` артефакти.
5. Додано Stage 7 env-параметри в `config.py` і `.env.example`.

## Що далі (найближчий крок)

1. Прогнати Stage 7 batch на historical DB.
2. Зафіксувати перший Stage 7 artifact set.
3. Додати unit tests для Stage 7 services.
4. Оновити API/README документацію Stage 7 endpoints.

## Перший batch run (Phase A)

Команда:
`env DATABASE_URL=sqlite:///artifacts/research/stage5_xplat3.db .venv/bin/python scripts/stage7_track_batch.py`

Артефакти:
1. `artifacts/research/stage7_batch_20260314_121716.json`
2. `artifacts/research/stage7_export_20260314_121716.csv`
3. `artifacts/research/stage7_agent_decisions_20260314_121716.jsonl`
4. `artifacts/research/stage7_final_report_20260314_121716.md`

Підсумок:
1. `final_decision=NO_GO`
2. `recommended_action=keep_stage6_baseline_and_continue_research`
3. `top_stack=langgraph`
4. `agent_decision_coverage=0.0` (на поточному 14-day вікні немає валідного shadow-потоку для порівняння)

## Phase B progress (adapter + harness)

Виконано:
1. Додано stack adapters:
   - `langgraph` (simulated deterministic adapter),
   - `plain_llm_api` (simulated baseline adapter).
2. Додано Stage 7 harness:
   - 5 failure-mode кейсів (`resolution_ambiguity`, `cross_source_mismatch`, `provider_drift`, `idempotency`, `latency_budget`).
3. Додано API:
   - `GET/POST /analytics/research/stage7/harness(/track)`.
4. Scorecard тепер підтримує емпіричний overlay з harness метрик.
5. Додано Stage 7 foundation tests:
   - `tests/test_stage7_foundation.py` -> `3 passed`.

Актуальний batch (Phase B):
1. `artifacts/research/stage7_batch_20260314_122510.json`
2. `artifacts/research/stage7_export_20260314_122510.csv`
3. `artifacts/research/stage7_agent_decisions_20260314_122510.jsonl`
4. `artifacts/research/stage7_final_report_20260314_122510.md`

Ключові результати:
1. `final_decision=NO_GO`
2. `top_stack=langgraph`
3. `harness_stacks_tested=2`
4. `harness_all_pass_rate_gte_80pct=true`
5. `harness_all_idempotent_gte_90pct=true`

## Додатково закрито (поточна ітерація)

1. Реальний persistence cache для Stage 7 рішень:
   - нова таблиця `stage7_agent_decisions` з `uq(input_hash)`.
2. Cache-first idempotency:
   - при повторному `input_hash` рішення повертається з БД без нового LLM-like виклику.
3. Cost circuit breaker у Stage 7 shadow:
   - `mode=normal/cached_only/hard_cutoff`,
   - місячний budget/spend трекінг,
   - budget-based fallback behavior.
4. Stage 7 final report враховує cost-control checks.
5. Тести Stage 7 foundation розширені до `4 passed` (включно з cache/store).

## Поточний blocker для Phase C на цій historical DB

`agent_decision_coverage=0.0`, бо в `stage5_xplat3.db` відсутній поточний signal stream у таблиці `signals` для shadow-порівняння.
Для ненульового coverage потрібен live/свіжий signal потік або окремий backfill `signals` з `signal_history`.

## Audit-driven bugfix iteration

Закрито:
1. `input_hash` стабілізовано:
   - volatile `fetched_at` виключено з hash material.
2. `external_verifier` перейшов з exact title match на нормалізований fuzzy title similarity.
3. Розширено ambiguity token set для rules ambiguity detection.
4. Stage 7 decision logic більше не блокується жорстко через `stage6_not_no_go=false`:
   - можливий `LIMITED_GO` при виконанні Stage 7 checks.
5. Реалізовано DB-backed cache/idempotency:
   - таблиця `stage7_agent_decisions`,
   - cache-first retrieval по `input_hash`.
6. Реалізовано cost circuit breaker в runtime:
   - `normal` / `cached_only` / `hard_cutoff`.
7. Додано calibration/anti-selection метрики:
   - `brier_score`,
   - `deflated_sharpe_proxy`.
8. Додано docs deliverables:
   - `docs/STAGE7_AGENT_STACK_SCORECARD.md`,
   - `docs/STAGE7_AGENT_ARCHITECTURE.md`,
   - `docs/STAGE7_SHADOW_RESULTS.md`.

Останній batch:
1. `artifacts/research/stage7_batch_20260314_124119.json`
2. `coverage=1.0`
3. `cache_hits_run=300`, `llm_calls_run=0`
4. `final_decision=LIMITED_GO`

Ще відкрито:
1. Реальні LLM/API adapters (поки adapters simulated).
2. Повноцінний OpenTelemetry spans pipeline (зараз lightweight trace fields у shadow rows).

## Оновлення 2026-03-14 (поточний прогін)

Перевірка:
1. `.venv/bin/pytest -q tests/test_stage7_foundation.py` -> `5 passed`.
2. Batch run з локальною historical БД:
   - `env DATABASE_URL=sqlite:///artifacts/research/stage5_xplat3.db .venv/bin/python scripts/stage7_track_batch.py`
3. Увага по середовищу:
   - без override `DATABASE_URL` скрипт звертається до `db` (docker host) і падає у локальному режимі.

Останній artifact set:
1. `artifacts/research/stage7_batch_20260314_125326.json`
2. `artifacts/research/stage7_export_20260314_125326.csv`
3. `artifacts/research/stage7_agent_decisions_20260314_125326.jsonl`
4. `artifacts/research/stage7_final_report_20260314_125326.md`

Ключовий результат:
1. `final_decision=LIMITED_GO`
2. `agent_decision_coverage=1.0`
3. `delta_keep_rate=0.0`
4. `reason_code_stability=1.0`
5. `latency_p95_ms=15.3974`
6. `cost_mode=normal`

Ризики/обмеження, що лишаються:
1. На цьому run `llm_calls_run=0`, `cache_hits_run=300` (warm-cache режим).
2. Реальні external LLM-виклики для adapter path лишаються опціональними (через env) і ще не є production-default.

## Оновлення 2026-03-14 (після fuzzy-normalization)

Регресійні тести:
1. `.venv/bin/pytest -q tests/test_stage7_foundation.py` -> `6 passed`.
2. Додано тест на fuzzy cross-platform match у `external_verifier`.

Останній batch:
1. `artifacts/research/stage7_batch_20260314_125529.json`
2. `artifacts/research/stage7_export_20260314_125529.csv`
3. `artifacts/research/stage7_agent_decisions_20260314_125529.jsonl`
4. `artifacts/research/stage7_final_report_20260314_125529.md`

Ключові метрики:
1. `final_decision=LIMITED_GO`
2. `recommended_action=enable_stage7_shadow_to_20pct_rollout`
3. `agent_decision_coverage=1.0`
4. `delta_keep_rate=-0.023333`
5. `reason_code_stability=0.936667`
6. `latency_p95_ms=23.3656`
7. `llm_calls_run=19`, `cache_hits_run=281` (не лише warm-cache)

## Provider-router update (Gemini / Groq / OpenRouter)

Додано:
1. Підтримка `STAGE7_AGENT_PROVIDER_PROFILE` у factory для `plain_llm_api`:
   - `openai` (існуючий),
   - `gemini`,
   - `groq`,
   - `openrouter`.
2. Пряме читання ключів:
   - `GEMINI_API_KEY`,
   - `GROQ_API_KEY`,
   - `OPENROUTER_API_KEY`.
3. Моделі за профілями:
   - `STAGE7_GEMINI_MODEL`,
   - `STAGE7_GROQ_MODEL`,
   - `STAGE7_OPENROUTER_MODEL`.
4. Додаткові OpenRouter headers:
   - `STAGE7_OPENROUTER_HTTP_REFERER`,
   - `STAGE7_OPENROUTER_X_TITLE`.

Верифікація:
1. `.venv/bin/pytest -q tests/test_stage7_foundation.py` -> `9 passed`.
2. Smoke-check adapters:
   - Gemini profile: adapter піднімається, відповідь приходить через `chat/completions` fallback (поки часто `adapter_parse_fallback`).
   - Groq profile: `403` (найімовірніше ключ/доступ/кредити).
   - OpenRouter profile: `400` (потрібно перевірити model id і доступність моделі для ключа).

## Gap-closure update (тімлід/тестер report)

Закрито по коду:
1. **§8.1 Phase B / 18 scenarios sweeps**:
   - додано `scenario_sweeps` у `stage7_shadow`:
   - `position_size=[50,100,500] x spread=[0.01,0.03,0.05] x fee=[0.02,0.025] => 18`.
   - додається `passes_12_of_18`.
2. **§9.6–9.8 Bootstrap CI + unified protocol**:
   - додано `bootstrap_protocol` (`n_bootstrap=500`, `confidence=0.80`, `seed=42`),
   - `bootstrap_ci_low_80`, `bootstrap_ci_high_80`, `bootstrap_ci_lower_bound_positive_80`.
3. **§7.5 Tool Interface Spec (6 tools)**:
   - додано `app/services/agent_stage7/tools.py`,
   - `external_verifier` працює через tools, не через сирі прямі DB квері.
4. **§12.6 Observability / structured logs**:
   - додано structured logging у `stage7_shadow` (`signal_id`, `trace_id`, `input_hash`, `provider`, `decision`, `reason_codes`, `cache_hit`).
5. **Harness real adapter gap**:
   - `build_stage7_harness_report(..., settings=...)`,
   - при `STAGE7_AGENT_REAL_CALLS_ENABLED=true` додає реальний adapter в harness.
6. **GO action mapping bug**:
   - `GO -> enable_stage7_rollout_full_with_guardrails`,
   - `LIMITED_GO -> enable_stage7_shadow_to_20pct_rollout`.
7. **tool_snapshot_version hardcode gap**:
   - `save_stage7_decision` тепер приймає параметр і не прив'язаний жорстко до `"v1"`.

Валідація:
1. `.venv/bin/pytest -q tests/test_stage7_foundation.py` -> `12 passed`.
2. Batch:
   - `artifacts/research/stage7_batch_20260314_174935.json`
   - `artifacts/research/stage7_export_20260314_174935.csv`
   - `artifacts/research/stage7_agent_decisions_20260314_174935.jsonl`
   - `artifacts/research/stage7_final_report_20260314_174935.md`

Поточний результат на цій historical БД:
1. `final_decision=NO_GO` (очікувано за жорсткими новими acceptance check'ами)
2. `sweeps_positive=5/18` (не проходить `>=12/18`)
3. `bootstrap_ci_lower_bound_positive_80=false`
4. `walkforward_negative_window_share_le_30pct=false`

## Додатково (provider-profile isolation + stability fix)

1. Виправлено cache-key cross-profile leakage:
   - provider у Stage 7 shadow тепер нормалізується як `plain_llm_api:<profile>` для `gemini/groq/openrouter`.
   - `input_hash` тепер включає:
     - `provider`,
     - `model_id`,
     - `model_version`,
     - `prompt_template_version`.
2. Виправлено `reason_code_stability`:
   - повторна перевірка йде через `get_cached_stage7_decision(input_hash)`,
   - а не через інший локальний deterministic path.
3. Після цього профільні прогони більше не ділять спільний cache і реально можуть робити окремі LLM виклики.

## Comparative run (Gemini/Groq/OpenRouter, 30d/200)

Проведено 3 послідовні прогони:
1. `stage7_batch_20260314_180515.json` (gemini)
2. `stage7_batch_20260314_180606.json` (groq)
3. `stage7_batch_20260314_180654.json` (openrouter)

Порівняльний звіт:
1. `docs/STAGE7_PROVIDER_COMPARISON.md`

Результат:
1. Усі профілі поки `NO_GO` по бізнес-критеріях Stage 7 (`sweeps`, `bootstrap_lb`, `walkforward_share`).
2. По операційній якості (latency/error-rate) поточний best-effort порядок:
   - `primary_candidate_now=groq`,
   - `fallback_candidate_now=gemini`,
   - `openrouter` як третій.

## Data-gap classification fix (NO_GO vs DATA_PENDING)

Що змінено:
1. Stage 7 final gate тепер відрізняє:
   - `NO_GO` (даних достатньо, але стратегія/метрики не проходять),
   - `NO_GO_DATA_PENDING` (даних для валідної оцінки недостатньо).
2. Додано `data_sufficiency` секцію в shadow/final report:
   - `resolved_rows_total`,
   - `keeps_with_resolution`,
   - `walkforward_windows_total`,
   - порогові мінімуми та `data_sufficient_for_acceptance`.

Валідація:
1. `.venv/bin/pytest -q tests/test_stage7_foundation.py` -> `13 passed`.
2. Batch: `artifacts/research/stage7_batch_20260314_191107.json`
3. Результат:
   - `final_decision=NO_GO_DATA_PENDING`,
   - `recommended_action=continue_shadow_collect_labels_no_rollout`,
   - `data_sufficient_for_acceptance=false`,
   - `resolved_rows_total=0`.

## Data pipeline remediation (manual + scheduler)

Діагностика перед ручним прогоном:
1. `signal_history total=2595`
2. `resolved_success != null = 0`
3. `resolution_checked_at is null = 2595`

Ручний прогін labeling jobs на historical DB:
1. `label_signal_history_1h`: `updated=78` (з `515`)
2. `label_signal_history_6h`: `updated=401` (з `1024`)
3. `label_signal_history_24h`: `updated=1533` (з `2574`)
4. `label_signal_history_resolution`: `checked_resolved=115`, `updated=95`

Scheduler fixes:
1. Додано `analyze_markets` у beat schedule (`кожні 15 хв`, offset `2-59/15`).
2. `label_signal_history_resolution` змінено з `1 раз/день` на `щогодини` (`minute=10`).

Стан після ремедіації:
1. Stage 7 batch: `artifacts/research/stage7_batch_20260314_192452.json`
2. `final_decision=NO_GO_DATA_PENDING` (правильно класифіковано)
3. `data_sufficiency`:
   - `resolved_rows_total=5` (у acceptance window 30d/200),
   - `keeps_with_resolution=0`,
   - `walkforward_windows_total=0`.
4. Для широкого вікна (`90d/1000`): `resolved_rows_total=36`, `keeps_with_resolution=5`, `walkforward_windows_total=0`.

Root cause, що лишився:
1. У поточному historical DB walk-forward має `rows=2`, але `nonzero_test_windows=0` для обох типів (`DIVERGENCE`, `RULES_RISK`) у 90-денному вікні.
2. Це підтверджує: далі потрібен live stream і накопичення свіжих labeled тест-вікон.

## Provider adapter bugfix (adapter_parse_fallback / 403 / 400)

Закрито три баги в `openai_compatible_adapter.py`:

1. **Groq 403 → fallback не спрацьовував**:
   - `/responses` ендпоінт не підтримується Groq → повертає 403.
   - `403` не був у fallback-set `{400,404,405}` → `/chat/completions` ніколи не викликався.
   - Фікс: додано `403` до fallback-set.

2. **OpenRouter 400 на chat/completions**:
   - `response_format: {"type": "json_object"}` не підтримується всіма моделями.
   - Фікс: прибрано `response_format` зі chat-тіла, посилено system-prompt з явним форматом JSON.

3. **Gemini `adapter_parse_fallback`**:
   - `_safe_parse_decision` не обробляв `reason_codes` як рядок (LLM може повертати `"no_issues"` замість `["no_issues"]`).
   - Фікс: додано `_parse_reason_codes()` з нормалізацією str→list (split by comma).
   - Фікс: regex `\{[\s\S]*?\}` (non-greedy) для коректного вилучення першого JSON-об'єкта з prose.

Тести: `.venv/bin/pytest -q tests/test_stage7_foundation.py` -> `12 passed`.
Нові тести: `test_stage7_parse_reason_codes_handles_string`, `test_stage7_safe_parse_handles_string_reason_codes`, `test_stage7_safe_parse_handles_missing_reason_codes`.
