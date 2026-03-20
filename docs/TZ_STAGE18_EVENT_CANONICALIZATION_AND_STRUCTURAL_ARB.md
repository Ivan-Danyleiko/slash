# ТЗ Stage 18: Event Canonicalization, Topic Weights, Structural Arb

## 1. Контекст

Поточний стек добре генерує сигнали Stage7/Stage17, але втрачає частину ринкових можливостей через:
1. неповний cross-platform matching (пари замість стабільних event-груп),
2. однакові ваги платформ у divergence,
3. відсутність basket-логіки для multi-outcome структурного арбітражу,
4. передчасну спробу microstructure без повноцінного LIVE execution.

Ключове рішення Stage 18: спочатку підняти recall/precision сигналів (A+B+C), і лише потім готувати D (CLOB microstructure) як окремий трек після Stage11 LIVE.

## 2. Цілі Stage 18

1. Підвищити recall релевантних крос-платформних пар/груп для DIVERGENCE.
2. Додати feedback-loop якості джерел per topic/category у скорингу.
3. Додати новий клас сигналів: structural basket arb (multi-outcome underround/overround).
4. Не ламати поточні Stage7/Stage17 пайплайни та risk-guardrails.

## 3. Scope (що робимо)

### In Scope

1. Event canonicalization layer (без LLM, deterministic).
2. Topic reliability weights (platform x category) на базі resolved history.
3. Structural arb detector для multi-outcome ринків.
4. Аналітика/метрики Stage18 + shadow-порівняння проти baseline Stage7/17.

### Out of Scope

1. Kalshi execution та Kalshi-specific trading flow.
2. CLOB microstructure execution (post-only, queue position, maker rebates) у production.
3. Нові external LLM framework-и.

## 4. Обмеження та платформи

1. Основний execution venue лишається поточний (Stage11/Polymarket CLOB).
2. Kalshi лишається optional data connector, але Stage18 acceptance не залежить від Kalshi.
3. Якщо `KALSHI_ENABLED=false`, весь Stage18 має проходити на `POLYMARKET + MANIFOLD + METACULUS`.

## 5. Архітектура Stage 18

```text
[Collectors Sync]
    -> [Event Canonicalizer]
    -> [Event Graph / Event Group IDs]
    -> [Signal Engine]
         - Divergence (weighted by platform-topic reliability)
         - Structural Basket Arb (multi-outcome)
    -> [Stage7 Verification]
    -> [Stage17 Tail (existing, unchanged core)]
    -> [Shadow Metrics + Final Stage18 Report]
```

## 6. Workstream A: Event Canonicalization (Priority #1)

### 6.1 Проблема

Збіг по title similarity не використовує стабільні ідентифікатори з payload (`conditionId`, `event_ticker`, інші external IDs), через що:
1. true matches губляться,
2. різні платформи для однієї події не групуються в єдиний event graph.

### 6.2 Рішення

1. Додати canonical key builder:
   - `event_key_primary` з payload IDs (якщо є),
   - `event_key_secondary` з нормалізованого title + date hints + entity aliases.
2. Під час sync формувати `event_group_id` (детерміністично).
3. У divergence/cross-platform інструментах використовувати:
   - спочатку `event_group_id`,
   - тільки fallback на fuzzy title.

### 6.3 Мінімальна схема даних

1. `markets.event_group_id` (nullable, indexed).
2. `markets.event_key_version` (для replayable міграцій канонікалізації).
3. `markets.event_key_confidence` (0..1).

### 6.4 Acceptance для A

1. `event_group_coverage >= 0.70` для активних ринків.
2. `cross_platform_match_recall +20%` проти baseline title-only.
3. `false_grouping_rate <= 5%` на ручній валідаційній вибірці.

## 7. Workstream B: Topic Reliability Weights (Priority #2)

### 7.1 Проблема

Скоринг джерел зараз здебільшого технічний (uptime/error), але не predictive quality per topic.

### 7.2 Рішення

1. Будуємо матрицю `weight(platform, category)` з resolved rows:
   - вхід: `SignalHistory` + `resolved_success` + `platform` + `category`,
   - мінімум для стабільної оцінки: `min_n = 100` (конфіг).
2. Формуємо вагу:
   - якщо `n < min_n`, беремо shrinkage до глобальної ваги,
   - якщо `n >= min_n`, застосовуємо topic-specific weight.
3. У DivergenceDetector замінюємо рівні ваги на topic-aware weighted divergence.

### 7.3 Формула (мінімум)

1. `raw_quality = hit_rate_or_calibration_score(platform, category)`.
2. `w = shrink(n) * raw_quality + (1 - shrink(n)) * global_platform_quality`.
3. Нормалізація ваг у межах [0.1, 1.0] з подальшим scaling.

### 7.4 Acceptance для B

1. На shadow періоді `precision@KEEP` не гірше baseline.
2. `weighted_divergence_hit_rate >= baseline + 5%` на OOS set.
3. Мінімум 1 категорія core (`crypto|sports|politics|finance`) показує покращення.

## 8. Workstream C: Multi-Outcome Structural Arb (Priority #3)

### 8.1 Проблема

Поточний ARBITRAGE сигнал працює як single-market momentum/uncertainty; немає basket логіки для взаємовиключних outcomes.

### 8.2 Рішення

1. Додати grouping outcomes в межах `event_group_id`.
2. Для групи outcome prices рахуємо:
   - `sum_prob = sum(p_i)`,
   - `underround = 1 - sum_prob` (арб можливість),
   - `overround = sum_prob - 1` (інфо-сигнал/ризик).
3. Сигнал structural arb:
   - `STRUCTURAL_ARB_CANDIDATE` (новий signal_type),
   - direction/basket legs з risk cap по сумарному notional.

### 8.3 Guardrails

1. Працює тільки для груп де:
   - outcomes взаємовиключні,
   - достатня ліквідність по всіх legs,
   - дедлайн <= конфіг ліміту.
2. Для `is_neg_risk=true` окремий execution cost multiplier.
3. Якщо будь-який leg не виконує risk filter -> весь basket `SKIP`.

### 8.4 Acceptance для C

1. `structural_arb_candidates_per_day >= 5` на активному ринку (або інший погоджений min).
2. Shadow basket PnL після costs не гірший за Stage7 baseline.
3. `basket_fill_feasibility >= 0.60` (оцінка виконуваності всіх legs).

## 9. Workstream D: CLOB Microstructure (Priority #4, Deferred)

### 9.1 Статус

Не блокер Stage18. Готуємо специфікацію, але не включаємо в Stage18 GO criteria.

### 9.2 Preconditions

1. Stage11 LIVE mode активний.
2. `order_type` і post-only semantics підтримані в order model.
3. Реальні latency/fill метрики зібрані мінімум за 30 днів.

### 9.3 Що готуємо у Stage18

1. Tech design doc для microstructure signals (depth imbalance, spread reversion).
2. Схему полів для order flags (post_only/ioc/fok).
3. Порожній adapter contract + тестові фікстури.

## 10. Конфіг (нові параметри)

1. `STAGE18_EVENT_CANON_ENABLED=true`
2. `STAGE18_EVENT_GROUP_MIN_CONFIDENCE=0.60`
3. `STAGE18_TOPIC_WEIGHTS_ENABLED=true`
4. `STAGE18_TOPIC_WEIGHTS_MIN_N=100`
5. `STAGE18_STRUCTURAL_ARB_ENABLED=true`
6. `STAGE18_STRUCTURAL_ARB_MIN_UNDERROUND=0.015`
7. `STAGE18_STRUCTURAL_ARB_MAX_GROUP_SIZE=8`
8. `STAGE18_REQUIRE_KALSHI=false`

## 11. API/Artifacts

### 11.1 Нові звіти

1. `GET /analytics/research/stage18/event-canonicalization`
2. `GET /analytics/research/stage18/topic-weights`
3. `GET /analytics/research/stage18/structural-arb`
4. `GET /analytics/research/stage18/final-report`

### 11.2 Артефакти

1. `artifacts/research/stage18_event_canonicalization.json`
2. `artifacts/research/stage18_topic_weights.json`
3. `artifacts/research/stage18_structural_arb.json`
4. `artifacts/research/stage18_final_report.md`

## 12. План реалізації

### Phase A (1 тиждень)

1. Event canonical key builder + `event_group_id`.
2. Міграція БД + backfill.
3. Базовий canonicalization report.

### Phase B (1 тиждень)

1. Topic weight matrix builder.
2. Інтеграція у divergence scoring.
3. Shadow comparison проти baseline.

### Phase C (1-2 тижні)

1. Structural arb signal detector.
2. Basket guardrails + reporting.
3. End-to-end regression tests.

### Phase D (0.5 тижня)

1. Stage18 final report.
2. GO / LIMITED_GO / NO_GO verdict.
3. Rollback plan.

## 13. Ризики

1. Поганий event grouping -> false arbitrage.
2. Data sparsity для topic weights у вузьких категоріях.
3. Structural arb без якісної leg-level liquidity моделі може бути “paper-only edge”.
4. Drift у payload IDs між платформами.

## 14. Критерії прийняття Stage18

1. `event_group_coverage >= 0.70`
2. `cross_platform_match_recall >= baseline + 20%`
3. `weighted_divergence_hit_rate >= baseline + 5%`
4. `structural_arb_candidates_per_day >= agreed_min`
5. `stage18_shadow_post_cost_ev_ci_low_80 > 0` хоча б для 1 core category
6. Відсутність регресій у Stage7/Stage17 критичних тестах

## 15. Явне рішення щодо Kalshi

1. Stage18 **не залежить** від Kalshi execution.
2. Якщо Kalshi відключений, Stage18 залишається валідним на трійці:
   - `POLYMARKET`
   - `MANIFOLD`
   - `METACULUS`
3. Усі acceptance checks мають мати режим `kalshi_optional=true`.

