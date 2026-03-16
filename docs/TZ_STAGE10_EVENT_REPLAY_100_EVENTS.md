# ТЗ Stage 10: Event Replay Engine (100+ подій)

## 1. Мета

Побудувати офлайн-контур історичного прогону подій, який:
1. Дає валідну оцінку edge без lookahead leakage.
2. Дозволяє швидко перевірити гіпотези по категоріях (crypto/finance/sports/politics/other).
3. Формує рішення для переходу в production trading (Stage 11).

## 2. In Scope

1. Replay мінімум 100 resolved подій.
2. Побудова time-sliced dataset (тільки дані, доступні в момент `t`).
3. Прогін Stage 7–9 policy/agent шару в режимі `SHADOW_ONLY`.
4. Порівняння category-specific policy vs global policy.
5. Оцінка готових agent-модулів (рейтинговий shortlist) у безпечному sandbox.

## 3. Out of Scope

1. Реальні ордери/реальна торгівля.
2. Обхід platform ToS/KYC обмежень.
3. Висновки про абсолютну прибутковість без confidence bounds.

## 4. Джерела даних та історичний timeline (обов'язкова конкретика)

Базові джерела:
1. Polymarket:
   - `Gamma` для market metadata і базових полів.
   - CLOB/Gamma snapshot history з локальної БД (`market_snapshots`), якщо API не дає історії напряму.
2. Manifold:
   - timeline реконструюється з `/v0/bets` (або еквівалентного bets endpoint),
   - `probability(t)` відновлюється з останньої ставки до `t`.
3. Metaculus:
   - `search + detail` для поточних полів,
   - `prediction-history` endpoint для `community_prediction(t)`.
4. Внутрішня БД:
   - `signal_history`, `stage7_agent_decisions`, `stage8_decisions`, `market_snapshots`.

Правило блокування replay:
1. Якщо для події неможливо побудувати `probability(t)` хоча б з 2 джерел або з локальних snapshots,
   така подія маркується `DATA_INSUFFICIENT_TIMELINE` і не входить в acceptance-вибірку.

## 5. Data Contract Replay Row

Кожен replay-row містить:
1. `event_id`, `market_id`, `platform`, `category`.
2. `replay_timestamp`.
3. `feature_observed_at_max` (максимальний timestamp джерел фічей).
4. `features_snapshot` (тільки pre-`replay_timestamp` фічі).
5. `policy_decision`, `agent_decision`, `execution_action`.
6. `predicted_edge_after_costs_pct`, `cost_components`.
7. `resolved_outcome` (`YES|NO|VOID|PENDING`).
8. `resolved_success_direction_aware`.
9. `trace_id`, `input_hash`, `model_version`.

## 6. Anti-Lookahead (алгоритм)

Визначення порушення:
1. Для кожної фічі `f` з `source_timestamp(f)`:
   - порушення, якщо `source_timestamp(f) > replay_timestamp - embargo_seconds`.
2. `embargo_seconds`:
   - мінімум `max(3600, labeling_horizon_seconds)`.
3. Для derived фічей:
   - `source_timestamp(derived)` = max timestamp усіх вхідних полів.
4. Заборонені поля у replay-фічах:
   - `resolved_*`, `probability_after_*`, `final_report_*`, будь-які post-resolution поля.

Обчислення:
1. `leakage_violations_count` = кількість replay-rows з >=1 порушенням.
2. `leakage_violation_rate` = `leakage_violations_count / rows_total`.

Hard gate:
1. `leakage_violations_count == 0`.

## 7. Модулі/агенти: security protocol перед tuning

1. Shortlist: Top-5 кандидатів із фіксованого списку (`docs/STAGE10_AGENT_MODULE_SHORTLIST.md`).
2. Кожен кандидат проходить:
   - dependency scan (`pip-audit`, `safety`),
   - static scan (`bandit`),
   - sandbox run (Docker `--network=none`, readonly FS, без secrets),
   - permission check (тільки allowlist tools).
3. Тільки після `SECURITY_PASS` дозволено replay benchmark і tuning.

## 8. Метрики Stage 10

Основні:
1. `precision_at_10`, `precision_at_25`, `precision_at_50`.
2. `post_cost_ev_mean_pct`, `post_cost_ev_ci_low_80`.
3. `brier_score`, `brier_skill_score`, `ece`.
4. `longshot_bias_error` (bucket 0-15%).
5. `scenario_sweeps_positive_count` з 18 сценаріїв.
6. `walkforward_negative_window_share`.
7. `reason_code_stability`.

Визначення `reason_code_stability`:
1. Для однакового `input_hash` у повторних replay runs:
   - частка збігів exact-set `reason_codes`.
2. Acceptance поріг: `>= 0.90`.

## 9. Acceptance Criteria (Stage 10)

1. `events_total >= 100`.
2. `core_categories_each >= 20` для crypto/finance/sports/politics.
3. `leakage_violations_count == 0`.
4. `post_cost_ev_ci_low_80 > 0` хоча б для 1 policy-кандидата в core категорії.
5. `scenario_sweeps_positive_count >= 12` (з 18) у кандидата.
6. `walkforward_negative_window_share <= 0.30`.
7. `reason_code_stability >= 0.90`.
8. Є мінімум 1 agent/module candidate, що пройшов security-gate.

## 10. DB schema / migrations

Обов'язково:
1. Таблиця `stage10_replay_rows` (id, event_id, replay_timestamp, features_json, decisions, metrics, trace_id).
2. Індекси:
   - `(event_id, replay_timestamp)`,
   - `(category, replay_timestamp)`,
   - `(input_hash)`.
3. Міграція: `0013_stage10_replay_rows.py`.

## 11. Deliverables

1. `docs/STAGE10_EVENT_REPLAY_REPORT.md`.
2. `docs/STAGE10_AGENT_MODULE_SECURITY_SCORECARD.md`.
3. `artifacts/research/stage10_replay_<timestamp>.json`.
4. `artifacts/research/stage10_replay_<timestamp>.csv`.
5. `artifacts/research/stage10_module_audit_<timestamp>.json`.

## 12. Технічна реалізація

Модулі:
1. `app/services/research/stage10_replay.py`.
2. `app/services/research/stage10_leakage_guard.py`.
3. `app/services/research/stage10_module_audit.py`.
4. `scripts/stage10_track_batch.py`.

API:
1. `GET /analytics/research/stage10/replay`.
2. `GET /analytics/research/stage10/module-audit`.
3. `POST /analytics/research/stage10/track`.

## 13. LLM budget control

1. `STAGE10_LLM_BUDGET_USD_MONTHLY` (обов'язково).
2. `>80%` бюджету: `cached-only` mode.
3. `>100%` бюджету: LLM calls заборонені, тільки replay з кешу.

## 14. Gate до Stage 11

Перехід дозволений, якщо:
1. Stage 10 acceptance виконаний.
2. Є formal verdict `GO` або `LIMITED_GO`.
3. Є зафіксований shortlist `primary + fallback` агентних стеків.
