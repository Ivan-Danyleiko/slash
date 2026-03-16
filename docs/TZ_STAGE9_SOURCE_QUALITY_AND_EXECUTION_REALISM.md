# ТЗ Stage 9: Source Quality, Directional Labeling, Execution Realism

## 1. Контекст

Stage 8 стабілізував policy/gates, але залишились системні обмеження:
1. `metaculus_median` часто `None` через неповний lookup.
2. `edge_after_costs` може занулятись при low sample size.
3. Немає `signal_direction`, через що `resolved_success` семантично неточний.
4. Немає реального spread/depth для Polymarket execution costs.
5. Manifold дає шум (resolved / нецільові ринки).
6. Consensus matching занадто м’який (ризик false match).

Stage 9 закриває ці проблеми на рівні даних, моделей і execution-моделі.

## 2. Цілі Stage 9

1. Підвищити якість external consensus до стабільного 3-source режиму.
2. Зробити success-лейбли напрямними (direction-aware), а не односторонніми.
3. Додати реалістичний spread/fee input для execution cost.
4. Зменшити шум джерел (особливо Manifold) до production-рівня.
5. Підготувати основу для реального profit-validation у Stage 10.

## 3. In Scope / Out of Scope

### 3.1 In Scope

1. Fix Metaculus consensus (search -> detail).
2. Tight title/event matching (порог/нормалізація/анти-false-match).
3. `signal_direction` у `Signal` + `SignalHistory`.
4. Direction-aware `resolved_success`.
5. Execution fallback при `n < min_samples` (не нульовий EV).
6. Polymarket CLOB spread ingestion (best bid/ask; feature-flag).
7. Manifold collector filters (`isResolved`, market type).
8. Новий collector Kalshi (baseline REST).
9. Volume-weighted consensus замість рівноважного.
10. Void/N/A та dispute-aware labeling.
11. Нові data-quality/calibration метрики та acceptance checks.

### 3.2 Out of Scope

1. Автоторгівля реальними коштами.
2. Новинний/соціальний LLM-sentiment layer.
3. Повний order execution engine.

## 4. Архітектурні зміни

```text
Collectors (Manifold / Metaculus / Polymarket / Kalshi)
        |
        v
Normalized Market + Snapshot (+ spread fields)
        |
        v
Signal Engine (direction-aware) -> SignalHistory (direction-aware labels)
        |
        v
Stage7 Tools (improved consensus matching + Metaculus detail)
        |
        v
Stage8/9 Shadow & Final Report (quality + profitability proof)
```

## 5. Модель даних (обов'язково)

## 5.1 Нові поля

1. `signals.signal_direction` (`YES` | `NO`, nullable=False для нових записів).
2. `signal_history.signal_direction` (`YES` | `NO`, nullable=True для legacy).
3. `markets.spread_cents` (`float`, nullable=True).
4. `markets.best_bid_yes` (`float`, nullable=True).
5. `markets.best_ask_yes` (`float`, nullable=True).
6. `markets.execution_source` (`gamma_api|clob_api|kalshi_api`, nullable=True).
7. `markets.is_neg_risk` (`bool`, nullable=True).
8. `markets.open_interest` (`float`, nullable=True).
9. `markets.notional_value_dollars` (`float`, nullable=True).
10. `markets.previous_yes_bid` (`float`, nullable=True).
11. `signal_history.resolved_outcome` (`YES|NO|VOID|PENDING`, nullable=True).

## 5.2 Міграції

1. `0012_stage9_direction_and_execution_fields.py`:
   - додає поля з п.5.1;
   - індекси: `ix_market_execution_source`, `ix_signal_direction`, `ix_signal_history_direction`, `ix_market_neg_risk`.
2. Backfill migration script:
   - `signal_direction` для старих сигналів: `DIVERGENCE/ARBITRAGE -> YES` за замовчуванням, `RULES_RISK` через heuristics (`metadata_json`), інакше `NULL`.

## 6. Логіка сигналів і лейблінгу

## 6.1 Signal direction

1. Engine при створенні сигналу зобов’язаний проставляти `signal_direction`.
2. Правила:
   - `DIVERGENCE`: за знаком expected move.
   - `ARBITRAGE_CANDIDATE`: за side з execution analysis.
   - `RULES_RISK`: direction із rule-гіпотези (up/down), інакше `SKIP` для accuracy-critical path.

## 6.2 Direction-aware resolved_success

Замість:
`resolved_probability > probability_at_signal`

Нова логіка:
1. якщо `signal_direction == YES`: success, коли `resolved_probability > probability_at_signal`;
2. якщо `signal_direction == NO`: success, коли `resolved_probability < probability_at_signal`;
3. якщо direction відсутній: `resolved_success = NULL`, `missing_label_reason="direction_missing"`.
4. якщо `resolved_outcome == VOID`: `resolved_success = NULL`, `missing_label_reason="void_resolution"` і запис виключається з training/evaluation.
5. якщо `platform=POLYMARKET` і `oracle_dispute_flag=true`: `resolved_success = NULL`, `missing_label_reason="oracle_dispute_risk"` і запис виключається з training/evaluation.

## 7. Consensus & matching (Stage7 tools)

## 7.1 Metaculus detail call

1. Після search-match обов’язковий detail GET:
   `/api2/questions/{id}/`
2. Джерело медіани:
   - `community_prediction.full.q2` (або fallback на доступні median fields).
3. Якщо detail недоступний:
   - reason code: `metaculus_detail_unavailable`.

## 7.2 Matching hardening

1. Підняти поріг match: `0.25 -> 0.40`.
2. Додати canonical normalization:
   - lower, punctuation strip, number/date normalization, asset aliases (btc/bitcoin).
3. Додати hard guard:
   - якщо category/assets/conflict -> reject match (`cross_event_mismatch`).

## 7.3 Volume-weighted consensus

1. Формула:
   - `P_consensus = (w_poly*P_poly + w_kalshi*P_kalshi + w_manifold*P_manifold + w_meta*P_meta) / (w_poly + w_kalshi + w_manifold + w_meta)`
2. Ваги:
   - якщо є `volume/open_interest`: `w = max(1.0, liquidity_proxy)`.
   - для Metaculus (без реального volume) фіксована вага `w_meta = 0.10`.
3. Якщо доступно лише 2 джерела:
   - консенсус рахується, але reason code: `consensus_two_source_mode`.

## 8. Collectors

## 8.1 Manifold

1. Фільтрувати `isResolved=true` на ingest.
2. Пропускати нецільові mechanism/type (не бінарні для execution research).
3. Категорію нормалізувати через mapping, а не `groupSlugs[0]` як є.
4. Записувати `open_interest`/еквівалент у normalized model (якщо доступно).

## 8.2 Polymarket CLOB (feature-flag)

1. Нові env:
   - `POLYMARKET_CLOB_ENABLED=false`
   - `POLYMARKET_CLOB_API_KEY` (за потреби)
2. При enabled:
   - тягнути best bid/ask YES;
   - писати `best_bid_yes`, `best_ask_yes`, `spread_cents`.
   - тягнути `neg_risk` і писати `is_neg_risk`.
3. NegRisk profile:
   - для `is_neg_risk=true` використовувати окремий cost/impact profile (менший market impact, окремі guardrails).
4. Fallback:
   - якщо CLOB недоступний -> gamma mode + reason `clob_unavailable_fallback_gamma`.

## 8.3 Kalshi (новий collector)

1. Додати `app/services/collectors/kalshi.py`.
2. Додати в `CollectorSyncService` + platform registry.
3. Мінімальні поля:
   - id, title, status, yes bid/ask, volume/liquidity, resolution_time, rules.
4. Додатково обов’язково:
   - `open_interest_fp` -> `open_interest`,
   - `notional_value_dollars`,
   - `previous_yes_bid_dollars` -> `previous_yes_bid`,
   - `settlement_timer_seconds`.
5. Historical bootstrap:
   - initial sync починається з `GET /historical/cutoff`,
   - окремий хендлер для historical/settled markets.

## 9. Execution model

## 9.1 Empirical EV fallback (low samples)

Коли `n < min_samples`:
1. не обнуляти EV;
2. використовувати shrinkage fallback:
   - `ev_blend = w_empirical * ev_empirical + (1-w_empirical) * ev_prior`
   - `w_empirical = min(1.0, n / min_samples)`
3. `ev_prior` з category baseline (конфіг).
   - `ev_prior = mean_edge_historical[category][ttr_bucket]`,
   - `ttr_bucket in {<1d, 1-7d, 7-30d, >30d}`,
   - fallback bucket при пустій історії: `category_global_prior`.
4. Маркер: `execution_assumptions_version="v2_empirical_shrinkage_fallback"`.

5. Leak prevention rule (обов'язково):
   - для навчання/оцінки `ExecutionSimulatorV2` заборонено використовувати будь-які post-resolution поля (`resolved_probability`, future snapshots, outcome/result columns) у feature space.

## 9.2 Cost realism

1. Polymarket fee profile:
   - стандартний режим: `fee = 0` (non-DCM, без dynamic fee-маркетів),
   - DCM режим: `taker_fee_bps=10`, `maker_rebate_bps=10` (через feature flag profile),
   - dynamic crypto-15m: окремий `polymarket_dynamic_fee_profile`.
2. Kalshi fee profile:
   - `kalshi_taker_fee(price) = 0.07 * price * (1 - price)`,
   - maker: `0%` (default) або `0.25%` election-profile.
3. Manifold fee profile:
   - `0` реальних fees (play money; research-only execution proxy).
4. Якщо є CLOB spread:
   - spread cost = `(ask - bid)/2`.
5. Інакше gamma fallback spread з conservative default per category.
6. Price velocity warning:
   - використовувати `previous_yes_bid`/snapshot deltas як `information_arrival_warning` у soft/warning gates.

## 10. API та звітність

Додати endpoints:
1. `GET /analytics/research/stage9/consensus-quality`
2. `GET /analytics/research/stage9/directional-labeling`
3. `GET /analytics/research/stage9/execution-realism`
4. `POST /analytics/research/stage9/track`

## 11. Нові метрики якості

1. `metaculus_median_fill_rate` (ціль >= 70% для matched events).
2. `consensus_3source_share` (ціль >= 50% core categories).
3. `direction_labeled_share` (ціль >= 95% нових сигналів).
4. `direction_missing_label_share` (ціль <= 5%).
5. `non_zero_edge_share` (ціль >= 60% серед candidate set).
6. `clob_spread_coverage` (для Polymarket when enabled, ціль >= 60%).
7. `false_match_rate` (manual sampled audit, ціль <= 10%).
8. `brier_skill_score_per_category` (BSS).
9. `ece_per_category` (Expected Calibration Error).
10. `longshot_bias_error_0_15pct`.
11. `precision_at_10`, `precision_at_25`, `precision_at_50`.
12. `auprc`.
13. `void_outcome_share` (monitoring-only, не входить у precision denominator).

## 12. Acceptance Criteria Stage 9

Stage 9 вважається завершеним, якщо:
1. `analyze_markets` стабільно `SUCCESS` (24h без FK/error spikes).
2. `metaculus_median_fill_rate >= 0.70` на matched вибірці.
   - Якщо `METACULUS_API_TOKEN` відсутній: check переходить у `informational` і не блокує final gate.
3. `direction_labeled_share >= 0.95` для сигналів після релізу.
4. `non_zero_edge_share >= 0.60` (edge gate не «нульова стіна»).
5. CLOB mode (якщо enabled) дає ненульовий `spread_cents` для >=60% активних Polymarket.
6. Stage8 final decision більше не впирається масово в `edge_after_costs=0`.
7. Нема regression у Stage7/Stage8 тестах + нові Stage9 тести зелені.
8. `consensus_3source_share >= 0.50` або формально зафіксований `two_source_mode` з причинами.
9. `precision@25 >= stage8_baseline_precision@25` на тому ж періоді.

## 13. План реалізації

### Phase A (Тиждень 1): Data Contract
1. Міграції полів (direction + execution).
2. Engine direction assignment.
3. Direction-aware labeling.

### Phase B (Тиждень 2): Consensus Hardening
1. Metaculus detail fetch.
2. Matching threshold + normalization.
3. Метрики consensus quality.

### Phase C (Тиждень 3): Execution Realism
1. Shrinkage fallback для EV.
2. Polymarket CLOB ingestion (flagged).
3. Manifold filters.

### Phase D (Тиждень 4): Expansion + Validation
1. Kalshi collector (baseline).
2. Stage9 reports/endpoints.
3. Final batch + acceptance gate.

## 14. Тести

1. Unit:
   - direction assignment;
   - direction-aware resolved_success;
   - Metaculus detail parser;
   - match threshold/normalization.
   - kalshi fee formula;
   - volume-weighted consensus;
   - VOID/dispute exclusion logic.
2. Integration:
   - collector sync (4 платформи);
   - stage7 consensus tool contract;
   - execution fallback for low samples.
   - historical cutoff flow for Kalshi.
3. Regression:
   - Stage7/Stage8 existing test suite.

## 15. Ризики та контроль

1. API drift (Metaculus/Kalshi/Polymarket):
   - versioned adapters + fallback reason codes.
2. Неповні historical дані:
   - clear `DATA_PENDING` gate, без fake-positive GO.
3. Overfitting на нових порогах:
   - only shadow-first rollout + locked evaluation windows.

## 16. Deliverables

1. `docs/TZ_STAGE9_SOURCE_QUALITY_AND_EXECUTION_REALISM.md` (цей документ).
2. Міграції `0012_*`.
3. Код collector/tool/execution updates.
4. `artifacts/research/stage9_batch_<ts>.json`
5. `artifacts/research/stage9_export_<ts>.csv`
6. `docs/TZ_STAGE9_STATUS.md`
