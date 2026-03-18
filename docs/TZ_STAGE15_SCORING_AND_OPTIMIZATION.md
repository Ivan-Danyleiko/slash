# ТЗ Stage 15 — Composite Scoring, Kelly Optimization, Exit Strategy

**Дата:** 2026-03-18
**Статус:** ПЛАНУВАННЯ
**Пріоритет:** HIGH — безпосередньо впливає на прибутковість dry-run і реальної торгівлі

---

## 1. Мета

Замінити "waterfall" фільтри на ранжувальний composite score, виправити Kelly розрахунок, додати time-horizon management та оптимізувати стратегію виходу. Результат: збільшити кількість відкритих позицій з ~4–5/день до ~20–30/день при кращій якості відбору.

---

## 2. Поточний стан (baseline)

### 2.1 Проблеми які вирішуємо

| # | Проблема | Наслідок |
|---|----------|----------|
| P1 | Waterfall-фільтри незалежні, немає trade-off між EV і spread | Хороші угоди відкидаються через один слабкий параметр |
| P2 | Kelly fraction береться від LLM → ненадійно (галюцинації, 0 values) | Позиції занижені або взагалі не відкриваються |
| P3 | Немає обмеження по time-horizon | Капітал може бути заморожений у 5-місячних ринках |
| P4 | Stop-loss 50% — занадто агресивний | Позиції закриваються при тимчасовій корекції |
| P5 | Volume $5k filter — надлишковий для CLOB ринків | Втрачаємо валідні CLOB ринки з меншим volume |
| P6 | Non-CLOB ринки повністю виключені | 80% ринків недоступні навіть для dry-run симуляції |

### 2.2 Поточні числові параметри

```
HARD_MAX_SPREAD     = 0.08   (8%)
HARD_MIN_VOLUME     = 5_000  ($5k)
HARD_MAX_DAYS       = 180
SOFT_MAX_SPREAD     = 0.04   (4%)
SOFT_MIN_VOLUME     = 50_000 ($50k)
LLM_MIN_DAILY_EV    = 0.0005 (0.05%/день)
MIN_POSITION_PCT    = 0.03   (3%)
MAX_POSITION_PCT    = 0.05   (5%)
STOP_LOSS_RATIO     = 0.50   (50% від entry)
TAKE_PROFIT_RATIO   = 0.65   (65% captured max)
TIME_EXIT_DAYS      = 14
TIME_EXIT_MIN_EV    = 0.03
```

---

## 3. Архітектура змін

```
Старий pipeline:
  Signal → [EV filter] → [spread filter] → [volume filter] → [confidence filter] → Open

Новий pipeline:
  Signal → CompositeScorer → ranked_score → [Time-bucket check] → Kelly(_deterministic) → Open
                ↓
         (non-CLOB: додаємо estimated_entry_price = market_prob + spread_estimate)
```

---

## 4. Stage 15A — Composite Entry Scorer

### 4.1 Мета

Замінити waterfall hard/soft фільтри на єдиний числовий score. Ранжуємо кандидатів по score, беремо топ-N.

### 4.2 Формула (Детермінована, без ML)

```python
def composite_score(
    daily_ev_pct: float,       # EV на день, напр. 0.0015 = 0.15%/день
    spread: float,             # bid-ask spread, напр. 0.03 = 3%
    volume_usd: float,         # total traded volume, напр. 50000
    confidence: float,         # signal confidence 0..1
    days_to_resolution: int,   # днів до резолюції
    kelly_fraction: float,     # розраховується окремо (Stage 15B)
    is_clob: bool,             # чи є реальна CLOB ціна
) -> float:

    # 1. EV component: головна метрика, більше = краще
    #    Normalize: типовий діапазон 0..0.5%/день → 0..1
    ev_score = min(daily_ev_pct / 0.005, 1.0)  # cap at 0.5%/день

    # 2. Spread component: менше = краще
    #    0% spread → 1.0; 8% spread → 0.0
    spread_score = max(0.0, 1.0 - spread / 0.08)

    # 3. Liquidity component: логарифмічна шкала
    #    $1k → 0.5; $10k → 0.7; $100k → 1.0
    import math
    liq_score = min(math.log10(max(volume_usd, 1)) / 5.0, 1.0)  # log10(100k)=5

    # 4. Confidence component: 0..1 → 0..1
    conf_score = min(confidence, 1.0)

    # 5. Time component: near-term краще (більший daily EV)
    #    7 днів → 1.0; 30 днів → 0.8; 90 днів → 0.5; 180 днів → 0.2
    time_score = max(0.1, 1.0 - (days_to_resolution / 200.0))

    # 6. Kelly component: сила сигналу за Kelly
    kelly_score = min(kelly_fraction / 0.25, 1.0)  # cap at 25%

    # 7. CLOB bonus: реальна ціна краща за estimate
    clob_bonus = 0.15 if is_clob else 0.0

    # Ваги (можна тюнити; рекомендовані на основі дослідження)
    WEIGHTS = {
        "ev":         0.30,   # найважливіший
        "kelly":      0.20,   # сила позиції
        "spread":     0.20,   # вартість входу
        "confidence": 0.15,   # якість сигналу
        "time":       0.10,   # hour rate
        "liquidity":  0.05,   # proxy виходу
    }

    score = (
        WEIGHTS["ev"]         * ev_score
      + WEIGHTS["kelly"]      * kelly_score
      + WEIGHTS["spread"]     * spread_score
      + WEIGHTS["confidence"] * conf_score
      + WEIGHTS["time"]       * time_score
      + WEIGHTS["liquidity"]  * liq_score
      + clob_bonus
    )

    return round(score, 4)
```

### 4.3 Відбір кандидатів

```python
# Замість MAX_CANDIDATES=25 hard cap:
MIN_SCORE_THRESHOLD = 0.35     # абсолютний мінімум
TOP_N_PER_CYCLE     = 30       # беремо топ-30 по score

candidates = [c for c in all_signals if composite_score(c) >= MIN_SCORE_THRESHOLD]
candidates.sort(key=composite_score, reverse=True)
candidates = candidates[:TOP_N_PER_CYCLE]
```

### 4.4 Hard limits (залишаємо але спрощуємо)

```python
# Лишаємо тільки абсолютні hard limits:
HARD_MAX_SPREAD  = 0.10   # збільшуємо з 8% до 10% (spread враховується в score)
HARD_MAX_DAYS    = 180    # лишається
# HARD_MIN_VOLUME видаляємо — замінений liq_score в composite

# Для non-CLOB: додатковий penalty в score (вже включено через clob_bonus)
```

### 4.5 Файли для зміни

- `app/services/dryrun/simulator.py` — замінити `_check_hard_limits` і `_check_soft_limits` на `_compute_composite_score`
- `app/services/dryrun/scorer.py` — **новий файл** з функцією `composite_score()`
- Видалити константи: `SOFT_MAX_SPREAD`, `SOFT_MIN_VOLUME`, `SOFT_MAX_DAYS`, `LLM_MIN_DAILY_EV`, `HARD_MIN_VOLUME`

---

## 5. Stage 15B — Deterministic Kelly Fraction

### 5.1 Проблема

LLM повертає kelly=0 або нереалістичні значення. Kelly має рахуватися детерміновано.

### 5.2 Формула

```python
def kelly_fraction(
    market_price: float,     # поточна ціна YES (наприклад 0.40)
    our_prob: float,         # наша оцінка справжньої ймовірності (наприклад 0.55)
    alpha: float = 0.25,     # fractional kelly дільник (рекомендовано 0.25 для нових систем)
    max_fraction: float = 0.10,  # абсолютний cap на позицію (10% від портфеля)
) -> float:
    """
    Класичний Kelly для бінарного ринку, fractional варіант.

    Логіка:
    - Якщо купуємо YES за p_m, виграємо (1 - p_m) при WIN, втрачаємо p_m при LOSS
    - b = net odds = (1 - p_m) / p_m
    - f* = (our_prob * b - (1 - our_prob)) / b
    - f_fractional = alpha * f*

    Вибір alpha:
    - 0.25 (1/4 Kelly): рекомендується для нових систем з невизначеним edge
    - 0.50 (1/2 Kelly): якщо є 200+ trades з підтвердженим edge
    - 1.00 (Full Kelly): тільки з дуже точними оцінками (не рекомендується)
    """
    if our_prob <= market_price:
        return 0.0  # немає edge

    b = (1.0 - market_price) / market_price  # net odds
    q = 1.0 - our_prob                        # probability of loss

    f_star = (our_prob * b - q) / b

    if f_star <= 0:
        return 0.0

    # Fractional Kelly
    f_fractional = alpha * f_star

    # Cap
    return min(f_fractional, max_fraction)
```

### 5.3 Звідки брати `our_prob`

`our_prob` — це наша оцінка справжньої ймовірності. Джерела в порядку пріоритету:

```python
def estimate_our_prob(signal, stage7_decision, market) -> float:
    # 1. Якщо Stage7 дає market_prob — використовуємо
    if stage7_decision and stage7_decision.evidence_bundle.get("market_prob"):
        return float(stage7_decision.evidence_bundle["market_prob"])

    # 2. Якщо є Metaculus/Manifold duplicate — cross-platform consensus
    if signal.metadata_json.get("cross_platform_prob"):
        return float(signal.metadata_json["cross_platform_prob"])

    # 3. Якщо momentum signal — використовуємо current price як нижню оцінку
    #    і додаємо momentum bias
    if signal.signal_subtype == "momentum":
        current = market.market_prob or 0.5
        momentum_delta = float(signal.metadata_json.get("price_move", 0))
        return min(0.95, max(0.05, current + momentum_delta * 0.3))

    # 4. Fallback: current market price (edge = 0 → kelly = 0)
    return market.market_prob or 0.5
```

### 5.4 Portfolio Kelly (кілька одночасних позицій)

```python
def portfolio_kelly_adjustment(
    base_kelly: float,
    open_positions_count: int,
    total_open_notional_pct: float,  # % портфеля вже відкрито
    max_total_exposure: float = 0.40,  # max 40% портфеля в позиціях
) -> float:
    """
    Якщо вже є багато позицій — зменшуємо нові.
    Логіка: загальний Kelly-ризик розподіляється між позиціями.
    """
    remaining_capacity = max(0.0, max_total_exposure - total_open_notional_pct)

    if remaining_capacity <= 0:
        return 0.0  # портфель повний

    # Scale down якщо наближаємось до ліміту
    if total_open_notional_pct > max_total_exposure * 0.7:
        scale = 1.0 - (total_open_notional_pct / max_total_exposure)
        base_kelly = base_kelly * scale

    return min(base_kelly, remaining_capacity)
```

### 5.5 Файли для зміни

- `app/services/dryrun/kelly.py` — **новий файл** з `kelly_fraction()` і `portfolio_kelly_adjustment()`
- `app/services/dryrun/simulator.py` — замінити звернення до `ev_bundle.get("kelly_fraction")` на виклик `kelly.kelly_fraction()`
- Stage7 AI більше не відповідає за kelly число — тільки за `market_prob` оцінку

---

## 6. Stage 15C — Time-Horizon Portfolio Management

### 6.1 Концепція Time Buckets

```
Bucket A: 0–14 днів    → max 35% портфеля
Bucket B: 15–45 днів   → max 35% портфеля
Bucket C: 46–90 днів   → max 20% портфеля
Bucket D: 91–180 днів  → max 10% портфеля
```

Якщо bucket переповнений → нові позиції в цей bucket не відкриваються (поки існуючі не закриються або не виходять з bucket часом).

### 6.2 Daily EV як головна метрика порівняння

```python
def daily_ev_pct(total_ev_pct: float, days_to_resolution: int) -> float:
    """
    Нормалізує EV до денної ставки для порівняння ринків різних горизонтів.
    Ринок +3% за 90 днів = 0.033%/день
    Ринок +1% за 7 днів  = 0.143%/день  ← краще
    """
    if days_to_resolution <= 0:
        return 0.0
    return total_ev_pct / days_to_resolution
```

### 6.3 Bucket check при відкритті позиції

```python
TIME_BUCKETS = [
    (0,  14, 0.35),   # (min_days, max_days, max_portfolio_pct)
    (15, 45, 0.35),
    (46, 90, 0.20),
    (91, 180, 0.10),
]

def get_bucket(days: int) -> tuple:
    for min_d, max_d, max_pct in TIME_BUCKETS:
        if min_d <= days <= max_d:
            return (min_d, max_d, max_pct)
    return (91, 180, 0.10)  # default

def can_open_in_bucket(days: int, portfolio: DryrunPortfolio, db) -> bool:
    _, _, max_pct = get_bucket(days)
    # Порахувати відкриті позиції в цьому bucket
    bucket_exposure = _calc_bucket_exposure(days, portfolio, db)
    return bucket_exposure < (portfolio.initial_balance_usd * max_pct)
```

### 6.4 Файли для зміни

- `app/services/dryrun/simulator.py` — додати `can_open_in_bucket()` перед відкриттям позиції
- `app/services/dryrun/time_buckets.py` — **новий файл** з bucket логікою

---

## 7. Stage 15D — Оптимізована Exit Strategy

### 7.1 Stop-Loss: м'якший і розумніший

```
Поточно:  mark < entry * 0.50  (50% падіння → закрити)
Нове:
  Tier 1: mark < entry * 0.65  → partial exit (закрити 50% позиції)
  Tier 2: mark < entry * 0.40  → full exit (закрити решту)

Логіка: 50% → ринок може відновитись; 60% → вже серйозно; 40% → майже безнадійно
```

```python
STOP_LOSS_PARTIAL_RATIO = 0.65   # при -35% → виходимо на 50%
STOP_LOSS_FULL_RATIO    = 0.40   # при -60% → виходимо повністю
```

### 7.2 Take-Profit: Trailing Stop замість фіксованого

```
Поточно:  entry + captured_gain * 0.65  (статичний рівень)
Нове:     Trailing Stop від максимуму позиції

Логіка:
  - Відстежуємо max_mark_price (peak ціна позиції)
  - Якщо mark < max_mark * TRAILING_STOP_RATIO → закрити
  - TRAILING_STOP_RATIO = 0.85 (якщо відкотилось на 15% від піку → закрити)

Приклад:
  entry = 0.40, max_mark = 0.70
  trailing_stop = 0.70 * 0.85 = 0.595
  якщо mark впав до 0.58 < 0.595 → закриваємо з +45% прибутком
```

```python
TRAILING_STOP_RATIO  = 0.85   # від максимуму позиції
TAKE_PROFIT_ABSOLUTE = 0.90   # якщо mark >= 0.90 → завжди закрити (ринок майже resolved)
```

### 7.3 Time-Exit: на основі daily EV, а не фіксованих днів

```
Поточно:  після 14 днів з EV < 3% → закрити
Нове:     перевіряти daily_ev_remaining кожні 24 год

  daily_ev_remaining = unrealized_pnl / notional / days_remaining

  Якщо days_remaining > 30 AND daily_ev_remaining < MIN_DAILY_EV_CONTINUE (0.01%)
    → закрити (opportunity cost вищий ніж утримувати)

  Якщо days_remaining <= 7 → тримати до резолюції (вже близько)
```

```python
MIN_DAILY_EV_CONTINUE  = 0.0001   # 0.01%/день — мінімум щоб тримати позицію
HOLD_TO_RESOLUTION_DAYS = 7       # ближче 7 днів — тримати до кінця
```

### 7.4 Нова модель DryrunPosition

Додати поля:
```sql
-- Нові поля в dryrun_positions:
max_mark_price          DECIMAL(10,6)   -- peak ціна позиції (для trailing stop)
partial_exit_done       BOOLEAN         -- чи вже зроблено partial exit
partial_exit_price      DECIMAL(10,6)   -- ціна partial exit
partial_exit_shares     DECIMAL(18,6)   -- кількість акцій при partial exit
```

### 7.5 Файли для зміни

- `app/services/dryrun/simulator.py` — переписати `_check_exit_conditions()`
- `app/models.py` — додати 4 нових поля до `DryrunPosition`
- Alembic міграція `0019_dryrun_trailing_stop`

---

## 8. Stage 15E — Non-CLOB симуляція (dry-run only)

### 8.1 Мета

Дозволити dry-run відкривати позиції на ринках без CLOB ціни, використовуючи `market_prob` з консервативною поправкою.

### 8.2 Estimated Entry Price

```python
def estimated_entry_price(
    market_prob: float,
    signal_direction: str,  # "YES" або "NO"
    volume_usd: float,
    estimated_spread_pct: float = None,
) -> tuple[float, str]:
    """
    Оцінює ціну входу для non-CLOB ринку.

    Returns: (entry_price, price_source)
    price_source: "clob" | "estimated_amm" | "estimated_gamma"
    """
    if estimated_spread_pct is None:
        # Оцінюємо spread за volume
        if volume_usd >= 50_000:
            estimated_spread_pct = 0.02   # 2% для великих
        elif volume_usd >= 10_000:
            estimated_spread_pct = 0.04   # 4% для середніх
        else:
            estimated_spread_pct = 0.06   # 6% для малих

    # Консервативна поправка: входимо по гіршій ціні
    if signal_direction == "YES":
        # Ми купуємо YES → платимо ask (вище за mid)
        entry_price = min(0.99, market_prob + estimated_spread_pct / 2)
    else:
        # Ми купуємо NO → NO price = 1 - YES price
        yes_price = max(0.01, market_prob - estimated_spread_pct / 2)
        entry_price = 1.0 - yes_price

    return entry_price, "estimated_gamma"
```

### 8.3 Обмеження для non-CLOB позицій

```python
NON_CLOB_MAX_POSITION_PCT = 0.02   # max 2% від портфеля (vs 5% для CLOB)
NON_CLOB_MAX_TOTAL_PCT    = 0.15   # max 15% всього портфеля в non-CLOB

# Non-CLOB отримує penalty в composite score (clob_bonus = 0)
# Тобто будуть відкриватись тільки якщо score все одно >= MIN_SCORE_THRESHOLD
```

### 8.4 Mark-to-Market для non-CLOB

```python
# Без CLOB API не можемо оновлювати ціну
# Використовуємо Gamma API market_prob (оновлюється при sync)
# Mark price = market.market_prob (або 1 - market_prob для NO)
```

### 8.5 Файли для зміни

- `app/services/dryrun/simulator.py` — в `_open_position()` додати гілку для non-CLOB entry
- `app/services/dryrun/pricing.py` — **новий файл** з `estimated_entry_price()`
- `app/models.py` — `DryrunPosition.entry_price_source` (нове поле)

---

## 9. Stage 15F — Метрики і Brier Score

### 9.1 Нові метрики в reporter.py

```python
def calculate_brier_score(closed_positions: list) -> float:
    """
    Brier Score = mean((predicted_prob - actual_outcome)^2)
    Менше = краще. 0.25 = random, 0.0 = perfect.

    predicted_prob = entry_price (наша ставка на YES)
    actual_outcome = 1 якщо WIN, 0 якщо LOSS
    """
    if not closed_positions:
        return None
    scores = []
    for pos in closed_positions:
        if pos.close_reason in ("resolved_yes", "resolved_no"):
            predicted = pos.entry_price if pos.direction == "YES" else (1 - pos.entry_price)
            actual = 1.0 if pos.close_reason == "resolved_yes" else 0.0
            scores.append((predicted - actual) ** 2)
    return sum(scores) / len(scores) if scores else None

def calculate_daily_ev_realized(closed_positions: list) -> float:
    """Середній реалізований daily EV для закритих позицій"""
    evs = []
    for pos in closed_positions:
        if pos.realized_pnl_usd and pos.notional_usd and pos.opened_at and pos.closed_at:
            days_held = (pos.closed_at - pos.opened_at).total_seconds() / 86400
            if days_held > 0:
                ev_pct = pos.realized_pnl_usd / pos.notional_usd
                evs.append(ev_pct / days_held)
    return sum(evs) / len(evs) if evs else None

def calculate_sharpe_ratio(daily_pnl_series: list) -> float:
    """Annualized Sharpe Ratio. > 1.0 = хороший, > 2.0 = відмінний"""
    if len(daily_pnl_series) < 2:
        return None
    import statistics
    mean = statistics.mean(daily_pnl_series)
    std = statistics.stdev(daily_pnl_series)
    if std == 0:
        return 0.0
    return (mean / std) * (252 ** 0.5)  # annualized
```

### 9.2 Оновлений звіт `/api/v1/admin/dryrun/report`

```json
{
  "portfolio": { ... },
  "stats": {
    "total_positions": 25,
    "open": 15,
    "closed": 10,
    "win_rate": 0.60,
    "avg_win_usd": 2.10,
    "avg_loss_usd": 1.80,
    "brier_score": 0.18,
    "realized_daily_ev_pct": 0.12,
    "sharpe_ratio": 1.4,
    "positions_by_bucket": {
      "0_14d": 4,
      "15_45d": 6,
      "46_90d": 3,
      "91_180d": 2
    },
    "clob_vs_estimated": {
      "clob_positions": 9,
      "estimated_positions": 6
    }
  }
}
```

### 9.3 Файли для зміни

- `app/services/dryrun/reporter.py` — додати `brier_score`, `sharpe_ratio`, `realized_daily_ev_pct`, `positions_by_bucket`

---

## 10. Параметри конфігурації (нові .env змінні)

```bash
# Stage 15A — Composite Scorer
DRYRUN_MIN_SCORE_THRESHOLD=0.35
DRYRUN_TOP_N_PER_CYCLE=30
DRYRUN_SCORE_WEIGHT_EV=0.30
DRYRUN_SCORE_WEIGHT_KELLY=0.20
DRYRUN_SCORE_WEIGHT_SPREAD=0.20
DRYRUN_SCORE_WEIGHT_CONFIDENCE=0.15
DRYRUN_SCORE_WEIGHT_TIME=0.10
DRYRUN_SCORE_WEIGHT_LIQUIDITY=0.05

# Stage 15B — Kelly
DRYRUN_KELLY_ALPHA=0.25
DRYRUN_KELLY_MAX_FRACTION=0.10
DRYRUN_MAX_TOTAL_EXPOSURE=0.40

# Stage 15C — Time Buckets
DRYRUN_BUCKET_0_14_MAX_PCT=0.35
DRYRUN_BUCKET_15_45_MAX_PCT=0.35
DRYRUN_BUCKET_46_90_MAX_PCT=0.20
DRYRUN_BUCKET_91_180_MAX_PCT=0.10

# Stage 15D — Exit Strategy
DRYRUN_STOP_LOSS_PARTIAL_RATIO=0.65
DRYRUN_STOP_LOSS_FULL_RATIO=0.40
DRYRUN_TRAILING_STOP_RATIO=0.85
DRYRUN_TAKE_PROFIT_ABSOLUTE=0.90
DRYRUN_MIN_DAILY_EV_CONTINUE=0.0001
DRYRUN_HOLD_TO_RESOLUTION_DAYS=7

# Stage 15E — Non-CLOB
DRYRUN_NON_CLOB_ENABLED=true
DRYRUN_NON_CLOB_MAX_POSITION_PCT=0.02
DRYRUN_NON_CLOB_MAX_TOTAL_PCT=0.15
```

---

## 11. Фази реалізації

### Phase 1 — Core Scoring + Kelly (Тиждень 1)
- [ ] `app/services/dryrun/scorer.py` — `composite_score()`
- [ ] `app/services/dryrun/kelly.py` — `kelly_fraction()`, `portfolio_kelly_adjustment()`
- [ ] Оновити `simulator.py` — замінити waterfall на scorer + kelly
- [ ] Видалити `HARD_MIN_VOLUME`, `SOFT_*` константи
- [ ] Тести: `tests/dryrun/test_scorer.py`, `tests/dryrun/test_kelly.py`

### Phase 2 — Time Buckets + Non-CLOB (Тиждень 1–2)
- [ ] `app/services/dryrun/time_buckets.py`
- [ ] `app/services/dryrun/pricing.py` — `estimated_entry_price()`
- [ ] Оновити `simulator.py` — bucket check + non-CLOB branch
- [ ] Alembic міграція `0019_dryrun_entry_source`

### Phase 3 — Exit Strategy Overhaul (Тиждень 2)
- [ ] Переписати `_check_exit_conditions()` в `simulator.py`
- [ ] Додати `max_mark_price` tracking в mark-to-market task
- [ ] Alembic міграція `0020_dryrun_trailing_stop`
- [ ] Тести: `tests/dryrun/test_exit_strategy.py`

### Phase 4 — Metrics + Report (Тиждень 2–3)
- [ ] Оновити `reporter.py` — Brier, Sharpe, bucket stats
- [ ] Оновити `/api/v1/admin/dryrun/report` response schema
- [ ] Тести: `tests/dryrun/test_reporter.py`

---

## 12. KPI / Definition of Done

| Метрика | Baseline (зараз) | Ціль після Stage 15 |
|---------|-----------------|---------------------|
| Позицій/день | 4–5 | 20–30 |
| Conversion rate | 0.14% | 0.8–1.0% |
| Brier Score | N/A | < 0.20 (краще за random 0.25) |
| Win rate | N/A (1 відкрита) | > 55% після 50+ closed |
| Sharpe Ratio | N/A | > 1.0 після 30+ днів |
| Realized daily EV | N/A | > 0.05%/день |
| Max bucket fill | N/A | < 90% у кожному bucket |

---

## 13. Ризики

| Ризик | Ймовірність | Мітигація |
|-------|------------|-----------|
| Non-CLOB estimated price відрізняється від реальної | Середня | Відстежувати `entry_price_source`, аналізувати різницю post-factum |
| Composite score перетоп у одному факторі | Низька | Логувати breakdown score для кожної позиції |
| Kelly дає великі ставки на нових даних | Середня | `KELLY_ALPHA=0.25` + `MAX_FRACTION=0.10` = жорсткий cap |
| Time bucket занадто обмежує сигнали | Низька | `BUCKET_0_14_MAX_PCT=0.35` досить широко; моніторити наповненість |
| Trailing stop спрацьовує на волатильності | Середня | `TRAILING_STOP_RATIO=0.85` — є 15% буфер |

---

## 14. Зв'язок з існуючими модулями

| Модуль | Зміна |
|--------|-------|
| `simulator.py` | Головний рефактор: scorer, kelly, buckets, exit |
| `reporter.py` | Нові метрики: Brier, Sharpe, bucket stats |
| `models.py` | Нові поля: `max_mark_price`, `entry_price_source`, `partial_exit_*` |
| `tasks.py` | `dryrun_refresh_prices`: оновлювати `max_mark_price` |
| `admin.py` | Оновити response schema для `/dryrun/report` |
| `scorer.py` | **Новий** |
| `kelly.py` | **Новий** |
| `time_buckets.py` | **Новий** |
| `pricing.py` | **Новий** |
