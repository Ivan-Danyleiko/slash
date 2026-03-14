# TZ Stage 6 Status

## Поточний статус

- Загальний прогрес: `Core implementation complete, rollout validation in progress`
- Дата старту: `2026-03-13`
- Джерело вимог: `TZ_STAGE6_AGENT_DECISION_AND_PROFIT_VALIDATION.md`

## Чекліст виконання

1. ExecutionSimulatorV2 (empirical EV) — `DONE (shadow-ready + fallback)`
2. Agent Decision Layer (deterministic policy engine) — `DONE (v1 policy service)`
3. Agent decisions endpoint `/analytics/research/agent-decisions` — `DONE`
4. Platform-aware execution profiles — `DONE (polymarket/manifold/default)`
5. Appendix C ranking 1-в-1 — `DONE (prod ranker + research shadow compare)`
6. 15m/30m horizons in labeling/analytics — `DONE (subhour jobs + scheduler + admin + analytics coverage)`
7. Walk-forward with embargo — `DONE (research endpoint + tracking)`
8. Type 3/5 dedicated runs with explicit verdict — `DONE (stage6 type35 report + track endpoint)`
9. Tiered circuit breakers and statistical rollback trigger — `DONE (stage6 risk guardrails report + track endpoint)`
10. GO/LIMITED_GO/NO_GO gate report — `DONE (stage6 governance report + track endpoint)`

## Ітерація 1 (сьогодні)

Виконано:
1. Додано `SIGNAL_EXECUTION_MODEL=v2` та Stage 6 execution/agent env-параметри.
2. Додано `ExecutionSimulatorV2` (empirical EV from `signal_history`) з fallback на v1 при low sample size.
3. Додано `app/services/agent/policy.py` (deterministic KEEP/MODIFY/REMOVE/SKIP).
4. Додано endpoint `GET /analytics/research/agent-decisions`.
5. Додано unit tests: `tests/test_stage6_execution_and_agent.py`.
6. Прогін тестів: `15 passed`.
7. Додано walk-forward research:
   - `GET /analytics/research/walkforward`
   - `POST /analytics/research/walkforward/track`
   - підтримка `embargo_hours`, `min_samples_per_window`, bootstrap CI, `low_confidence`.
8. Розширено signal lifetime:
   - 15m/30m горизонти через `MarketSnapshot` (де доступні),
   - статус `INSUFFICIENT_ARCHITECTURE` для архітектурно-обмежених типів при низькому sub-hour coverage.
9. Додано тести Stage 6 Phase B:
   - `tests/test_stage6_walkforward_and_lifetime.py`.
10. Останній прогін: `7 passed` (stage6 + lifetime suite).
11. Додано sub-hour labeling pipeline:
   - jobs: `label_signal_history_15m_job`, `label_signal_history_30m_job` (store в `signal_history.simulated_trade`),
   - celery tasks + schedules (15m/30m),
   - admin endpoints: `/admin/label-signal-history/15m`, `/admin/label-signal-history/30m`,
   - `GET /analytics/signal-history` тепер показує coverage для `15m`/`30m`.
12. Приведено ranking до Appendix C:
   - `score_total` в `SignalEngine._score_breakdown` тепер: `0.35*edge + 0.25*liquidity + 0.20*execution_safety + 0.10*freshness + 0.10*confidence - risk_penalties`.
   - `select_top_signals` підтримує `SIGNAL_TOP_APPENDIX_C_ENABLED=true`.
   - `ranking_research` тепер має shadow-порівняння формул: `legacy_rank_score` vs `appendix_c_score`.
13. Останній прогін після Appendix C змін: `21 passed`.
14. Додано Stage 6 governance gate:
   - service: `build_stage6_governance_report`,
   - endpoints: `GET/POST /analytics/research/stage6-governance(/track)`,
   - рішення: `GO/LIMITED_GO/NO_GO`,
   - вбудовані overfit sanity checks (`EV>15%`, `hit_rate>63%`, `sharpe>2.5 && n<500`).
15. Додано Stage 6 risk guardrails:
   - service: `build_stage6_risk_guardrails_report`,
   - endpoints: `GET/POST /analytics/research/stage6-risk-guardrails(/track)`,
   - tiered circuit breaker: `SOFT/HARD/PANIC`,
   - statistical rollback: one-sided test `mean_return < 0`, `n>=30`, `p < threshold`, cooldown параметризовано.
16. Додано dedicated Type 3/5 report:
   - service: `build_stage6_type35_report`,
   - endpoints: `GET/POST /analytics/research/stage6-type35(/track)`,
   - verdicts: `KEEP/MODIFY/REMOVE/INSUFFICIENT_DATA/INSUFFICIENT_ARCHITECTURE`,
   - маппінг для поточної таксономії: Type 3 -> `LIQUIDITY_RISK`, Type 5 -> `WEIRD_MARKET`.
17. Додано єдиний Stage 6 final report:
   - service: `build_stage6_final_report`,
   - endpoints: `GET/POST /analytics/research/stage6-final-report(/track)`,
   - агрегує `governance + risk_guardrails + type35`,
   - формує фінальний rollout verdict + recommended action в одному артефакті.
18. Додано batch automation для Stage 6:
   - script: `scripts/stage6_track_batch.py`,
   - артефакти: `artifacts/research/stage6_batch_<timestamp>.json`, `artifacts/research/stage6_export_<timestamp>.csv`,
   - smoke run: `stage6_batch_20260314_065948.json` + `stage6_export_20260314_065948.csv`.

## Ризики

1. Низька вибірка для окремих signal types може активувати fallback до v1.
2. EV V2 чутливий до якості label coverage (особливо 6h/24h).
3. Для Type 3/5 без high-frequency collector можливий verdict `INSUFFICIENT_ARCHITECTURE`.

## Фінальний batch (closure run)

- Запуск: `env DATABASE_URL=sqlite:///artifacts/research/stage5_xplat3.db .venv/bin/python scripts/stage6_track_batch.py`
- Артефакти:
  - `artifacts/research/stage6_batch_20260314_070431.json`
  - `artifacts/research/stage6_export_20260314_070431.csv`
- Підсумок:
  - `final_decision=NO_GO`
  - `recommended_action=block_rollout_and_research`
  - `governance_decision=NO_GO`
  - `circuit_breaker_level=OK`
  - `rollback_triggered=true`
  - `keep_types=0`
  - `executable_signals_per_day=0.0`
  - `type35_decision_counts={"INSUFFICIENT_DATA": 2}`
