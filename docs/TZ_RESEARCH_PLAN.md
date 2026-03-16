# ТЗ: Дослідницький план — Автономний торговий бот

**Версія:** 1.0
**Дата:** 2026-03-16
**Статус:** Draft
**Поточний стан:** Stage 10 PASS, Stage 11 SHADOW (dry run), `probability_after_6h = NULL` для всіх 2 595 рядків `signal_history`

---

## Зміст

1. [Завдання 1: Розмітка сигналів із snapshot-ів](#завдання-1-розмітка-сигналів-із-snapshot-ів)
2. [Завдання 2: Виконувана дивергенція (bid/ask)](#завдання-2-виконувана-дивергенція-bidask)
3. [Завдання 3: Калібровка апріорних оцінок V2](#завдання-3-калібровка-апріорних-оцінок-v2)
4. [Завдання 4: Оцінка рішень LLM (Stage 7)](#завдання-4-оцінка-рішень-llm-stage-7)
5. [Завдання 5: Walk-forward тестування з реальними мітками](#завдання-5-walk-forward-тестування-з-реальними-мітками)
6. [Порядок виконання та залежності](#порядок-виконання-та-залежності)
7. [Загальні критерії готовності](#загальні-критерії-готовності)

---

## Завдання 1: Розмітка сигналів із snapshot-ів

### Мета
Заповнити колонки `probability_after_1h`, `probability_after_6h`, `probability_after_24h` у таблиці `signal_history` на основі існуючих `market_snapshots`. Це розблоковує всі наступні кроки.

### Контекст
Наразі існує `run_stage10_timeline_backfill` (`stage10_timeline_backfill_run.py`), але він пише в `market.source_payload` (Manifold/Metaculus API-history) — **не** в `signal_history`. Потрібен окремий job, який зіставляє кожен рядок `signal_history` зі snapshot-ами і записує дрейф вірогідності.

Таблиця `market_snapshots` має ~200 000+ рядків з мітками часу — це достатня щільність для більшості сигналів.

### Технічна специфікація

#### 1.1 Новий сервісний модуль
**Файл:** `app/services/research/signal_history_labeler.py`

```python
def label_signal_history_from_snapshots(
    db: Session,
    *,
    batch_size: int = 500,
    max_snapshot_lag_hours: float = 2.0,   # допустиме відхилення від target_ts
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Для кожного рядка signal_history де probability_after_6h IS NULL:
    1. Знайти ринок (market_id)
    2. Для N = 1, 6, 24 год: знайти перший snapshot ПІСЛЯ timestamp + N год,
       але не пізніше ніж timestamp + N год + max_snapshot_lag_hours
    3. Записати probability_yes зі snapshot у відповідне поле
    4. Повернути статистику: labeled_count, skipped_no_snapshot, skipped_no_market
    """
```

**Алгоритм пошуку snapshot для горизонту N годин:**
```sql
SELECT probability_yes, fetched_at
FROM market_snapshots
WHERE market_id = :market_id
  AND fetched_at >= :target_ts                          -- timestamp + N*3600
  AND fetched_at <= :target_ts + interval ':lag hours'  -- max lag
ORDER BY fetched_at ASC
LIMIT 1
```

**Умова запису:** записувати тільки якщо `probability_yes IS NOT NULL` і snapshot знайдено. Не перезаписувати вже заповнені поля.

#### 1.2 Celery job
**Файл:** `app/tasks/jobs.py` — додати функцію `label_signal_history_job`

```python
def label_signal_history_job(db: Session) -> dict:
    # job_name = "label_signal_history"
    # Запускати: щодня о 02:00 UTC
    # batch_size = 1000, max_snapshot_lag_hours = 2.0
    # dry_run = False
```

**Розклад Celery Beat:** `crontab(hour=2, minute=0)`

#### 1.3 API endpoint (опціональний, для ручного запуску)
`POST /admin/label-signal-history?dry_run=true`
Відповідь: `{labeled: N, skipped_no_snapshot: M, already_labeled: K}`

### Критерії прийняття

| Критерій | Порогове значення |
|----------|-------------------|
| `labeled_count` після першого запуску | ≥ 500 рядків |
| `probability_after_6h IS NOT NULL` частка | ≥ 30% від 2 595 рядків |
| Відсутність lookahead: `fetched_at ≥ signal_history.timestamp + 6h` | 100% |
| Збіг market_id: snapshot відносить до того ж ринку | 100% |
| Час виконання на 2 595 рядках | ≤ 60 секунд |

### Тести
- `tests/test_signal_history_labeler.py`
- Тест 1: рядок зі snapshot через 7 год → `probability_after_6h` заповнено, `probability_after_24h` NULL (немає snapshot через 24 год)
- Тест 2: рядок без snapshot → всі поля NULL, в `skipped_no_snapshot += 1`
- Тест 3: dry_run=True → DB не змінюється, статистика вірна
- Тест 4: повторний запуск → вже заповнені поля не перезаписуються

### Залежності від існуючого коду
- `app/models/models.py`: `SignalHistory`, `MarketSnapshot` — вже існують, змін не потрібно
- `app/tasks/jobs.py`: додати `label_signal_history_job`
- Celery Beat config: додати розклад

---

## Завдання 2: Виконувана дивергенція (bid/ask)

### Мета
Замінити грубу формулу `|p_A - p_B|` на чисту виконувану дивергенцію з урахуванням bid-ask спредів обох платформ і транзакційних витрат. Виключити хибнопозитивні сигнали, де паперова дивергенція поглинається спредом.

### Контекст
Поточна логіка в `app/services/analyzers/divergence.py`:
```python
divergence = abs(p_a - p_b)
if divergence >= SIGNAL_DIVERGENCE_THRESHOLD:
    emit_signal()
```
Не враховується: bid/ask на кожній стороні, gas-fee ($2), bridge-fee ($0.50), spread Manifold (~0.5-1%).

### Технічна специфікація

#### 2.1 Нова функція в аналізаторі
**Файл:** `app/services/analyzers/divergence.py`

```python
def compute_executable_divergence(
    market_a: Market,
    market_b: Market,
    *,
    position_size_usd: float = 50.0,
    gas_fee_usd: float = 2.0,
    bridge_fee_usd: float = 0.50,
) -> ExecutableDivergenceResult:
    """
    Повертає:
      gross_divergence: float          — |p_a - p_b|
      executable_divergence: float     — після спреду обох сторін
      net_edge_after_costs: float      — після всіх витрат
      direction: str                   — "YES" або "NO"
      has_clob_data: bool              — Polymarket CLOB доступний
      spread_a: float                  — спред на стороні A
      spread_b: float                  — спред на стороні B
    """
```

**Логіка розрахунку:**

```python
# Отримання ефективних bid/ask:
def _effective_spread(market: Market) -> tuple[float, float]:
    # ask = best_ask_yes  або  probability_yes + spread_cents/200  або  p + 0.01
    # bid = best_bid_yes  або  probability_yes - spread_cents/200  або  p - 0.01
    if market.best_ask_yes and market.best_bid_yes:
        return float(market.best_ask_yes), float(market.best_bid_yes)
    if market.spread_cents:
        half = float(market.spread_cents) / 200.0
        p = float(market.probability_yes)
        return p + half, p - half
    p = float(market.probability_yes)
    return p + 0.01, p - 0.01  # fallback 1% спред

# Для YES-напрямку (p_a < p_b, купуємо YES на A, "продаємо" YES на B):
ask_a, bid_a = _effective_spread(market_a)
ask_b, bid_b = _effective_spread(market_b)

if p_a < p_b:  # YES: дешево купуємо на A
    executable = bid_b - ask_a          # скільки отримаємо на B - скільки витратимо на A
    direction = "YES"
else:           # NO: дешево купуємо на B
    executable = bid_a - ask_b
    direction = "NO"

fixed_costs_pct = (gas_fee_usd + bridge_fee_usd) / position_size_usd
net_edge = executable - fixed_costs_pct
```

#### 2.2 Оновлення сигналу дивергенції
У `SignalHistory` при створенні DIVERGENCE сигналу додатково зберігати:
```python
features_snapshot = {
    ...existing fields...,
    "gross_divergence": gross,
    "executable_divergence": executable,
    "net_edge_after_costs": net_edge,
    "has_clob_data": has_clob,
    "spread_a": spread_a,
    "spread_b": spread_b,
}
```

#### 2.3 Новий поріг для емісії сигналу
Конфіг (`app/core/config.py`):
```python
SIGNAL_DIVERGENCE_NET_EDGE_MIN: float = 0.02   # мінімальна чиста дивергенція
SIGNAL_DIVERGENCE_USE_EXECUTABLE: bool = False  # feature flag (вимк. за замовч.)
```

При `SIGNAL_DIVERGENCE_USE_EXECUTABLE=true`:
```python
if net_edge_after_costs >= settings.signal_divergence_net_edge_min:
    emit_signal()
```

При `false` — стара логіка (зворотна сумісність).

#### 2.4 Ретроспективний аналіз
**Скрипт:** `scripts/analyze_executable_divergence.py`

Для всіх існуючих DIVERGENCE сигналів в `signal_history` (де `features_snapshot` містить `divergence`):
- Реконструювати `net_edge` використовуючи збережені дані або поточні ринкові спреди
- Вивести: `{total, profitable_gross, profitable_net, false_positive_rate}`

### Критерії прийняття

| Критерій | Порогове значення |
|----------|-------------------|
| `executable_divergence ≤ gross_divergence` завжди | 100% |
| `net_edge_after_costs ≤ executable_divergence` завжди | 100% |
| Feature flag вимкнений → поведінка ідентична старій | 100% сигналів однакові |
| Частка хибнопозитивних в ретроаналізі задокументована | ≥ 1 запуск скрипту |
| Час обчислення на пару ринків | ≤ 1 мс |

### Тести
- `tests/test_executable_divergence.py`
- Тест 1: 5% gross, 1% спред кожен бік → executable = 3%, net = 2.5% (після fees при $50)
- Тест 2: 3% gross, 2% спред → net < 0, сигнал не емітується при USE_EXECUTABLE=true
- Тест 3: немає CLOB-даних → fallback спред, `has_clob_data=false`
- Тест 4: feature flag вимкнено → стара поведінка, сигнали як раніше

---

## Завдання 3: Калібровка апріорних оцінок V2

### Мета
Уточнити константи `SIGNAL_EXECUTION_V2_PRIOR_*` на основі реальних емпіричних даних (отриманих з Завдання 1). Замінити «розумні здогадки» на дані-обґрунтовані значення.

### Контекст
`ExecutionSimulatorV2` (`app/services/signals/execution.py`) змішує емпіричний edge з апріором:
```python
w = min(1.0, len(returns) / min_samples)   # 0 при відсутності даних
expected_edge = w * empirical_edge + (1 - w) * prior
```
Поточні `prior_*` = ~2-3% (невідомого походження). Після розмітки можна обчислити реальні значення.

### Передумови
- Завдання 1 виконано: ≥ 500 рядків `signal_history` з `probability_after_6h IS NOT NULL`

### Технічна специфікація

#### 3.1 Скрипт калібровки
**Файл:** `scripts/calibrate_v2_priors.py`

```python
def compute_empirical_priors(db: Session, *, lookback_days: int = 90) -> dict:
    """
    Для кожної комбінації (category × signal_type):
    1. Відфільтрувати signal_history де probability_after_6h IS NOT NULL
    2. Обчислити direction-aware return:
       return = (p_after_6h - p_at_signal) * sign  # sign: YES=+1, NO=-1
    3. Агрегат: mean_return, std_return, n_samples, hit_rate
    4. Повернути рекомендовані значення prior_*
    """
    returns_by_category = {
        "crypto": [],
        "finance": [],
        "sports": [],
        "politics": [],
        "other": [],
    }
    # Також per signal_type: DIVERGENCE, RULES_RISK, WEIRD_MARKET, etc.
```

**Метрики, що обчислюються:**
```python
{
    "category": "crypto",
    "signal_type": "DIVERGENCE",
    "n_samples": 142,
    "mean_return": 0.031,       # → рекомендований prior
    "std_return": 0.087,        # → рекомендована variance для Kelly
    "hit_rate": 0.54,
    "ci_80_low": 0.008,
    "ci_80_high": 0.054,
    "recommended_prior": 0.031,  # або 0.020 якщо CI включає 0
}
```

**Правило оновлення prior:**
```python
# Якщо CI_80_low > 0: prior = mean_return (впевнені в edge)
# Якщо CI_80_low <= 0 і mean > 0: prior = mean * 0.5 (знижена довіра)
# Якщо mean <= 0: prior = 0.005 (мінімальний захисний prior)
recommended = (
    mean if ci_80_low > 0
    else (mean * 0.5 if mean > 0 else 0.005)
)
```

#### 3.2 Оновлення конфігурації
Скрипт виводить рекомендовані env-змінні у форматі `.env`:
```bash
# Рекомендовані значення на основі N=XXX сигналів (2026-03-16):
SIGNAL_EXECUTION_V2_PRIOR_CRYPTO=0.031
SIGNAL_EXECUTION_V2_PRIOR_FINANCE=0.018
SIGNAL_EXECUTION_V2_PRIOR_SPORTS=0.012
SIGNAL_EXECUTION_V2_PRIOR_POLITICS=0.024
SIGNAL_EXECUTION_V2_PRIOR_DEFAULT=0.015
```

Оновлення відбувається **вручну** після рев'ю результатів скрипту.

#### 3.3 Збереження в БД (опціонально, Stage 10+)
Зберігати артефакт калібровки в `artifacts/v2_prior_calibration_YYYYMMDD.json` для аудит-треку.

### Критерії прийняття

| Критерій | Порогове значення |
|----------|-------------------|
| Скрипт виконується без помилок | ✓ |
| Охоплення: категорій з ≥ 30 зразків | ≥ 3 з 5 |
| Відхилення нових прайорів від поточних | Задокументовано, не > 5x |
| V2 симулятор з новими прайорами: `predicted_edge > 0` для ≥ 60% сигналів | ✓ |

### Тести
- `tests/test_v2_prior_calibration.py`
- Тест 1: 100 рядків crypto з mean=0.03, std=0.05, CI_80_low=0.01 → prior=0.03
- Тест 2: 100 рядків finance з mean=0.005, CI_80_low=-0.01 → prior=0.0025
- Тест 3: 5 рядків (< min_samples) → попередження, prior = поточний default

---

## Завдання 4: Оцінка рішень LLM (Stage 7)

### Мета
1. Ввімкнути shadow mode Stage 7 агента в продакшні для збору рішень
2. Оцінити точність KEEP/SKIP рішень відносно реальних ринкових результатів
3. Перевірити калібровку confidence
4. Провести A/B порівняння одиночного vs ensemble підходу

### Контекст
Наразі: `STAGE7_AGENT_REAL_CALLS_ENABLED=false` → `stage7_agent_decisions` = 0 рядків. Shadow mode мусить бути ввімкнений, але рішення не впливають на виконання.

### Передумови
- `STAGE7_AGENT_SHADOW_ENABLED=true`, `STAGE7_AGENT_REAL_CALLS_ENABLED=true` в prod
- Зібрано ≥ 200 рішень `Stage7AgentDecision` з `decision=KEEP`
- Завдання 1 частково виконано (щоб порівняти KEEP зі збігом 6h)

### Технічна специфікація

#### 4.1 Ввімкнення shadow mode

Зміни в `.env` (продакшн):
```bash
STAGE7_AGENT_REAL_CALLS_ENABLED=true    # зараз: false
STAGE7_AGENT_SHADOW_ENABLED=true        # залишається
STAGE7_AGENT_MONTHLY_BUDGET_USD=50.0    # підняти з 20
```

**Важливо:** `shadow_mode=true` означає, що рішення KEEP **не** проходять до Stage 11 виконання — лише логуються.

#### 4.2 Модуль оцінки
**Файл:** `app/services/research/stage7_calibration.py`

```python
def build_stage7_calibration_report(
    db: Session,
    *,
    days: int = 90,
    horizon: str = "6h",
) -> dict[str, Any]:
    """
    Метрики:
    1. Precision: P(сигнал прибутковий | decision=KEEP)
    2. Recall: P(KEEP | сигнал прибутковий)  — якщо є контрфакт
    3. Calibration: для кожного confidence-бакету [0-0.5, 0.5-0.6, ..., 0.9-1.0]:
       hit_rate_actual vs confidence_bucket_mean
    4. Порівняння: single-model vs multi-model (якщо є кілька провайдерів)
    """
```

**Визначення "прибутковий":**
```python
def _is_profitable(decision: Stage7AgentDecision, db: Session) -> bool | None:
    # 1. Знайти signal → signal_history рядок
    # 2. Знайти probability_after_6h
    # 3. Обчислити direction-aware return
    # 4. return > 0 → True (profitable), <= 0 → False, NULL → None (unknown)
```

**Confidence calibration bucket:**
```python
buckets = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
for lo, hi in buckets:
    decisions_in_bucket = [d for d in keep_decisions if lo <= d.confidence < hi]
    actual_hit_rate = profitable_count / len(decisions_in_bucket)
    expected_confidence = mean([d.confidence for d in decisions_in_bucket])
    calibration_error = abs(actual_hit_rate - expected_confidence)
```

#### 4.3 API endpoint
`GET /analytics/stage7-calibration?days=90`

#### 4.4 Ensemble A/B тест (фаза 2, після збору даних)

**Конфігурація:**
```bash
STAGE7_ENSEMBLE_ENABLED=false     # feature flag
STAGE7_ENSEMBLE_MODELS=gpt-4o-mini,claude-haiku-4-5  # через кому
STAGE7_ENSEMBLE_VOTING=majority   # majority | weighted_confidence
```

**Логіка:**
- `signal_id % 2 == 0` → control (single model)
- `signal_id % 2 == 1` → treatment (ensemble: 2+ моделі, majority vote)
- Логувати всі рішення окремих моделей + фінальне ensemble рішення

**Метрики порівняння:**
```python
{
    "control": {
        "n_keep": 85,
        "precision_keep": 0.54,
        "avg_confidence": 0.71,
        "cost_usd_per_signal": 0.002,
    },
    "treatment": {
        "n_keep": 72,     # може бути менше — суворіший відбір
        "precision_keep": 0.63,  # очікуємо вище
        "avg_confidence": 0.74,
        "cost_usd_per_signal": 0.005,  # ~2.5x дорожче
    }
}
```

### Критерії прийняття

| Критерій | Порогове значення |
|----------|-------------------|
| `stage7_agent_decisions` зібрано | ≥ 300 рядків KEEP |
| Precision при confidence ≥ 0.7 | > 0.55 |
| Calibration ECE (Expected Calibration Error) | < 0.15 |
| Ensemble precision > single model precision | Підтверджено або спростовано |
| Місячні витрати на LLM при поточному обсязі | ≤ $50 |

### Тести
- `tests/test_stage7_calibration.py`
- Тест 1: 10 KEEP рішень, 6 прибуткових → precision=0.6
- Тест 2: confidence=[0.8,0.8,0.8], hits=[1,1,0] → ECE = |0.67 - 0.8| = 0.13
- Тест 3: ensemble 2/2 = KEEP, 1/2 = SKIP → majority=KEEP, вирішується за confidence

---

## Завдання 5: Walk-forward тестування з реальними мітками

### Мета
Повторно запустити Stage 10 після розмітки (Завдання 1) і отримати реальний, а не проксі-розрахований результат. Перевірити стабільність стратегії через сценарний аналіз.

### Передумови
- Завдання 1 виконано: ≥ 500 рядків з `probability_after_6h IS NOT NULL`
- Ці рядки охоплюють ≥ 2 різні типи сигналів
- Часовий діапазон: ≥ 60 днів (для мінімум 2 walk-forward вікон)

### Технічна специфікація

#### 5.1 Оновлення walk-forward звіту
Функція `build_walkforward_report` (`walkforward.py`) вже правильно реалізована — вона автоматично використає заповнені дані. Змін у коді не потрібно.

**Параметри запуску після розмітки:**
```python
build_walkforward_report(
    db,
    days=180,            # збільшити з 90 — більше вікон
    horizon="6h",
    train_days=30,
    test_days=14,
    step_days=7,         # зменшити крок для більше вікон
    embargo_hours=24,
    min_samples_per_window=10,  # знизити поріг — мало даних спочатку
    bootstrap_sims=500,
)
```

#### 5.2 Оновлення Stage 10 final report
Після того як `walkforward_available=True` (є реальні вікна), перевірка:
```python
"walkforward_negative_window_share_le_30pct": (
    True if walkforward_negative_window_share is None   # старий fallback
    else walkforward_negative_window_share <= 0.30       # реальна перевірка
)
```
Це вже реалізовано. Завдання — досягти стану, де `walkforward_available=True`.

#### 5.3 Сценарний аналіз (розширені sweeps)
Поточні sweeps (3×3×2 = 18) з `_row_effective_edge` (симетричний проксі). Після розмітки — додати варіант з реальними returns:

**Новий параметр `use_real_returns`:**
```python
# В scenario_sweeps: якщо labeled_returns доступні для рядка — використовувати їх
# Інакше — fallback на edge_proxy (як зараз)
real_return_coverage = labeled_rows / total_rows
```

**Метрики нових sweeps:**
```python
{
    "sweeps_using_real_returns": 12,    # кількість сценаріїв де є реальні дані
    "sweeps_using_proxy": 6,            # де все ще проксі
    "real_return_coverage": 0.45,       # частка рядків з реальними мітками
    "positive_scenarios_real": 10,      # серед real-return сценаріїв
    "positive_scenarios_proxy": 5,      # серед proxy сценаріїв
}
```

#### 5.4 Перевірка embargo (Q5 з RESEARCH_CONTEXT)
**Скрипт:** `scripts/analyze_embargo_sensitivity.py`

Запустити Stage 10 replay з різними значеннями embargo: 0h, 1h, 3h, 6h, 12h.
Зафіксувати: `leakage_violations_count` і `post_cost_ev_ci_low_80` для кожного.

```python
for embargo_h in [0, 1, 3, 6, 12]:
    result = build_stage10_replay_report(
        db, settings=settings,
        embargo_seconds=embargo_h * 3600,
        ...
    )
    print(f"embargo={embargo_h}h: leakage={result['leakage']}, ci_low={result['ev_ci_low']}")
```

#### 5.5 Аналіз швидкості корекції дивергенцій (Q6 з RESEARCH_CONTEXT)
**Скрипт:** `scripts/analyze_divergence_decay.py`

Для DIVERGENCE-рядків у `signal_history`:
```python
for row in divergence_rows:
    for lag_min in [15, 30, 60, 120]:
        snap = first_snapshot_after(row.market_id, row.timestamp + timedelta(minutes=lag_min))
        if snap:
            residual_divergence = abs(snap.probability_yes - row.related_market_probability)
            # residual < threshold → сигнал "скоригувався"
```

Вивести: медіанний час до корекції, відсоток сигналів що корегуються за 1 годину.

### Критерії прийняття

| Критерій | Порогове значення |
|----------|-------------------|
| `walkforward_available = True` | ✓ (після Завдання 1) |
| `evaluated_windows ≥ 2` для ≥ 1 типу сигналу | ✓ |
| `walkforward_negative_window_share ≤ 0.30` | Ціль (може не виконатись одразу) |
| Stage 10 `final_decision` залишається PASS | ✓ |
| Аналіз embargo: задокументовано оптимальне значення | ≥ 1 запуск |
| Аналіз decay дивергенцій: задокументована медіана | ≥ 1 запуск |

### Тести
Основний функціонал `build_walkforward_report` вже протестований у `test_stage10_foundation.py`. Додаткові тести:
- `tests/test_walkforward_with_labeled_data.py`
- Тест 1: 50 рядків з реальними returns → evaluated_windows ≥ 1
- Тест 2: всі test-windows мають avg_return > 0 → `negative_window_share = 0.0`
- Тест 3: mix реальних і NULL рядків → правильне розділення між реальними і proxy

---

## Порядок виконання та залежності

```
Тиждень 1-2:
  ├── [1] Розмітка сигналів            ← починаємо першими
  └── [2] Executable divergence        ← паралельно, незалежна

Тиждень 3 (після ~500 розмічених рядків):
  ├── [3] Калібровка V2 прайорів        ← потребує [1]
  └── [4a] Ввімкнення Stage 7 shadow   ← незалежно, але збір даних займе час

Тиждень 4-6:
  ├── [4b] Оцінка LLM (після збору 200+ рішень)
  └── [5] Walk-forward з реальними мітками ← потребує [1]

Тиждень 6+:
  └── [4c] Ensemble A/B тест (опціонально)
```

**Критичний шлях:** 1 → 3 → 5 (всі три залежні послідовно)

---

## Загальні критерії готовності

Stage 10 залишається PASS після всіх змін:
- `leakage_violations_count = 0`
- `post_cost_ev_ci_low_80 > 0`
- `scenario_sweeps_positive ≥ 12/18`

Нові метрики після виконання плану:
- `walkforward_available = True` і `walkforward_negative_window_share ≤ 0.30`
- `v2_prior_calibrated = True` (задокументовані оновлені значення)
- `stage7_precision_keep ≥ 0.55`
- `executable_divergence_analyzed = True` (% хибнопозитивних задокументовано)

Готовність до переходу Stage 11 LIMITED mode:
- Усі вищенаведені метрики виконані
- ≥ 30 днів shadow mode Stage 11 (поточна вимога `STAGE11_MIN_SHADOW_DAYS`)
- `stage11_final_decision = GO` або `LIMITED_GO`
