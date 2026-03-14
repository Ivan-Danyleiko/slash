# ТЗ Stage 6: Agent Decision Layer + Profit Validation (Revised)

## 1. Мета етапу

Перевести систему зі стану `research PASS` у стан керованої експлуатації, де рішення по сигналам:
1. приймаються policy-агентом;
2. мають статистично валідну EV-оцінку;
3. проходять risk-guardrails;
4. підтверджуються walk-forward + shadow validation.

## 2. Scope

### In scope
1. Agent Decision Layer над 3 джерелами проекту.
2. Перепис EV-моделі: `ExecutionSimulatorV2` (empirical EV from labeled data).
3. Platform-aware execution profiles.
4. Walk-forward validation з embargo, bootstrap fallback.
5. Unified ranking за Appendix C 1-в-1.
6. Controlled rollout із GO/LIMITED_GO/NO_GO.

### Scope clarification (обов'язково)
1. Це не cross-platform arbitrage.
2. Це directional signal trading:
   - `Polymarket` — execution venue;
   - `Manifold` — read-only information anchor;
   - `Metaculus` — read-only calibrated prior.

### Out of scope
1. Fully autonomous real-money execution без manual override.
2. HFT (<1m).
3. News/social/on-chain контекст (перенесено в Stage 7+).

## 3. Тип агента

Stage 6 агент = **deterministic policy engine** (versioned rules + calibrated thresholds).

Заборони Stage 6:
1. ML-класифікатор як primary decision engine (ризик overfit при поточному sample size).
2. LLM для probability/EV calculation.

LLM дозволено лише як optional qualitative helper для текстових flags (`rules ambiguity`, `market clarity`) без впливу на фінальний EV.

## 4. Архітектура

## 4.1 Шари
1. Data Layer: `signal_history`, labeling, backfill.
2. Research Layer: analytics, optimization, readiness.
3. Agent Layer:
   - feature collector;
   - policy evaluator;
   - risk gate;
   - explanation renderer.
4. Delivery Layer: API/Telegram + observability.

## 4.2 Agent Decision Contract

```json
{
  "signal_id": 123,
  "decision": "KEEP|MODIFY|REMOVE|SKIP",
  "confidence": 0.0,
  "expected_ev_pct": 0.0,
  "expected_costs_pct": 0.0,
  "utility_score": 0.0,
  "risk_flags": ["low_liquidity"],
  "assumptions_version": "ev_v2_empirical",
  "policy_version": "policy_v1",
  "created_at": "2026-03-13T00:00:00Z"
}
```

## 5. ExecutionSimulatorV2 (критичний prerequisite)

Без цього пункту Stage 6 не стартує.

## 5.1 Формула EV

Використовувати empirical EV на labeled outcomes:

```text
EV_after_costs = P_win * AvgWin - (1 - P_win) * AvgLoss - Costs
```

Для market-price sanity check допускається додатковий контроль:

```text
EV_price = (P_true / P_market) - 1
```

де:
1. `P_true` — estimate з cross-platform consensus або signal-type posterior.
2. `P_market` — execution-side price (ask/best executable).

## 5.2 Platform-aware execution profiles

1. `POLYMARKET`
   - mode: `gamma_api | clob_api`;
   - costs: spread + fee + slippage + bridge amortization.
2. `MANIFOLD`
   - mode: AMM proxy;
   - execution = research-only (no real-money claim).
3. `METACULUS`
   - mode: reference-only;
   - execution disabled.

## 5.3 Position-size-aware EV thresholds

1. `size < 100 USD` -> `EV_min = 5%`.
2. `100-500 USD` -> `EV_min = 3%`.
3. `> 500 USD` -> `EV_min = 2%`.

## 6. Ranking formula Appendix C (1-в-1)

```text
score_total =
  0.35 * edge
+ 0.25 * liquidity
+ 0.20 * execution_safety
+ 0.10 * freshness
+ 0.10 * confidence
- risk_penalties
```

## 6.1 Field mapping (обов'язково)
1. `edge` <- `slippage_adjusted_edge_v2`.
2. `execution_safety` <- `utility_score`.
3. `freshness` <- `1 - min(1.0, hours_since_created / 24.0)`.
4. `liquidity` <- `liquidity_score`.
5. `confidence` <- `confidence_score`.

Старий ранкер лишається як rollback feature flag.

## 7. Data requirements

1. `rows_total >= 2000` labeled rolling.
2. Мінімум `500` labeled samples для core types.
3. Горизонти: `15m`, `30m`, `1h`, `6h`, `24h`, `resolution`.
4. `missing_label_reason` обов'язково для unlabeled.

## 8. Walk-forward protocol (обов'язково)

## 8.1 Мінімальні параметри
1. Мінімум `100 labeled samples per type per window`.
2. Якщо `<100`: використати bootstrap CI та flag `low_confidence`.

## 8.2 Embargo проти leakage
1. `embargo >= max(labeling_horizon_used, 24h)`.
2. Для 24h labeling: розрив train/test >= 48h.
3. Для 6h labeling: розрив train/test >= 12h.

## 8.3 Type 3/5 rule
Якщо без high-frequency collector (1-2 min polling top-N):
1. verdict = `INSUFFICIENT_ARCHITECTURE`;
2. не підміняти це на `INSUFFICIENT_DATA`.

## 9. План реалізації

### Phase A (Week 1)
1. `ExecutionSimulatorV2` + platform profiles.
2. Agent module (`policy engine`, not ML).
3. Endpoint: `GET /analytics/research/agent-decisions`.

Deliverable:
1. V2 EV live in shadow for 100% new signals.

### Phase B (Week 2)
1. Threshold optimization (vectorbt/fallback).
2. 15m/30m lifetime.
3. Dedicated runs for Type 3/5.

Deliverable:
1. Updated thresholds + explicit verdicts for Type 3/5.

### Phase C (Week 3)
1. Walk-forward with embargo.
2. Monte Carlo 1000 sims.
3. **Shadow policy comparison (14 days)** замість прямого A/B для policy.
4. Existing user-level A/B лишається тільки для delivery UX metrics.

Deliverable:
1. Policy comparison report with statistical confidence.

### Phase D (Week 4)
1. Staged rollout.
2. Tiered circuit breakers.
3. Final decision gate.

Deliverable:
1. `GO | LIMITED_GO | NO_GO` with explicit criteria.

## 10. Acceptance criteria

## 10.1 Технічні
1. `agent_decision_coverage >= 95%`.
2. `provider_contract_checks` no blocking failures for 7 days.
3. `data_quality_blocking_gates = PASS`.
4. Appendix C rank in shadow >=14 days and stable.

## 10.2 Бізнесові
1. >=2 signal types in `KEEP`.
2. Portfolio `sharpe_like > 1.0`.
3. `risk_of_ruin < 10%`.
4. `executable_signals_per_day >= 5`.
5. EV threshold by position size (§5.3).

## 10.3 Анти-overfit sanity checks
Якщо будь-яке виконується -> `SUSPICIOUS_OVERFIT` + manual review:
1. `EV_backtest > 15%`.
2. `hit_rate > 63%`.
3. `sharpe_like > 2.5` при `n < 500`.

## 11. Rollout decision gate

1. `GO`
   - всі §10.2 виконані;
   - немає blocking risk flags;
   - walk-forward consistent.
2. `LIMITED_GO`
   - >=1 KEEP;
   - EV > 1%;
   - sharpe_like > 0.5;
   - risk_of_ruin < 15%;
   - rollout <=20% traffic.
3. `NO_GO`
   - 0 KEEP або EV <= 0 або risk_of_ruin > 20%.

## 12. Guardrails & circuit breakers

## 12.1 Statistical rollback trigger
Rollback trigger:
1. `n_labeled >= 30`;
2. one-sided t-test supports `EV < 0` with `p < 0.10`.

Cooldown:
1. `rollback_cooldown = 7 days`.

## 12.2 Tiered breakers
1. Level 1 (soft): daily_loss > 1% NAV -> reduce size 50% + alert.
2. Level 2 (hard): daily_loss > 2% NAV -> halt new entries, manual reset required.
3. Level 3 (panic): daily_loss > 5% NAV -> emergency flatten / no-new-risk mode.

## 12.3 Capital lock-up awareness
Для Polymarket враховувати non-instant liquidity (bridge/withdraw latency) в risk budget.

## 13. Build-vs-Buy stack

## 13.1 Must-have
1. `vectorbt`
2. `quantstats`
3. `mlflow` (або JSONL fallback)
4. `great_expectations`
5. `py-clob-client` (advanced mode)

## 13.2 Compatibility mode
1. `orderbook_mode = gamma_api | clob_api`.
2. В звітах: `stack_mode = baseline | advanced`.

## 14. Deliverables

1. `docs/TZ_STAGE6_AGENT_DECISION_AND_PROFIT_VALIDATION.md`.
2. `docs/TZ_STAGE6_STATUS.md`.
3. `artifacts/research/stage6_batch_<timestamp>.json`.
4. `artifacts/research/stage6_walkforward_<timestamp>.csv`.
5. `artifacts/research/stage6_agent_decisions_<timestamp>.jsonl`.
6. `artifacts/research/stage6_final_report_<timestamp>.md`.

## 15. Definition of Done

Stage 6 вважається закритим, якщо:
1. `ExecutionSimulatorV2` у продакшн-шадоу, v1 лишається лише fallback.
2. Appendix C ranking увімкнено й валідувано.
3. Є formal verdict `GO/LIMITED_GO/NO_GO` за §11.
4. Всі рішення агента відтворювані (`policy_version`, `assumptions_version`, config snapshot).
