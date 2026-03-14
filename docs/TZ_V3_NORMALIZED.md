# ТЗ v3 — Stage 5: Signal Quality Research

## 1. Мета Stage 5

Перевірити, які типи сигналів реально мають позитивне очікування (`EV+`) після врахування execution-витрат і ліквідності, та визначити:

1. Які сигнали залишити (`KEEP`).
2. Які сигнали модифікувати (`MODIFY`).
3. Які сигнали прибрати (`REMOVE`).

Кінцева ціль: побудувати систему сигналів, яка дає користувачам реальний шанс заробляти.

## 2. Ключові дослідницькі питання

### 2.1 Які сигнали мають реальний edge

Порівняти:

1. `divergence signals`
2. `rules-risk signals`
3. `arbitrage signals`
4. `weird market signals`

### 2.2 Який мінімальний divergence має практичний сенс

Дослідити пороги:

1. `diff = 3%`
2. `diff = 5%`
3. `diff = 10%`
4. `diff = 15%`

Потрібно знайти threshold, за якого сигнал ще `tradeable`.

### 2.3 Як ліквідність впливає на сигнал

Перевірити, коли сигнал теоретично існує, але не є executable.

Оцінювати:

1. `liquidity`
2. `volume`
3. `slippage`

### 2.4 Який timing сигналу

Виміряти, через скільки після появи divergence він зникає.

Горизонти:

1. `1 хв`
2. `10 хв`
3. `1 година`
4. `1 день`

### 2.5 Які категорії markets найкращі

Порівняти категорії:

1. `crypto`
2. `macro`
3. `politics`
4. `technology`
5. `community markets`

## 3. Data Collection для research

Потрібно зберігати історію для кожного сигналу:

1. `signal_id`
2. `signal_type`
3. `platform`
4. `market_id`
5. `timestamp`
6. `p_market`
7. `related_market_probability`
8. `divergence`
9. `liquidity`
10. `volume`

Відкладені мітки (labeling через час):

1. `probability_after_1h`
2. `probability_after_6h`
3. `probability_after_24h`

## 4. Execution Simulation

Симулювати:

1. `buy at signal time`
2. `sell later`

Сценарії виходу:

1. `exit_after_1h`
2. `exit_after_6h`
3. `exit_after_24h`
4. `exit_at_resolution`

Враховувати витрати:

1. `fees`
2. `spread`
3. `slippage`

## 5. EV Calculation

Для кожного сигналу:

`EV = expected_profit - costs`

Де:

1. `expected_profit` = `price_change`
2. `costs` = `trading fee + slippage + spread + gas`

## 6. Quality Metrics

Обов’язково рахувати:

1. `hit_rate`
2. `avg_return`
3. `median_return`
4. `max_drawdown`
5. `sharpe_like_ratio`

## 7. Signal Lifetime

Вимірювати:

1. `time_until_divergence_closes`

Це критично важлива метрика для виконуваності сигналу.

## 8. Liquidity Safety

Перевіряти:

1. `max trade size without slippage`

Базові розміри для тесту:

1. `$50`
2. `$100`
3. `$500`

## 9. Signal Filtering Research

### 9.1 Divergence thresholds

Перевірити:

1. `>5%`
2. `>10%`
3. `>15%`

### 9.2 Liquidity thresholds

Перевірити:

1. `volume > 1000`
2. `volume > 5000`

### 9.3 Rules-risk thresholds

Перевірити:

1. `risk_score > 0.5`
2. `risk_score > 0.7`

## 10. Monte Carlo Simulation

Для кожного типу сигналів:

1. Симулювати `1000 trades`.
2. Оцінити:
   - `risk of ruin`
   - `expected return`
   - `variance`

## 11. Ranking Research

Порівняти формули:

1. `score_total`
2. `edge_only`
3. `edge + liquidity`
4. `edge + liquidity + freshness`

## 12. Platform Comparison

Порівняти платформи:

1. `Polymarket`
2. `Manifold`
3. `Metaculus`

Мета: визначити, де найбільший `edge`.

## 13. Event Cluster Research

Дослідити markets, що належать до однієї події.

Приклад кластера:

1. `BTC >100k`
2. `BTC ATH`
3. `Bitcoin price milestone`

Оцінити, чи `cluster divergence` сильніший за одиночні сигнали.

## 14. Result Tables (обов’язкові артефакти)

### 14.1 Table — Best signals

Поля:

1. `signal_type`
2. `threshold`
3. `avg_return`
4. `confidence`

### 14.2 Table — Bad signals

Поля:

1. `signal_type`
2. `reason`

## 15. Final Decision Framework

Для кожного типу сигналу сформувати рішення:

1. `KEEP`
2. `MODIFY`
3. `REMOVE`

## 16. Practical Output (після research)

### 16.1 Оновлення порогів

1. `divergence threshold`
2. `liquidity threshold`
3. `rules risk threshold`

### 16.2 Оновлення ranking

1. Оновити `score formula`.

### 16.3 Оновлення signal types

1. Переглянути/скоригувати набір типів сигналів.

## 17. Deliverables Stage 5

Після Stage 5 має бути:

1. `research report`
2. `signal performance tables`
3. `new thresholds`
4. `algorithm improvements`

Ключове правило Stage 5:

`execution > theory`

Найпоширеніша помилка prediction market ботів: показують сигнали, але не перевіряють, чи їх реально виконати.

---

## Додаток A — Найперспективніші типи сигналів

### A.1 Signal Type 1 — Cross-Platform Divergence

Опис: одна й та сама подія має різну ймовірність на різних платформах.

Приклад:

1. Event: `BTC > 100k by Dec`
2. Platform A: `0.62`
3. Platform B: `0.48`
4. Platform C: `0.55`
5. `max_diff = 14%`

Чому може працювати:

1. Різна ліквідність платформ.
2. Різна аудиторія.
3. Повільне оновлення ціни.
4. Різні механіки ринку.

Що дослідити по сигналу:

1. `divergence %`
2. `liquidity`
3. `volume`
4. `market similarity score`

Execution simulation:

1. `buy undervalued market`
2. `sell later`

Exit сценарії:

1. `exit_after_1h`
2. `exit_after_6h`
3. `exit_after_24h`
4. `exit_when_divergence_closes`

Threshold research:

1. `>3%`
2. `>5%`
3. `>8%`
4. `>10%`
5. `>15%`

Метрики:

1. `hit_rate`
2. `avg_return`
3. `median_return`
4. `max_drawdown`

### A.2 Signal Type 2 — Rules Mispricing

Опис: markets з неякісними/ambiguous rules часто мають неправильну ціну.

Приклад:

1. Market: `Will company X release product in 2026?`
2. Rules: `Resolved by major news sources`
3. Проблема: `ambiguity`

Чому може працювати:

1. Більшість трейдерів не читають rules.

Що дослідити:

1. Побудувати `rules risk score`.
2. Фактори:
   - `ambiguity`
   - `subjective resolution`
   - `unclear data source`
   - `multi-condition rules`
3. Перевірити, чи markets з high rules risk частіше мають `price corrections`.

Метрики:

1. `probability_change_after_signal`
2. `resolution_bias`

### A.3 Signal Type 3 — Low Liquidity Lag

Опис: markets з низькою ліквідністю повільно реагують на інформацію.

Приклад:

1. Event: `Fed rate decision`
2. New information released.
3. Liquid market: ціна рухається одразу.
4. Thin market: реакція із затримкою.

Що дослідити:

1. `volume`
2. `orderbook depth`
3. `spread`
4. `price_change_lag`

Метрики:

1. `time_to_price_adjustment`
2. `expected_return_after_lag`

### A.4 Signal Type 4 — Event Cluster Divergence

Опис: одна подія має кілька markets з різним wording.

Приклад (Bitcoin breakout):

1. `BTC > 100k`
2. `BTC ATH`
3. `BTC > 120k`
4. `BTC record price`

Чому може працювати:

1. `fragmented liquidity` через різні формулювання.

Що дослідити:

1. Побудувати `event clusters`.

Метрика:

1. `cluster_probability_variance`

### A.5 Signal Type 5 — Timing Shock (Information Lag)

Ідея: ціна часто реагує на новину із затримкою.

Сценарій:

1. `новина`
2. `5–30 хв затримки`
3. `price update`

Гіпотеза: хто заходить раніше, має edge.

(Деталізація порогів/метрик для Type 5 виконується в межах Stage 5 research pipeline за тією ж методологією, що й для Type 1–4: execution simulation + EV + risk metrics.)

---

## Додаток B — Execution Safety

Для кожного сигналу перевіряти:

1. `spread`
2. `slippage`
3. `volume`

Execution safety rule:

Сигнал показується лише якщо:

`expected_profit > execution_cost`

---

## Додаток C — Signal Ranking Update

Цільова формула ранжування:

`score = 0.35*edge + 0.25*liquidity + 0.20*execution_safety + 0.10*freshness + 0.10*confidence`

Штрафи (`penalty`):

1. `rules_risk`
2. `low_volume`
3. `high_spread`

---

## Додаток D — Research Output

Наприкінці Stage 5 потрібно отримати:

### Таблиця 1

1. `signal_type`
2. `avg_return`
3. `median_return`
4. `hit_rate`
5. `max_drawdown`
6. `confidence`

### Таблиця 2

1. `best_thresholds`

### Таблиця 3

1. `worst signals`

Final deliverable Stage 5:

`validated signal framework`, який визначає:

1. Які сигнали залишити.
2. Які сигнали змінити.
3. Які сигнали видалити.

Ключовий висновок:

У prediction markets edge часто не в прогнозі події, а в:

1. `structure`
2. `rules`
3. `liquidity`
4. `timing`

---

## Уточнення v3.1 (інтеграція аналітики)

Цей блок додає відсутні технічні специфікації для запуску Stage 5 в production-safe режимі.

## 3.1 DB Schema для Research

Додати таблицю `signal_history` для фіксації стану сигналу в момент генерації та відкладених labels.

### Мінімальна структура

1. `id` (PK)
2. `signal_id` (FK -> `signals.id`)
3. `timestamp`
4. `platform`
5. `market_id`
6. `probability_at_signal`
7. `related_market_probability`
8. `divergence`
9. `liquidity`
10. `volume_24h`
11. `probability_after_1h`
12. `probability_after_6h`
13. `probability_after_24h`
14. `labeled_at`
15. `simulated_trade` (JSON)

### Retention policy

1. `SIGNAL_HISTORY_RETENTION_DAYS=90`.
2. Щоденний cleanup-job видаляє записи старші retention.

### Індекси

1. `(timestamp)`
2. `(signal_type, timestamp)`
3. `(platform, timestamp)`
4. `(signal_id)`

## 3.2 Labeling Jobs Architecture

### Job 1: Label 1h outcomes (щогодини)

1. Вибрати записи `signal_history` у вікні `now-1h ± 5m`, де `probability_after_1h IS NULL`.
2. Зчитати актуальну probability з `markets`.
3. Заповнити `probability_after_1h`, `labeled_at`.

### Job 2: Label 6h outcomes (кожні 6 годин)

Аналогічно для `probability_after_6h`.

### Job 3: Label 24h outcomes (щодня)

Аналогічно для `probability_after_24h`.

### Job 4: Resolution outcomes (щодня)

1. Для resolved markets фіксувати фінальний outcome.
2. Додавати поле `resolved_success` для пост-фактум оцінки сигналів.

## 4.1 Execution Simulation Assumptions

### Platform cost profiles

1. `POLYMARKET`: trading fee, gas fee, min spread.
2. `MANIFOLD`: fee≈0, lower spread (play-money limitation).
3. `METACULUS`: `tradeable=false` (аналітичний референс, не trade execution).

### Slippage model (без historical orderbook)

1. `volume_based` модель для ринків без depth.
2. `liquidity_based` модель як conservative fallback.
3. Обмеження slippage cap: `<= 5%`.

### Position sizes

1. `RESEARCH_POSITION_SIZES = [50, 100, 250, 500]` USD.
2. Всі метрики рахуються окремо по кожному розміру позиції.

## 5.1 Detailed EV Calculation

Використовувати:

`EV = (P_win * Profit_if_win) + (P_lose * Loss_if_lose) - Costs`

Де:

1. `P_lose = 1 - P_win`
2. `Costs = fees + slippage + spread + gas`
3. Окремо рахувати:
   - `EV_abs`
   - `EV_pct`
   - `Sharpe_like`
   - `Kelly_fraction` (informational)

## 7.1 Signal Lifetime Measurement (realistic)

Поточна частота sync задає мінімальну гранулярність вимірювання.

### Базові горизонти

1. `15m`
2. `30m`
3. `1h`
4. `6h`
5. `24h`

### Додатковий режим

Для critical divergence (`>15%`) дозволено додатковий polling кожні 5 хв тільки для top-N сигналів (rate-limit safe).

## 10.1 Monte Carlo Methodology

Використовувати bootstrapping + parameter sampling на базі реальних historical signals.

### Мінімум

1. `n_sims=1000`
2. Метрики:
   - `final_pnl_distribution`
   - `max_drawdown`
   - `risk_of_ruin`
   - `variance`
   - `sharpe_like`

### Обмеження

1. Якщо реальних сигналів мало, явно маркувати confidence інтервал результатів Monte Carlo як low-confidence.

## 18. Implementation Roadmap

### Phase 1 (Week 1-2): Infrastructure

1. `signal_history` + migrations.
2. Labeling jobs (1h/6h/24h).
3. MVP execution simulator assumptions.
4. Базовий research dashboard/report generator.

### Phase 2 (Week 3-4): Signal Type 1 (Cross-Platform Divergence)

1. Зібрати мінімум `500` divergence samples.
2. Прогнати EV + execution simulation.
3. Рішення `KEEP/MODIFY/REMOVE`.

### Phase 3 (Week 5): Signal Type 2 (Rules Mispricing)

1. Посилення rules-risk features.
2. Correlation price correction vs rules-risk score.

### Phase 4 (Week 6-7): Signal Type 3/4/5

1. Low liquidity lag.
2. Event clusters.
3. Timing shock validation у межах доступної granularity.

### Phase 5 (Week 8): Production Optimization

1. Оновлення thresholds.
2. Оновлення ranking.
3. Видалення слабких signal types.
4. Підсумковий performance report (before/after).

## 19. Acceptance Criteria Stage 5

### По кожному signal type

`KEEP` якщо одночасно:

1. `EV > 1%` per trade (after costs)
2. `hit_rate > 52%`
3. `sharpe_like > 0.5`
4. `risk_of_ruin < 10%` (на 100 trades)
5. `median_lifetime > 1h`

`MODIFY` якщо:

1. `0.5% <= EV <= 1%` і є потенціал підсилення threshold tuning.

`REMOVE` якщо:

1. `EV < 0.5%` або
2. `hit_rate < 50%` або
3. `avg_lifetime < 30m`.

### Загальний success Stage 5

1. Мінімум 2 signal types з `EV > 2%`.
2. Portfolio `Sharpe_like > 1.0`.
3. Мінімум 5 executable signals/day.
4. Документовані причини для `REMOVE` типів.

## 20. A/B Testing Framework

### Мета

Перевірити, що optimized v3 реально кращий за v2 у production.

### Дизайн

1. Control: v2 framework.
2. Treatment: v3 optimized framework.
3. Спліт аудиторії: `50/50`.
4. Тривалість: `30 днів`.

### Метрики

1. `CTR`
2. engagement time
3. retention
4. signal diversity
5. user feedback score (якщо доступно)

### Умова успіху

1. v3 CTR > v2 на `20%+`.
2. engagement > v2 на `15%+`.

## 21. Ethical Guidelines

### Disclosure (обов’язково)

Показувати дисклеймер:

`This is algorithmic analysis, not financial advice. Prediction markets involve risk. Past performance != future results.`

### Заборонено

1. Публікувати сигнали з негативним EV як actionable.
2. Використовувати формулювання «гарантований прибуток».
3. Приховувати execution costs та confidence.

### Прозорість для користувача

Показувати в сигналі:

1. confidence score
2. expected costs
3. utility/EV estimate
4. execution assumptions version

---

## Уточнення v3.2 (готові інструменти та Build-vs-Buy)

## 22. Technology Stack (цільовий стек + режим впровадження)

### 22.0 Compliance mode

Щоб уникнути vendor lock-in і важких залежностей у production path, Stage 5 працює у двох сумісних режимах:

1. `Baseline (mandatory)`: in-app research pipeline (native Monte Carlo, native tracking, execution assumptions).
2. `Advanced (recommended)`: підключення external stack через optional dependencies (`pip install .[research]`).

Обидва режими вважаються валідними для виконання Stage 5, якщо acceptance criteria секції 19 виконані.

### 22.1 Data Collection

1. `Polymarket`: `py-clob-client` (CLOB orderbook, trades, realtime streams).
2. `Manifold`: official REST API + AMM-based slippage formula.
3. `Metaculus`: REST API historical forecasting time series (labeling/reference).

### 22.2 Backtesting & Simulation (advanced mode)

1. `VectorBT` — primary backtesting engine (швидкий vectorized research).
2. `Backtrader` — fallback для складних event-driven сценаріїв.
3. `QuantStats` — performance/risk reports.

### 22.3 Data Management (advanced mode)

1. `PostgreSQL` — storage (`signal_history`, labels, experiment outputs).
2. `Great Expectations` — data quality checks.
3. `MLflow` — experiment tracking.

### 22.4 Statistical Analysis (baseline + advanced)

1. `SciPy` — базова статистика.
2. `PyMC` (optional) — Bayesian uncertainty.
3. `Pandas/NumPy` — data pipeline.

### 22.5 Visualization & Reporting (optional layer)

1. `Plotly` — інтерактивні графіки.
2. `Streamlit` — research dashboard.
3. `Jupyter` — exploratory analysis.

### 22.6 Cost & Setup

1. Рекомендований стек базується на open-source інструментах.
2. Орієнтовний setup: `3-5 днів`.
3. Build-from-scratch еквівалент: `8-10 тижнів`.

## 23. Build-vs-Buy Policy

### 23.1 Принцип

`Build only business logic; buy (reuse) infrastructure.`

### 23.2 Що НЕ будуємо з нуля

1. Backtesting engine (`VectorBT` / `Backtrader`).
2. Portfolio metrics/reporting (`QuantStats`).
3. Experiment tracking (`MLflow`).
4. Data quality framework (`Great Expectations`).
5. Polymarket orderbook client (`py-clob-client`).

### 23.3 Що будуємо кастомно

1. Signal detection logic.
2. Cross-platform matching and canonicalization.
3. Execution assumptions calibration для нашого домену.
4. Decision layer (`KEEP/MODIFY/REMOVE`) і threshold governance.

## 24. Tool Priority Matrix

### 24.1 Advanced recommended stack

1. `VectorBT`
2. `QuantStats`
3. `MLflow`
4. `py-clob-client` (Polymarket)
5. `Great Expectations`

Примітка: це обов'язково для режиму `advanced`, але не блокує baseline-виконання Stage 5.

### 24.2 NICE TO HAVE

1. `Backtrader`
2. `Snorkel` (programmatic labeling)
3. `PyMC`
4. `Streamlit`

### 24.3 OPTIONAL

1. `tsfresh`
2. `FLAML`
3. `Zipline`

### 24.4 MVP minimum (accepted baseline)

1. Native research backtesting/Monte Carlo у сервісі.
2. Native experiment registry (DB/JSONL) з сумісним export API.
3. Native execution-cost/slippage assumptions з явним `assumptions_version`.
4. Optional external stack підключається поступово без блокування delivery.

## 25. API/Provider Risk Controls

### 25.1 Third-party dependency risks

1. API schema drift.
2. Rate limits / temporary outages.
3. Incomplete historical depth on some platforms.

### 25.2 Mitigation

1. Локальний cache/warehouse historical snapshots.
2. Provider adapters з версіонуванням payload mapping.
3. Health checks + fallback mode per provider.
4. Регулярний contract-check для критичних endpoint-ів.

## 26. Implementation Notes for Existing Project

### 26.1 Мінімальний стартовий план інтеграції стеку

1. Додати research service-модуль (`app/services/research/`).
2. Додати jobs для history capture + labeling (`1h/6h/24h`).
3. Експорт даних у формат, зручний для VectorBT/QuantStats.
4. Зберігати кожен research run в MLflow (params, metrics, artifacts).

### 26.2 Порядок запуску

1. Спочатку Phase 1-2 (інфраструктура + divergence research).
2. Через 4 тижні перевірити EV/Sharpe/risk-of-ruin.
3. Якщо edge відсутній (`EV < 0.5%` стабільно) — pivot strategy.

## 27. Deliverables Addendum (до Stage 5)

Окрім базових deliverables Stage 5, додатково обов'язково надати:

1. `Stack decision log` (що взяли готове, що кастомне).
2. `Provider reliability report` (uptime/errors/rate-limit impact).
3. `Experiment registry export` (`MLflow summary` або `in-app registry summary`).
4. `Build-vs-Buy time saved estimate` (planned vs actual).
