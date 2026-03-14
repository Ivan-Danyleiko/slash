# Prediction Market Scanner — Повний README

## 1. Мета проєкту

Prediction Market Scanner — це система для збору ринків прогнозів, генерації сигналів, оцінки їх виконуваності (execution-aware), і прийняття рішень через policy layer з жорсткими risk-guardrails.

Головна ціль:
1. Виявляти сигнали, які мають реальний post-cost edge.
2. Відсіювати шум і невиконувані ідеї.
3. Приймати формалізоване рішення rollout-рівня: `GO / LIMITED_GO / NO_GO`.

## 2. Що система робить по кроках

1. Збирає ринки/оновлення з кількох джерел.
2. Будує сигнали (DIVERGENCE, RULES_RISK, інші типи за таксономією).
3. Записує історію сигналів у `signal_history`.
4. Долейблює відкладені результати на горизонтах:
   - `15m`, `30m` (Stage 6),
   - `1h`, `6h`, `24h`,
   - `resolution`.
5. Рахує execution-метрики:
   - expected edge,
   - costs/slippage impact,
   - utility/executability.
6. Рахує research-метрики:
   - hit rate,
   - avg/median return,
   - Sharpe-like,
   - risk of ruin,
   - drawdown,
   - walk-forward consistency.
7. Приймає рішення policy-рівня:
   - per signal: `KEEP/MODIFY/REMOVE/SKIP`,
   - per stage: `GO/LIMITED_GO/NO_GO`.

## 3. Архітектура

### 3.1 Рівні системи

1. Data Collection Layer:
   - collectors/ingest scripts,
   - periodic synchronization.
2. Storage Layer:
   - PostgreSQL/SQLite,
   - таблиці ринків, сигналів, історії, подій.
3. Signal Engine Layer:
   - генерація сигналів,
   - score breakdown,
   - duplicate/divergence logic,
   - execution simulation.
4. Research Layer (Stage 5/6):
   - аналітичні звіти,
   - threshold research,
   - walk-forward,
   - Monte Carlo,
   - readiness/governance.
5. Decision Layer:
   - deterministic policy engine,
   - risk guardrails,
   - rollout verdict.
6. Delivery Layer:
   - Telegram push/digest,
   - A/B tagging,
   - transparency fields у повідомленнях.

### 3.1.1 End-to-end data flow

```text
[Collectors + Ingest]
      |
      v
[markets / snapshots] ----> [duplicate + divergence analyzers]
      |                                  |
      |                                  v
      +--------------------------> [signal engine]
                                         |
                                         v
                                  [signals + signal_history]
                                         |
                 +-----------------------+----------------------+
                 |                                              |
                 v                                              v
      [labeling jobs 15m/30m/1h/6h/24h/resolution]      [execution simulator v2]
                 |                                              |
                 +-----------------------+----------------------+
                                         v
                             [research metrics + policy]
                                         |
                                         v
                         [governance gate GO/LIMITED_GO/NO_GO]
                                         |
                           +-------------+-------------+
                           |                           |
                           v                           v
                    [telegram delivery]       [batch artifacts/reports]
```

### 3.2 Ключові модулі

1. `app/services/signals/engine.py`
   - генерація сигналів,
   - score breakdown,
   - топ-селекція.
2. `app/services/signals/execution.py`
   - `ExecutionSimulatorV2`,
   - empirical EV з fallback на v1.
3. `app/services/agent/policy.py`
   - deterministic decisions per signal.
4. `app/services/research/*`
   - stage5/stage6 reports,
   - governance,
   - risk guardrails,
   - type35,
   - final report.
5. `app/tasks/jobs.py`, `app/tasks/worker.py`
   - labeling jobs,
   - periodic analytics/service jobs.
6. `app/api/routes/analytics.py`, `app/api/routes/admin.py`
   - research/admin endpoints.

### 3.3 Runtime components

1. API service (`FastAPI`):
   - приймає admin/research запити;
   - віддає аналітику і статуси.
2. Worker (`Celery`):
   - sync/analyze/generate jobs;
   - labeling jobs;
   - cleanup і provider checks.
3. Broker/queue (`Redis`):
   - черги задач і розклад.
4. DB (`PostgreSQL` у проді, `SQLite` для локальних batch):
   - storage для ринків/сигналів/історії/метрик.
5. Telegram delivery:
   - signal push;
   - daily digest;
   - tracking user events.

## 4. Дані та модель

### 4.1 `signal_history` (концептуально)

Таблиця знімає snapshot сигналу в момент створення та зберігає відкладені outcomes:
1. Базові поля:
   - `signal_id`, `signal_type`, `platform`, `market_id`, `timestamp`.
2. Snapshot поля:
   - `probability_at_signal`,
   - `related_market_probability`,
   - `divergence`,
   - `liquidity`, `volume_24h`,
   - `execution_assumptions_version`.
3. Відкладені лейбли:
   - `probability_after_15m`,
   - `probability_after_30m`,
   - `probability_after_1h`,
   - `probability_after_6h`,
   - `probability_after_24h`,
   - `resolved_probability`,
   - `resolved_success`.
4. Backfill/якість:
   - `source_tag`,
   - `timestamp_bucket`,
   - `missing_label_reason`.

### 4.3 Інші ключові сутності (спрощено)

1. `markets`:
   - ринки з платформ;
   - probability/liquidity/volume/metadata.
2. `signals`:
   - поточні сигнали для селекції і delivery.
3. `duplicate_pair_candidates`:
   - кандидати дублікатів;
   - drop reasons та stage-проходження.
4. `job_runs`:
   - технічний трекінг scheduled/manual jobs.
5. `user_events`:
   - події продукту (для A/B та engagement-аналітики).

### 4.2 Якість і безпечність даних

1. Індекси та idempotent-захист для historical ingestion.
2. Labeling jobs для регулярного дозаповнення.
3. Cleanup/retention policy (`90` днів у research-контурі).

## 5. Алгоритми та scoring

### 5.1 Signal modes та фільтри

1. ARBITRAGE/UNCERTAINTY логіка з режимами.
2. RULES_RISK explicit/missing split з daily caps/penalties.
3. Duplicate detector з профілями strict/balanced/aggressive.

### 5.2 Execution-aware оцінка

Система враховує:
1. empirical returns з labeled history;
2. fees/slippage/spread-like impact;
3. position-size профілі;
4. platform-aware припущення;
5. fallback на базову модель при нестачі даних.

### 5.3 Ranking (Appendix C)

Використовується формула:
`0.35*edge + 0.25*liquidity + 0.20*execution_safety + 0.10*freshness + 0.10*confidence - penalties`

Є feature flag для безпечного rollback на legacy ranking.

### 5.4 Decision framework (типи рішень)

1. `KEEP`:
   - сигнал/тип проходить EV + risk пороги.
2. `MODIFY`:
   - є потенціал, але потрібні stricter thresholds.
3. `REMOVE`:
   - негативний або недостатній edge після costs.
4. `SKIP`:
   - сигнал не підходить під policy constraints (low confidence/liquidity/etc).

## 6. Research framework (Stage 5 + Stage 6)

### 6.1 Що досліджується

1. Signal type performance.
2. Divergence thresholds.
3. Liquidity safety.
4. Signal lifetime.
5. Ranking formulas.
6. Platform comparison.
7. Event clusters.
8. Monte Carlo.
9. Walk-forward validation.
10. Governance + risk guardrails.

### 6.2 Ключові аналітичні принципи

1. Рішення тільки на post-cost метриках.
2. Walk-forward з embargo.
3. Bootstrap CI + `low_confidence` при малих вибірках.
4. Overfit sanity checks у governance.
5. Type 3/5 можуть отримати `INSUFFICIENT_ARCHITECTURE` при відсутності потрібної гранулярності.

### 6.3 Batch artifacts (що генерується)

1. Stage 5 batch:
   - `stage5_batch_<ts>.json`
   - `stage5_export_<ts>.csv`
2. Stage 6 batch:
   - `stage6_batch_<ts>.json`
   - `stage6_export_<ts>.csv`
3. Stage 7 batch:
   - `stage7_batch_<ts>.json`
   - `stage7_export_<ts>.csv`
   - `stage7_agent_decisions_<ts>.jsonl`
   - `stage7_final_report_<ts>.md`
4. Experiment registry:
   - `artifacts/research/experiments.jsonl`

### 6.4 Readiness interpretation

1. `PASS`:
   - критичні checks пройдені.
2. `WARN`:
   - критичні checks пройдені, але є non-critical gaps.
3. `FAIL`:
   - є провал хоча б одного critical check.

## 7. Agent layer (Stage 6)

Поточний агент — deterministic policy engine.

Вхідні дані агента:
1. execution metrics;
2. historical type metrics;
3. risk guardrail context;
4. readiness/governance constraints.

Вихід:
1. рішення `KEEP/MODIFY/REMOVE/SKIP`;
2. policy version;
3. risk flags;
4. reason codes.

Важливо:
агент не є black-box predictor і не генерує probabilities "з нуля".

## 8. API (огляд)

### 8.1 Admin

1. Запуск аналізу/лейблінгу.
2. Ручні тригери labeling jobs:
   - `15m`, `30m`, `1h`, `6h`, `24h`, `resolution`.
3. Provider contract checks trigger.
4. Manual run hooks для операційних перевірок.

### 8.2 Analytics / Research

Ключові групи:
1. Core research:
   - `signals`, `signal-types`, `divergence-thresholds`, `liquidity-safety`, `signal-lifetime`.
2. Advanced:
   - `ranking-formulas`, `platform-comparison`, `event-clusters`, `monte-carlo`.
3. Stage 5 orchestration:
   - `final-report`, `readiness-gate`, `export-package`, `experiments`.
4. Stage 6 orchestration:
   - `agent-decisions`,
   - `walkforward`,
   - `stage6-governance`,
   - `stage6-risk-guardrails`,
   - `stage6-type35`,
   - `stage6-final-report`.
5. Stage 7 orchestration:
   - `stage7/stack-scorecard`,
   - `stage7/harness`,
   - `stage7/shadow`,
   - `stage7/final-report`.

### 8.3 Повний список Stage 6 endpoint-ів (операційно важливі)

1. `GET /analytics/research/agent-decisions`
2. `GET /analytics/research/walkforward`
3. `POST /analytics/research/walkforward/track`
4. `GET /analytics/research/stage6-governance`
5. `POST /analytics/research/stage6-governance/track`
6. `GET /analytics/research/stage6-risk-guardrails`
7. `POST /analytics/research/stage6-risk-guardrails/track`
8. `GET /analytics/research/stage6-type35`
9. `POST /analytics/research/stage6-type35/track`
10. `GET /analytics/research/stage6-final-report`
11. `POST /analytics/research/stage6-final-report/track`

### 8.4 Повний список Stage 5 endpoint-ів (операційно важливі)

1. `GET /analytics/research/signals`
2. `GET /analytics/research/signals.csv`
3. `GET /analytics/research/progress`
4. `GET /analytics/research/divergence-thresholds`
5. `GET /analytics/research/monte-carlo`
6. `GET /analytics/research/result-tables`
7. `GET /analytics/research/signal-types`
8. `GET /analytics/research/signal-types/optimize`
9. `GET /analytics/research/liquidity-safety`
10. `GET /analytics/research/signal-lifetime`
11. `GET /analytics/research/platform-comparison`
12. `GET /analytics/research/ranking-formulas`
13. `GET /analytics/research/event-clusters`
14. `GET /analytics/research/final-report`
15. `GET /analytics/research/export-package`
16. `GET /analytics/research/export-package.csv`
17. `GET /analytics/research/readiness-gate`
18. `GET /analytics/research/experiments`
19. `GET /analytics/research/data-quality`
20. `GET /analytics/research/provider-reliability`
21. `GET /analytics/research/provider-contract-checks`
22. `GET /analytics/research/ab-testing`
23. `GET /analytics/research/ethics`

## 9. Операційний запуск

## 9.1 Базовий локальний batch для Stage 6

```bash
cd prediction_market_scanner
env DATABASE_URL=sqlite:///artifacts/research/stage5_xplat3.db .venv/bin/python scripts/stage6_track_batch.py
```

Артефакти на виході:
1. `artifacts/research/stage6_batch_<timestamp>.json`
2. `artifacts/research/stage6_export_<timestamp>.csv`

## 9.1.1 Stage 5 batch

```bash
cd prediction_market_scanner
env DATABASE_URL=sqlite:///artifacts/research/stage5_xplat3.db .venv/bin/python scripts/stage5_track_batch.py
```

Артефакти:
1. `artifacts/research/stage5_batch_<timestamp>.json`
2. `artifacts/research/stage5_export_<timestamp>.csv`

## 9.1.2 Stage 7 batch

```bash
cd prediction_market_scanner
env DATABASE_URL=sqlite:///artifacts/research/stage5_xplat3.db .venv/bin/python scripts/stage7_track_batch.py
```

Артефакти:
1. `artifacts/research/stage7_batch_<timestamp>.json`
2. `artifacts/research/stage7_export_<timestamp>.csv`
3. `artifacts/research/stage7_agent_decisions_<timestamp>.jsonl`
4. `artifacts/research/stage7_final_report_<timestamp>.md`

## 9.2 Що перевірити в артефакті

1. `reports.stage6_final_report.final_decision`
2. `reports.stage6_final_report.recommended_action`
3. `reports.stage6_final_report.summary.keep_types`
4. `reports.stage6_final_report.summary.executable_signals_per_day`
5. `reports.stage6_final_report.sections.risk_guardrails.rollback.triggered`

## 9.3 Scheduler (фактичний розклад worker)

1. `sync_all_platforms` — кожні `15 хв`.
2. `detect_duplicates` — кожні `20 хв`.
3. `analyze_rules` — кожні `20 хв`.
4. `detect_divergence` — кожні `20 хв`.
5. `generate_signals` — кожні `20 хв`.
6. `update_watchlists` — щогодини (`:00`).
7. `signal_push` — кожні `30 хв`.
8. `daily_digest` — щодня `09:00`.
9. `quality_snapshot` — щодня `00:10`.
10. `label_signal_history_15m` — кожні `15 хв`.
11. `label_signal_history_30m` — кожні `30 хв`.
12. `label_signal_history_1h` — щогодини (`:12`).
13. `label_signal_history_6h` — кожні `6 год` (`:18`).
14. `label_signal_history_24h` — щодня `01:25`.
15. `label_signal_history_resolution` — щодня `02:10`.
16. `cleanup_old_signals` — щодня `03:00`.
17. `cleanup_signal_history` — щодня `03:20`.
18. `provider_contract_checks` — щогодини (`:40`).

## 9.4 Мінімальний operational checklist

1. `DATABASE_URL`, `REDIS_URL`, `ADMIN_API_KEY` задані.
2. API відповідає на health/admin запити.
3. Worker/beat активні і виконують schedule без backlog.
4. `signal_history` labeling coverage росте.
5. Batch scripts генерують артефакти без помилок.

## 10. Поточний фактичний статус проєкту

Стан на останньому closure run:
1. Stage 6 технічно реалізований повністю.
2. Бізнес-verdict: `NO_GO`.
3. Причина: нестабільний позитивний edge по core типах на поточних historical slices.
4. Stage 7 запущено в `Phase A` (stack scorecard + shadow + final-report контур).

Останні зафіксовані значення:
1. `final_decision=NO_GO`
2. `recommended_action=block_rollout_and_research`
3. `keep_types=0`
4. `executable_signals_per_day=0.0`
5. `rollback_triggered=true`

## 11. Що лишилось доробити

Кодово (невеликі gaps):
1. артефакт `stage6_agent_decisions_<ts>.jsonl`;
2. артефакт `stage6_final_report_<ts>.md`;
3. метрика `agent_decision_coverage`.

Бізнесово (основний блокер):
1. підняти частку стабільних `KEEP` сигналів;
2. дотюнити thresholds на post-cost EV;
3. збільшити якість labeled даних у walk-forward вікнах;
4. окремо вирішити долю Type 3/5 (HF collector або formal architecture limitation).

## 11.1 Чому це не “просто підкрутити пороги”

1. Якщо даних мало або вони noisy, tuning порогів дає short-term cosmetics і поганий out-of-sample.
2. Потрібна дисципліна:
   - walk-forward,
   - embargo,
   - risk guardrails,
   - повторювані batch results.
3. Лише після цього tuning справді підвищує бізнес-якість, а не створює overfit.

## 12. Ризики

1. Data insufficiency для окремих signal types.
2. Sensitivity EV до costs/slippage assumptions.
3. Помилкові висновки при короткому горизонті спостереження.
4. Потенційний overfit без дисципліни walk-forward/embargo.

## 12.1 Технічні ризики

1. Різниця dev/prod оточень (наприклад, `DATABASE_URL` на docker host `db` поза контейнером).
2. API/provider drift (зміни у зовнішніх платформах).
3. Зовнішні rate limits і затримки.
4. Неповна sub-hour гранулярність для окремих сценаріїв.

## 12.2 Бізнес-ризики

1. Негативний EV після costs навіть при високому raw-confidence.
2. Нестабільна executability на різних розмірах позицій.
3. Низька денна кількість реально придатних сигналів.

## 13. Рекомендований план на 2-4 тижні

1. Тиждень 1:
   - додати missing artifacts + coverage metric,
   - стабілізувати щоденний shadow batch.
2. Тиждень 2:
   - threshold sweeps для core типів.
3. Тиждень 3:
   - рішення по Type 3/5 (HF або `INSUFFICIENT_ARCHITECTURE`).
4. Тиждень 4:
   - повторний full Stage 6 batch і новий verdict.

## 13.1 Очікувані KPI на наступний цикл

1. `keep_types >= 1` (мінімум кандидат на `LIMITED_GO`).
2. `rollback_triggered=false` у Stage 6 final report.
3. Зростання labeled coverage у core windows.
4. Позитивний тренд `executable_signals_per_day`.

## 14. Definition of Done (поточна ціль)

1. Є мінімум один policy profile-кандидат на `LIMITED_GO`.
2. Risk guardrails не порушуються.
3. Є повний auditable ланцюг:
   - data -> signal -> execution eval -> policy decision -> governance verdict.
4. Є прозорий executive report з причинами рішення.

## 15. Конфігурація (ключові ENV)

### 15.1 Базові

1. `APP_ENV`, `APP_DEBUG`, `APP_HOST`, `APP_PORT`
2. `DATABASE_URL`
3. `REDIS_URL`
4. `ADMIN_API_KEY`
5. `TELEGRAM_BOT_TOKEN`

### 15.2 Execution + Policy

1. `SIGNAL_EXECUTION_MODEL=v2`
2. `SIGNAL_EXECUTION_V2_HORIZON=6h`
3. `SIGNAL_EXECUTION_V2_LOOKBACK_DAYS`
4. `SIGNAL_EXECUTION_V2_MIN_SAMPLES`
5. `SIGNAL_EXECUTION_POSITION_SIZE_USD`
6. `SIGNAL_EXECUTION_POLYMARKET_MODE=gamma_api|clob_api`
7. `SIGNAL_EXECUTION_POLYMARKET_GAS_FEE_USD`
8. `SIGNAL_EXECUTION_POLYMARKET_BRIDGE_FEE_USD`
9. `AGENT_POLICY_KEEP_EV_THRESHOLD_PCT`
10. `AGENT_POLICY_MODIFY_EV_THRESHOLD_PCT`
11. `AGENT_POLICY_MIN_CONFIDENCE`
12. `AGENT_POLICY_MIN_LIQUIDITY`
13. `AGENT_POLICY_VERSION`

### 15.3 Ranking + Top selection

1. `SIGNAL_TOP_APPENDIX_C_ENABLED`
2. `SIGNAL_RANK_WEIGHT_EDGE`
3. `SIGNAL_RANK_WEIGHT_LIQUIDITY`
4. `SIGNAL_RANK_WEIGHT_EXECUTION_SAFETY`
5. `SIGNAL_RANK_WEIGHT_FRESHNESS`
6. `SIGNAL_RANK_WEIGHT_CONFIDENCE`
7. `SIGNAL_TOP_MIN_SCORE_TOTAL`
8. `SIGNAL_TOP_MIN_UTILITY_SCORE`

### 15.4 Research stack

1. `RESEARCH_TRACKING_ENABLED`
2. `RESEARCH_EXPERIMENT_REGISTRY_PATH`
3. `RESEARCH_MLFLOW_ENABLED`
4. `RESEARCH_MLFLOW_TRACKING_URI`
5. `RESEARCH_MLFLOW_EXPERIMENT_NAME`
6. `RESEARCH_GREAT_EXPECTATIONS_ENABLED`
7. `RESEARCH_AB_ENABLED`
8. `RESEARCH_AB_CONTROL_SHARE`
9. `RESEARCH_ETHICS_DISCLAIMER_TEXT`

## 16. Troubleshooting

1. Проблема: `DATABASE_URL` на `db` не резолвиться локально.
   - Рішення: запускати batch з локальним SQLite або з shell всередині docker network.
2. Проблема: worker jobs не оновлюють labels.
   - Перевірити, чи запущені `worker` і `beat`, і чи є записи старші за horizon.
3. Проблема: завжди `NO_GO`.
   - Перевірити labeled coverage, position-size assumptions, і чи нема rollback trigger через негативний mean return.
4. Проблема: мало сигналів у топі.
   - Перевірити пороги top selection і rules-risk gating, але тільки з контролем out-of-sample.
