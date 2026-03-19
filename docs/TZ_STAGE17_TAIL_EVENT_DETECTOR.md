# ТЗ Stage 17 — Tail Event Detector

**Дата:** 2026-03-19
**Статус:** ПЛАНУВАННЯ
**Пріоритет:** HIGH — нова незалежна стратегія, паралельна до momentum/uncertainty

---

## 1. Мета і концепція

### 1.1 Що таке tail event стратегія

Prediction markets систематично **переоцінюють ймовірність драматичних подій**.
Люди бояться землетрусів, обвалів крипти, воєн — і ставлять на них більше ніж слід.
Це створює стабільний edge для того хто ставить проти паніки.

**Реальний приклад (planktonXD, Polymarket):**
- Стартовий капітал: $1,000
- Результат за 30 днів: $98,241 (+9,724%)
- Стратегія: сотні малих ставок на "нічого не станеться"
- Більшість програвала, але x500 виплати перекривали все з лишком

**Ключова асиметрія:**
```
Звичайна ставка (50/50):  entry $0.50 → win $1.00 → ROI +100%
Tail ставка:              entry $0.01 → win $1.00 → ROI +9,900%

Якщо реальна prob = 5%, а ринок ціну = 1%:
  Kelly каже ставити БАГАТО — edge величезний
  Навіть якщо програємо 9 з 10 — одна перемога покриває 9 програшів
```

### 1.2 Психологія ринку (чому edge існує)

| Bias | Опис | Як нам допомагає |
|------|------|-----------------|
| **Availability bias** | Після новини про землетрус всі думають що буде ще | Ставити NO на наступний землетрус |
| **Narrative bias** | Ринок "знає" що щось станеться → ціна 15% → реально 3% | Ставити проти наративу |
| **Fear premium** | Hedge buyers готові переплачувати за захист | Ставати counterparty |
| **Recency bias** | BTC впав вчора → ринок думає впаде ще | Ставити на відскок |
| **Overconfidence** | Ринок занадто впевнений в різких рухах | Ставити на стабільність |

### 1.3 Відмінність від поточної стратегії

| | Поточна (Stage 15/16) | Stage 17 (Tail) |
|--|----------------------|-----------------|
| Prob range | 0.30 – 0.70 | 0.01 – 0.10 |
| Edge type | Momentum mean-reversion | Base rate mispricing |
| Win rate | 55–73% | 70–90% |
| Avg win | +10–30% | +200–2000% |
| Avg loss | -30–60% | -100% (full loss) |
| Position size | Kelly-based 1–5% | Fixed micro 0.5–1% |
| Risk profile | Багато середніх угод | Багато програшів + рідкісні x величезні wins |

---

## 2. Що у нас вже є

### 2.1 Дані
- **31,018 ринків** у БД (більшість в `other`) — є потенційні tail candidates
- **Polymarket Gamma API** синкає metadata включно з `probability_yes`
- **CLOB API** дає bid/ask для ліквідних ринків
- **Stage7 LLM chain** — Groq → Gemini → OpenRouter вже налаштований
- **signal_history** — збергіає historical probabilities

### 2.2 Готова інфраструктура
- `generate_signals()` — можна додати новий mode
- `simulator.py` — `_open_position()` підтримує будь-який signal mode
- `scorer.py` — composite score легко розширити
- `reporter.py` — метрики вже рахуються per mode

### 2.3 Чого не вистачає
- Немає base rate estimation logic
- Немає external APIs (USGS, weather, sports stats)
- Немає спеціалізованого Stage7 промпту для tail events
- Немає категоризатора для типу події

---

## 3. Архітектура Stage 17

```
Новий flow:

Markets (prob < 10%)
    ↓
TailEventClassifier
  ├── CategoryDetector → earthquake | weather | crypto_level | sports | political
  ├── BaseRateEstimator
  │     ├── HistoricalFrequency (наші дані)
  │     ├── ExternalAPI (USGS / Binance / sports ref)
  │     └── LLM BaseRate Reasoner
  └── MispricingScorer
        └── mispricing = (our_prob - market_prob) / market_prob
              ↓ якщо mispricing > TAIL_MIN_MISPRICING_RATIO (2.0 = 200%)
              ↓
         TailSignal (TAIL_EVENT_CANDIDATE)
              ↓
         Stage7 (tail-specialized prompt)
              ↓
         DryRunSimulator (micro fixed bet)
```

---

## 4. Stage 17A — TailEventClassifier

### 4.1 Категорії і базові стратегії

```python
TAIL_CATEGORIES = {
    "natural_disaster": {
        "keywords": ["earthquake", "hurricane", "tornado", "flood", "tsunami",
                     "wildfire", "volcano", "storm", "typhoon", "landslide"],
        "strategy": "bet_no",        # ставимо проти катастрофи
        "base_rate_source": "usgs_api | historical_frequency",
        "min_prob": 0.005,           # мін 0.5% (нижче — занадто малий потенціал)
        "max_prob": 0.10,            # max 10%
    },
    "crypto_level": {
        "keywords": ["bitcoin above", "btc above", "eth above", "sol above",
                     "below $", "reach $", "hit $", "exceed"],
        "strategy": "llm_evaluate",  # залежить від напрямку
        "base_rate_source": "binance_historical | coingecko",
        "min_prob": 0.02,
        "max_prob": 0.12,
    },
    "sports_outcome": {
        "keywords": ["win", "score", "championship", "final", "beat",
                     "goal", "point", "match", "game"],
        "strategy": "llm_evaluate",
        "base_rate_source": "sports_reference | llm_base_rate",
        "min_prob": 0.01,
        "max_prob": 0.08,
    },
    "political_stability": {
        "keywords": ["resign", "impeach", "coup", "invasion", "war", "attack",
                     "sanction", "ban", "arrest", "assassination"],
        "strategy": "bet_no",        # ставимо на стабільність
        "base_rate_source": "llm_base_rate",
        "min_prob": 0.01,
        "max_prob": 0.08,
    },
    "regulatory": {
        "keywords": ["fda", "sec", "approve", "reject", "ban", "regulate",
                     "ruling", "verdict", "decision", "law"],
        "strategy": "llm_evaluate",
        "base_rate_source": "llm_base_rate",
        "min_prob": 0.02,
        "max_prob": 0.10,
    },
    "zero_event": {
        # Спеціальний тип: "ніщо не трапиться" питання
        "keywords": ["exactly 0", "no ", "zero ", "none ", "will not",
                     "won't happen", "without any"],
        "strategy": "bet_yes",       # ставимо ЗА нульовий результат
        "base_rate_source": "llm_base_rate",
        "min_prob": 0.01,
        "max_prob": 0.08,
    },
}
```

### 4.2 Класифікатор

```python
# app/services/signals/tail_classifier.py

def classify_tail_event(market: Market) -> dict | None:
    """
    Класифікує ринок як tail event candidate.
    Повертає None якщо не підходить.
    """
    prob = float(market.probability_yes or 0.5)
    title_lower = str(market.title or "").lower()

    # Prob range check
    if not (TAIL_MIN_PROB <= prob <= TAIL_MAX_PROB):
        return None

    # Category detection
    for cat_name, cat_config in TAIL_CATEGORIES.items():
        for keyword in cat_config["keywords"]:
            if keyword in title_lower:
                return {
                    "category": cat_name,
                    "strategy": cat_config["strategy"],
                    "base_rate_source": cat_config["base_rate_source"],
                    "market_prob": prob,
                    "direction": _infer_tail_direction(title_lower, cat_config["strategy"]),
                }

    return None


def _infer_tail_direction(title: str, strategy: str) -> str:
    """YES = ставимо що подія відбудеться; NO = що не відбудеться."""
    if strategy == "bet_no":
        return "NO"
    if strategy == "bet_yes":
        return "YES"
    # llm_evaluate — буде вирішено Stage7
    return "TBD"
```

---

## 5. Stage 17B — Base Rate Estimator

### 5.1 Концепція

**Base rate** = реальна частота події на основі historical даних або prior knowledge.
Якщо `base_rate >> market_prob` → ринок недооцінює → ставимо YES.
Якщо `base_rate << market_prob` → ринок переоцінює → ставимо NO.

### 5.2 Рівні оцінки (в порядку надійності)

```python
# app/services/signals/base_rate.py

class BaseRateEstimator:

    def estimate(self, market: Market, category: str) -> dict:
        """
        Returns: {
            'our_prob': float,
            'confidence': float,   # 0..1 наскільки впевнені
            'source': str,
            'reasoning': str,
        }
        """
        # Рівень 1: External API (найнадійніше)
        if category == "natural_disaster":
            return self._usgs_estimate(market)

        if category == "crypto_level":
            return self._crypto_volatility_estimate(market)

        # Рівень 2: Наші historical дані
        historical = self._our_historical_estimate(market, category)
        if historical["confidence"] >= 0.5:
            return historical

        # Рівень 3: LLM base rate reasoning (fallback)
        return self._llm_base_rate(market, category)
```

### 5.3 USGS Earthquake API (безкоштовний)

```python
def _usgs_estimate(self, market: Market) -> dict:
    """
    USGS Earthquake Hazards Program — публічний API.
    https://earthquake.usgs.gov/fdsnws/event/1/

    Рахуємо: скільки днів за останній рік не було землетрусу >= M4.5?
    Це і є наш base rate для "no earthquake today".
    """
    import urllib.request, json
    from datetime import datetime, timedelta

    # Запит: скільки землетрусів M4.5+ за останній рік?
    end = datetime.utcnow()
    start = end - timedelta(days=365)
    url = (
        f"https://earthquake.usgs.gov/fdsnws/event/1/count?"
        f"format=geojson&starttime={start.date()}&endtime={end.date()}"
        f"&minmagnitude=4.5"
    )
    # Відповідь: ~1400 землетрусів/рік → ~3.8/день
    # P(хоча б один за день) = 1 - P(0) = 1 - e^(-3.8) ≈ 0.978
    # Значить P(жодного) ≈ 0.022 = 2.2%

    # Якщо ринок каже "no earthquake" = 1% → наша оцінка 2.2% → edge 2.2x
    ...
```

### 5.4 Crypto Volatility Estimate

```python
def _crypto_volatility_estimate(self, market: Market) -> dict:
    """
    Для ринків типу "BTC above $120k by Oct 10?":
    Рахуємо через Log-Normal модель чи за historical volatility.

    Binance публічний API (без ключа для historical klines):
    GET /api/v3/klines?symbol=BTCUSDT&interval=1d&limit=365
    """
    # Витягнути target price і deadline з title (через regex)
    # Порахувати поточну ціну, days to deadline, historical volatility
    # P(BTC > X by date) через Log-Normal: Φ((ln(S/K) + (μ-σ²/2)T) / (σ√T))
    ...
```

### 5.5 LLM Base Rate Reasoner

Для категорій де немає API (political, regulatory, sports):

```python
TAIL_BASE_RATE_PROMPT = """
You are a calibrated superforecaster. Your task is to estimate the BASE RATE probability
of the following type of event occurring, based on historical frequency.

Market title: {title}
Market question: Will this resolve YES?
Current market probability: {market_prob:.1%}
Category: {category}

Instructions:
1. Identify the REFERENCE CLASS for this event (e.g., "military invasions of EU countries since 1945")
2. Count (approximately) how many times this type of event has happened vs not happened historically
3. Compute base rate from reference class
4. Adjust for current context (is there elevated risk? lower risk?)
5. Give your probability estimate

Respond in JSON:
{{
  "reference_class": "string",
  "historical_frequency": "X out of Y in Z years",
  "base_rate_pct": float,
  "current_context_adjustment": float,  // +/- from base rate
  "our_prob": float,                    // final estimate 0..1
  "confidence": float,                  // 0..1 how sure are you
  "reasoning": "1-2 sentences"
}}

Be CALIBRATED: most dramatic events are rarer than people think.
"""
```

### 5.6 Наші Historical Дані

```python
def _our_historical_estimate(self, market: Market, category: str) -> dict:
    """
    Якщо у нас є resolved ринки тієї самої категорії і типу —
    рахуємо empirical win rate як proxy для base rate.
    """
    # Шукаємо схожі resolved ринки через historical_rag.py
    # Але тут фокус на TAIL events: prob < 0.10 при створенні
    similar = self._find_similar_tail_resolved(market, category)
    if len(similar) < 5:
        return {"confidence": 0.0, "our_prob": None, "source": "insufficient_data"}

    yes_count = sum(1 for m in similar if m["resolved_yes"])
    our_prob = yes_count / len(similar)
    return {
        "our_prob": our_prob,
        "confidence": min(0.9, len(similar) / 20),  # більше даних → більша впевненість
        "source": f"historical_{len(similar)}_similar",
        "reasoning": f"{yes_count}/{len(similar)} similar tail events resolved YES",
    }
```

---

## 6. Stage 17C — Mispricing Scorer

### 6.1 Формула

```python
def tail_mispricing_score(
    market_prob: float,
    our_prob: float,
    confidence: float,
    days_to_resolution: int,
) -> dict:
    """
    Рахує наскільки ринок помиляється і чи варто торгувати.
    """
    if our_prob is None or our_prob <= 0:
        return {"tradeable": False, "reason": "no_estimate"}

    # Mispricing ratio: скільки разів наша prob відрізняється від ринкової
    mispricing_ratio = our_prob / max(market_prob, 0.001)

    # EV calculation для tail event
    # Якщо купуємо YES за market_prob:
    # EV = our_prob * (1 - market_prob) - (1 - our_prob) * market_prob
    ev = our_prob * (1 - market_prob) - (1 - our_prob) * market_prob
    ev_pct = ev / market_prob  # EV як % від ставки

    # Daily EV
    daily_ev = ev_pct / max(days_to_resolution, 1)

    # Score: комбінація mispricing + confidence + daily_ev
    score = (
        min(mispricing_ratio / 10.0, 1.0) * 0.40   # mispricing (cap at 10x)
        + confidence * 0.35                          # впевненість оцінки
        + min(daily_ev / 0.10, 1.0) * 0.25          # daily EV (cap at 10%/day)
    )

    return {
        "tradeable": (
            mispricing_ratio >= TAIL_MIN_MISPRICING_RATIO   # default: 2.0
            and confidence >= TAIL_MIN_CONFIDENCE            # default: 0.40
            and ev > 0
        ),
        "mispricing_ratio": round(mispricing_ratio, 2),
        "ev_pct": round(ev_pct, 4),
        "daily_ev": round(daily_ev, 6),
        "score": round(score, 4),
        "reason": "ok" if ev > 0 else "negative_ev",
    }
```

### 6.2 Пороги відбору

```python
# .env
TAIL_MIN_PROB             = 0.005    # 0.5% мінімум (нижче — занадто дешево)
TAIL_MAX_PROB             = 0.10     # 10% максимум (вище — вже не "tail")
TAIL_MIN_MISPRICING_RATIO = 2.0     # наша оцінка >= 2x ринкової
TAIL_MIN_CONFIDENCE       = 0.40     # впевненість в оцінці >= 40%
TAIL_MAX_CANDIDATES_PER_CYCLE = 15  # не більше 15 tail candidates за раз
TAIL_MIN_VOLUME           = 500      # мінімум $500 total volume
TAIL_MAX_DAYS             = 90       # не більше 90 днів до резолюції
```

---

## 7. Stage 17D — Спеціалізований Stage7 для Tail Events

### 7.1 Окремий промпт

```python
TAIL_STAGE7_PROMPT = """
You are evaluating a TAIL EVENT prediction market — a low-probability event
where the market may be significantly mispriced due to fear, narrative bias,
or availability heuristics.

Market: {title}
Market probability (YES): {market_prob:.2%}
Our base rate estimate: {our_prob:.2%}
Estimated mispricing: {mispricing_ratio:.1f}x
Base rate source: {base_rate_source}
Base rate reasoning: {base_rate_reasoning}
Category: {category}
Days to resolution: {days_to_resolution}
Proposed direction: {direction} (we bet {direction})

Your task:
1. VERIFY the base rate estimate — is it reasonable?
2. CHECK resolution criteria — is it unambiguous? Could it resolve unexpectedly?
3. ASSESS current context — is there elevated risk right now that justifies higher prob?
4. DECIDE: KEEP (trade it) or REMOVE (skip it)

Critical checks for REMOVE:
- Resolution criteria ambiguous or subject to interpretation
- Current news significantly elevates real probability above base rate
- Market has low liquidity (wide spread, few counterparties)
- Event time window is very short and risk is concentrated

Respond in JSON:
{{
  "decision": "KEEP" | "REMOVE",
  "adjusted_our_prob": float,         // your probability after reasoning
  "resolution_clarity": "clear" | "ambiguous" | "risky",
  "current_risk_elevated": bool,
  "confidence": float,
  "reasoning": "2-3 sentences max",
  "kelly_fraction": float             // suggested position size 0..0.05
}}
"""
```

### 7.2 Відмінності від звичайного Stage7

| Аспект | Звичайний Stage7 | Tail Stage7 |
|--------|-----------------|-------------|
| Питання | "Чи є тут edge?" | "Чи підтверджуєш base rate?" |
| Focus | EV, spread, confidence | Resolution clarity, current context |
| Kelly | 2–10% | 0.5–2% (micro) |
| REMOVE threshold | Слабкий edge | Ambiguous resolution або elevated risk |

---

## 8. Stage 17E — Position Sizing для Tail Events

### 8.1 Чому Kelly тут не підходить

Стандартний Kelly для tail events дає **великі ставки**:
```
market_prob = 0.02, our_prob = 0.06
b = (1 - 0.02) / 0.02 = 49
f* = (0.06 * 49 - 0.94) / 49 = (2.94 - 0.94) / 49 = 0.041 = 4.1%
f_1/4 Kelly = 1.025%
```

Це виглядає нормально, але проблема в тому що:
- Win rate 6% → програємо 94% ставок
- Drawdown може бути -50% перш ніж перша велика виплата
- Психологічно важко, портфель виглядає катастрофою до великого win

### 8.2 Три режими sizing

```python
TAIL_SIZING_MODE = "micro_fixed"  # | "micro_kelly" | "adaptive"

# Режим 1: micro_fixed (рекомендований для початку)
TAIL_FIXED_POSITION_PCT = 0.005   # завжди 0.5% (~$0.50 з $100)
# Логіка: мінімальна участь, максимум угод, статистика

# Режим 2: micro_kelly (для перевіреної стратегії)
TAIL_KELLY_ALPHA = 0.10           # 10% від full Kelly (дуже консервативний)
TAIL_KELLY_MAX_PCT = 0.01         # cap at 1%

# Режим 3: adaptive (score-based)
# position_pct = TAIL_FIXED * (1 + score)
# score від 0..1 → position від 0.5% до 1.0%
```

### 8.3 Окремий бюджет для tail

```python
TAIL_MAX_TOTAL_EXPOSURE_PCT = 0.10   # max 10% портфеля в tail позиціях
# Незалежно від основного MAX_TOTAL_EXPOSURE_PCT = 0.80
# Tail — окремий "кишеню" портфеля
```

### 8.4 Exit strategy для tail

**Кардинально відрізняється від звичайних позицій:**

```python
# Немає trailing stop — або 0 або x50
# Немає time-exit — тримаємо до резолюції або якщо prob різко змінилась

def _tail_exit_conditions(pos, new_mark):
    # 1. Prob різко виросла (ринок "почув" погані новини)
    if new_mark > pos.entry_price * 5:  # ціна виросла в 5 разів від entry
        # Наша YES ставка вже виросла з $0.02 до $0.10
        # Частково фіксуємо прибуток (50% позиції)
        return "partial_profit_5x"

    if new_mark > pos.entry_price * 20:  # x20 — закриваємо повністю
        return "take_profit_20x"

    # 2. Фундаментально змінилась ситуація (новини підтверджують ризик)
    # Перевіряється через Stage7 re-evaluation якщо є новий KEEP/REMOVE
    if pos.stage7_says_remove:
        return "stage7_remove"

    # 3. До резолюції менше 24 годин — тримаємо (вже закоміттились)
    if pos.days_to_resolution < 1:
        return None  # тримаємо

    return None  # тримаємо до резолюції
```

---

## 9. Stage 17F — External Data Sources

### 9.1 USGS Earthquake API

```python
# Безкоштовний, без ключа
BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/"

def get_earthquake_daily_prob(min_magnitude: float = 4.5, region: str = "global") -> float:
    """Повертає P(хоча б 1 землетрус за день)"""
    # COUNT за 365 днів → avg per day → Poisson distribution
    # P(N>=1) = 1 - e^(-lambda)
    ...
```

### 9.2 Binance OHLC (для крипто рівнів)

```python
# Публічний API, без ключа для historical даних
BASE_URL = "https://api.binance.com/api/v3/klines"

def get_crypto_target_prob(
    symbol: str,       # "BTCUSDT"
    target_price: float,
    direction: str,    # "above" | "below"
    days: int,         # до резолюції
) -> float:
    """
    P(BTC > target_price в наступні days днів)
    Через Log-Normal модель з historical volatility.
    """
    # 1. Завантажити 365 днів OHLC
    # 2. Порахувати daily log returns → σ (volatility)
    # 3. Застосувати Log-Normal: P(S_T > K) = Φ(d2)
    # d2 = (ln(S/K) + (μ - σ²/2)T) / (σ√T)
    ...
```

### 9.3 OpenWeatherMap (для weather events)

```python
# Free tier: 1000 calls/day
# Для ринків типу "temperature above X in city Y"

def get_weather_base_rate(
    location: str,
    condition: str,   # "above_30c" | "rain" | "snow"
    month: int,
) -> float:
    """Historical base rate з weather data"""
    ...
```

### 9.4 Sports Reference (для спортивних ставок)

```python
# Scraped або через unofficial API
# Historical win rates для команд/гравців

def get_sports_base_rate(
    team_or_player: str,
    opponent: str,
    event_type: str,   # "win" | "cover_spread" | "over_under"
) -> float:
    """Head-to-head historical win rate"""
    ...
```

### 9.5 LLM як Universal Fallback

Коли немає API або дані недоступні → LLM оцінює base rate через reference class reasoning.
Це найменш точно але покриває всі категорії.

---

## 10. Stage 17G — Варіації стратегії (A/B тестування)

### 10.1 Варіація A: "Stability Bettor" (planktonXD стиль)

```
Ставимо NO на всі драматичні питання:
- "Earthquake today?" → NO
- "Market crash this week?" → NO
- "War starts?" → NO
- "Resignation today?" → NO

Логіка: статус-кво зберігається 95%+ часу
Sizing: мікро-фіксований $0.50 на ставку
```

### 10.2 Варіація B: "Base Rate Arbitrage"

```
Тільки якщо є external API confirmation:
- USGS каже: P(earthquake) = 2.2%, ринок каже 5% → ставимо NO
- Binance volatility каже: P(BTC > $120k) = 8%, ринок каже 3% → ставимо YES
- Є чіткий кількісний edge

Більш консервативна, менше угод, вища якість
```

### 10.3 Варіація C: "Narrative Fade"

```
Після великих новин ринки overreact:
- "Will earthquake happen AGAIN today?" (після вчорашнього) → ймовірно NO
- "Will BTC drop AGAIN today?" (після вчорашнього дропу) → mean-reversion

Сигнал генерується через signal_history: якщо схожа подія вже відбулась вчора
і ринок ціну ≥ 10% → ставимо NO (mean-reversion bias)
```

### 10.4 Варіація D: "Cluster Events"

```
Деякі події кластеризуються (після землетрусу — більше афтершоків):
- Навпаки від Stability Bettor
- Якщо подія щойно відбулась → купуємо YES на повторення

Сигнал: "triggered event mode"
```

### 10.5 Рекомендація по A/B

Запускати всі варіації паралельно з окремими `signal_mode` тегами:
- `tail_stability` (Варіація A)
- `tail_base_rate` (Варіація B)
- `tail_narrative_fade` (Варіація C)

Через 60 днів порівнюємо ROI по кожній варіації і вимикаємо слабші.

---

## 11. Нові поля і таблиці

### 11.1 Новий `signal_mode` значення

```python
# app/models/enums.py — розширити SignalMode
class SignalMode(str, Enum):
    MOMENTUM = "momentum"
    UNCERTAINTY_LIQUID = "uncertainty_liquid"
    TAIL_STABILITY = "tail_stability"       # NEW: bet against dramatic events
    TAIL_BASE_RATE = "tail_base_rate"       # NEW: API-confirmed base rate arb
    TAIL_NARRATIVE_FADE = "tail_narrative_fade"  # NEW: post-event mean reversion
```

### 11.2 Нові поля в `signals.metadata_json`

```json
{
  "tail_category": "natural_disaster",
  "tail_strategy": "bet_no",
  "base_rate_our_prob": 0.022,
  "base_rate_source": "usgs_api",
  "base_rate_reasoning": "~4 earthquakes/day globally → P(none) = 2.2%",
  "mispricing_ratio": 2.27,
  "base_rate_confidence": 0.85,
  "external_api_used": "usgs"
}
```

### 11.3 Нові .env змінні

```bash
# Stage 17 — Tail Events
TAIL_EVENTS_ENABLED=true
TAIL_MIN_PROB=0.005
TAIL_MAX_PROB=0.10
TAIL_MIN_MISPRICING_RATIO=2.0
TAIL_MIN_CONFIDENCE=0.40
TAIL_MAX_CANDIDATES_PER_CYCLE=15
TAIL_FIXED_POSITION_PCT=0.005
TAIL_KELLY_ALPHA=0.10
TAIL_MAX_TOTAL_EXPOSURE_PCT=0.10
TAIL_SIZING_MODE=micro_fixed
TAIL_TAKE_PROFIT_MULTIPLIER=20.0
TAIL_PARTIAL_PROFIT_MULTIPLIER=5.0
TAIL_MAX_DAYS=90
TAIL_MIN_VOLUME=500

# External APIs
USGS_API_ENABLED=true
BINANCE_HISTORICAL_ENABLED=true
OPENWEATHER_API_KEY=            # опціонально
SPORTS_REF_ENABLED=false        # потребує scraping
```

---

## 11H — Hard Block: Resolution Ambiguity

### Правило

**SKIP без Stage7** якщо title або правила ринку містять будь-який з маркерів невизначеності.
Це rule-based фільтр — LLM не запитується взагалі.

```python
# app/services/signals/tail_classifier.py

AMBIGUITY_PATTERNS = [
    # Мова невизначеності
    r"\bapproximately\b", r"\babout\b", r"\broughly\b",
    r"\bat least\b", r"\bup to\b", r"\bor more\b", r"\bor less\b",
    # Суб'єктивне рішення
    r"\bat (?:the )?discretion\b", r"\badmin(?:istrator)? decision\b",
    r"\bsubject to\b", r"\bif applicable\b", r"\bmay be\b",
    r"\bcould be\b", r"\bmight\b", r"\bpossibly\b",
    # Ambiguous resolution
    r"\bsignificant(?:ly)?\b", r"\bsubstantial(?:ly)?\b",
    r"\bmajor\b", r"\bnotable\b", r"\bwidespread\b",
    # Conditional
    r"\bdepending on\b", r"\bassuming\b", r"\bprovided that\b",
]

def has_resolution_ambiguity(title: str, rules: str | None = None) -> bool:
    """
    Hard block — якщо True, сигнал не генерується взагалі.
    Перевіряється ДО будь-якого LLM виклику.
    """
    import re
    text = f"{title} {rules or ''}".lower()
    for pattern in AMBIGUITY_PATTERNS:
        if re.search(pattern, text):
            return True
    return False
```

### Застосування

```python
# В tail signal generation loop:
if has_resolution_ambiguity(market.title, market.rules_text):
    tail_skipped_ambiguous += 1
    continue  # HARD SKIP — без Stage7, без запису в signals
```

---

## 11I — Окремий Ledger для Stage 17

### Три нові таблиці

```sql
-- 1. Tail позиції (аналог dryrun_positions але ізольований)
CREATE TABLE stage17_tail_positions (
    id                      SERIAL PRIMARY KEY,
    signal_id               INTEGER REFERENCES signals(id),
    market_id               INTEGER REFERENCES markets(id),

    -- Entry
    direction               VARCHAR(3) NOT NULL,       -- YES | NO
    entry_price             DECIMAL(10,6) NOT NULL,
    notional_usd            DECIMAL(10,4) NOT NULL,    -- завжди мало ($0.50–$2)
    shares_count            DECIMAL(18,6) NOT NULL,

    -- Tail-specific
    tail_category           VARCHAR(50),               -- disasters | crypto_level | ...
    tail_variation          VARCHAR(50),               -- tail_stability | tail_base_rate | ...
    base_rate_our_prob      DECIMAL(6,4),              -- наша оцінка
    base_rate_source        VARCHAR(50),               -- usgs_api | binance | llm
    mispricing_ratio        DECIMAL(6,2),              -- our_prob / market_prob
    prompt_version          VARCHAR(16),               -- hash першого Stage7 промпту

    -- Status tracking
    status                  VARCHAR(20) DEFAULT 'OPEN',  -- OPEN | CLOSED | EXPIRED
    mark_price              DECIMAL(10,6),
    peak_mark_price         DECIMAL(10,6),             -- для x-multiplier tracking
    current_multiplier      DECIMAL(8,2),              -- поточний multiplier від entry

    -- Close
    close_reason            VARCHAR(50),               -- resolved_yes | resolved_no | take_profit_20x | ...
    realized_pnl_usd        DECIMAL(10,4),
    realized_multiplier     DECIMAL(8,2),              -- фінальний multiplier

    -- Timestamps
    opened_at               TIMESTAMPTZ DEFAULT now(),
    closed_at               TIMESTAMPTZ,
    resolution_deadline     TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ DEFAULT now()
);

-- 2. Fill events (кожна значна зміна ціни)
CREATE TABLE stage17_tail_fills (
    id                  SERIAL PRIMARY KEY,
    position_id         INTEGER REFERENCES stage17_tail_positions(id),
    event_type          VARCHAR(30),   -- mark_update | partial_profit_5x | take_profit_20x | resolved | circuit_break
    mark_price          DECIMAL(10,6),
    multiplier          DECIMAL(8,2),
    pnl_usd             DECIMAL(10,4),
    note                TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- 3. Periodic reports (snapshot кожні 6 годин)
CREATE TABLE stage17_tail_reports (
    id                      SERIAL PRIMARY KEY,
    reported_at             TIMESTAMPTZ DEFAULT now(),

    -- Portfolio state
    tail_budget_total_usd   DECIMAL(10,4),
    tail_budget_used_usd    DECIMAL(10,4),
    tail_budget_used_pct    DECIMAL(6,4),

    -- Positions
    open_positions          INTEGER,
    closed_positions        INTEGER,
    max_concurrent          INTEGER,

    -- Performance
    total_realized_pnl_usd  DECIMAL(10,4),
    hit_rate                DECIMAL(6,4),    -- win / (win + loss)
    payout_skew             DECIMAL(6,4),    -- % прибутку від топ-10% wins
    avg_multiplier_wins     DECIMAL(8,2),
    time_to_resolution_median_days DECIMAL(6,2),

    -- By category
    by_category             JSONB,           -- {disasters: {open:2, pnl:-0.5}, ...}

    -- Circuit breaker state
    circuit_breaker_active  BOOLEAN DEFAULT FALSE,
    circuit_breaker_reason  VARCHAR(100)
);
```

### Міграція

```
alembic/versions/0020_stage17_tail_ledger.py
alembic/versions/0021_stage17_tail_ledger_extra_fields.py
```

---

## 11J — Circuit Breaker

### Три умови, три типи захисту

```python
# app/services/signals/tail_circuit_breaker.py

class TailCircuitBreaker:
    """
    Перевіряється НА ПОЧАТКУ кожного tail generation циклу.
    Якщо triggered — жодна нова tail позиція не відкривається.
    """

    def check(self, db: Session, settings: Settings) -> tuple[bool, str]:
        """Returns (is_blocked, reason)"""

        # 1. Budget hard stop
        used_pct = self._get_budget_used_pct(db, settings)
        if used_pct >= 1.0:
            return True, f"tail_budget_exhausted:{used_pct:.1%}"

        # 2. Consecutive losses cooldown
        recent_losses = self._count_recent_consecutive_losses(db)
        if recent_losses >= TAIL_CIRCUIT_BREAKER_CONSECUTIVE_LOSSES:
            last_loss_at = self._last_loss_timestamp(db)
            cooldown_until = last_loss_at + timedelta(hours=TAIL_CIRCUIT_BREAKER_COOLDOWN_HOURS)
            if datetime.now(UTC) < cooldown_until:
                return True, f"consecutive_losses_{recent_losses}:cooldown_until_{cooldown_until.isoformat()}"

        # 3. API degraded → shadow_only
        api_status = self._check_external_apis(settings)
        if api_status["degraded"] and settings.tail_base_rate_mode == "tail_base_rate":
            # Тільки блокуємо tail_base_rate mode; tail_stability може продовжувати
            return True, f"external_api_degraded:{api_status['failed_apis']}"

        return False, ""

    def _count_recent_consecutive_losses(self, db: Session) -> int:
        """Скільки останніх закритих tail позицій були програшами підряд за 24h."""
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        recent_closed = list(db.scalars(
            select(TailPosition)
            .where(
                TailPosition.status == "CLOSED",
                TailPosition.closed_at >= cutoff,
            )
            .order_by(TailPosition.closed_at.desc())
            .limit(TAIL_CIRCUIT_BREAKER_CONSECUTIVE_LOSSES + 1)
        ))
        consecutive = 0
        for pos in recent_closed:
            if float(pos.realized_pnl_usd or 0) < 0:
                consecutive += 1
            else:
                break  # перша перемога зупиняє рахунок
        return consecutive

    def _check_external_apis(self, settings: Settings) -> dict:
        """Швидка перевірка доступності external APIs."""
        failed = []
        if settings.usgs_api_enabled:
            if not self._ping_usgs():
                failed.append("usgs")
        if settings.binance_historical_enabled:
            if not self._ping_binance():
                failed.append("binance")
        return {"degraded": len(failed) > 0, "failed_apis": failed}
```

### Config

```bash
TAIL_CIRCUIT_BREAKER_CONSECUTIVE_LOSSES=3
TAIL_CIRCUIT_BREAKER_COOLDOWN_HOURS=24
TAIL_API_DEGRADED_MODE=shadow_only   # shadow_only | block | continue_without_api
```

---

## 11K — Детермінізм LLM Fallback

### Вимоги

```python
# app/services/signals/base_rate.py — LLM fallback

import hashlib, json

TAIL_BASE_RATE_PROMPT_VERSION = hashlib.sha256(
    TAIL_BASE_RATE_PROMPT.encode()
).hexdigest()[:8]   # "a3f7c12e" — змінюється тільки якщо промпт змінився

def _llm_base_rate(self, market: Market, category: str) -> dict:
    # Input hash для кешування
    input_data = {
        "title": market.title,
        "category": category,
        "market_prob": round(float(market.probability_yes or 0), 4),
        "prompt_version": TAIL_BASE_RATE_PROMPT_VERSION,
    }
    input_hash = hashlib.sha256(json.dumps(input_data, sort_keys=True).encode()).hexdigest()

    # Cache lookup (1 година TTL)
    cached = self._get_cached_base_rate(input_hash)
    if cached:
        return cached

    # LLM call — завжди temperature=0
    response = self.llm_client.complete(
        prompt=TAIL_BASE_RATE_PROMPT.format(**input_data),
        temperature=0,          # ОБОВ'ЯЗКОВО — детермінізм
        max_tokens=300,
    )

    result = self._parse_base_rate_response(response)
    result["prompt_version"] = TAIL_BASE_RATE_PROMPT_VERSION
    result["input_hash"] = input_hash

    # Обов'язкові reason codes
    if "reason_codes" not in result:
        result["reason_codes"] = ["llm_base_rate_fallback"]
    if result.get("our_prob") is None:
        result["reason_codes"].append("llm_parse_failed")
        result["our_prob"] = None   # НЕ дефолтимо до market_prob — краще пропустити

    self._cache_base_rate(input_hash, result, ttl_hours=1)
    return result
```

### Що зберігається в `stage17_tail_positions`

```python
position.prompt_version = base_rate_result["prompt_version"]
# Дозволяє: "покажи всі tail позиції відкриті з промптом v=a3f7c12e"
# і порівняти performance до/після зміни промпту
```

---

## 11L — Категорійні Ліміти

### Ліміти всередині 10% tail бюджету

```python
# app/services/signals/tail_circuit_breaker.py

TAIL_CATEGORY_LIMITS_PCT = {
    "crypto_level":      0.04,   # max 4% портфеля в крипто tail
    "natural_disaster":  0.03,   # max 3%
    "political_stability": 0.02, # max 2%
    "sports_outcome":    0.02,   # max 2%
    "regulatory":        0.01,   # max 1%
    "zero_event":        0.02,   # max 2%
}
# Сума = 14% > 10% загального ліміту — навмисно,
# щоб гнучко заповнювати активні категорії

def can_open_tail_by_category(
    db: Session,
    category: str,
    new_notional: float,
    portfolio_balance: float,
) -> bool:
    """
    Перевіряє чи не перевищить новий вхід категорійний ліміт.
    """
    cat_limit = TAIL_CATEGORY_LIMITS_PCT.get(category, 0.02)

    # Поточна експозиція в цій категорії
    current_exposure = db.scalar(
        select(func.coalesce(func.sum(TailPosition.notional_usd), 0.0))
        .where(
            TailPosition.status == "OPEN",
            TailPosition.tail_category == category,
        )
    ) or 0.0

    new_exposure_pct = (current_exposure + new_notional) / max(portfolio_balance, 1.0)
    return new_exposure_pct <= cat_limit
```

### Логування відмов

```python
if not can_open_tail_by_category(db, category, notional, balance):
    tail_skipped_category_limit += 1
    reasons.append(f"tail_category_limit:{category}:{current_pct:.1%}/{cat_limit:.1%}")
    continue
```

---

## 11M — Tail-Специфічні Метрики

### Нові метрики в reporter

```python
# app/services/dryrun/reporter.py — додати tail section

def build_tail_report(db: Session) -> dict:
    """Окремий звіт тільки для Stage 17 tail positions."""

    closed = list(db.scalars(
        select(TailPosition).where(TailPosition.status == "CLOSED")
    ))
    open_pos = list(db.scalars(
        select(TailPosition).where(TailPosition.status == "OPEN")
    ))

    wins = [p for p in closed if float(p.realized_pnl_usd or 0) > 0]
    losses = [p for p in closed if float(p.realized_pnl_usd or 0) <= 0]

    # 1. Hit rate
    hit_rate = len(wins) / len(closed) if closed else None

    # 2. Payout skew: частка прибутку від топ-10% угод
    if wins:
        win_pnls = sorted([float(p.realized_pnl_usd) for p in wins], reverse=True)
        top_10_count = max(1, len(win_pnls) // 10)
        top_10_pnl = sum(win_pnls[:top_10_count])
        total_win_pnl = sum(win_pnls)
        payout_skew = top_10_pnl / total_win_pnl if total_win_pnl > 0 else None
    else:
        payout_skew = None

    # 3. Time to resolution median
    resolution_days = []
    for p in closed:
        if p.opened_at and p.closed_at:
            days = (p.closed_at - p.opened_at).total_seconds() / 86400
            resolution_days.append(days)
    resolution_days.sort()
    median_days = resolution_days[len(resolution_days)//2] if resolution_days else None

    # 4. Max concurrent (з fills history)
    max_concurrent = db.scalar(
        select(func.max(TailReport.max_concurrent))
    ) or len(open_pos)

    # 5. Avg multiplier на wins
    avg_multiplier = (
        sum(float(p.realized_multiplier or 1) for p in wins) / len(wins)
        if wins else None
    )

    return {
        # Прийняття Stage 17
        "hit_rate_tail": round(hit_rate, 4) if hit_rate else None,
        "payout_skew": round(payout_skew, 4) if payout_skew else None,
        "time_to_resolution_median_days": round(median_days, 1) if median_days else None,
        "max_concurrent_tail_positions": max_concurrent,
        "avg_win_multiplier": round(avg_multiplier, 2) if avg_multiplier else None,

        # Стан
        "open_tail_positions": len(open_pos),
        "closed_tail_positions": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),

        # Фінанси
        "total_realized_pnl_usd": round(sum(float(p.realized_pnl_usd or 0) for p in closed), 4),
        "tail_budget_used_pct": None,   # рахується окремо

        # Категорії
        "by_category": _tail_by_category(closed + open_pos),

        # Circuit breaker
        "circuit_breaker_active": False,  # буде заповнено circuit_breaker.check()
    }
```

### Definition of Acceptance для Stage 17

```python
STAGE17_ACCEPTANCE_CRITERIA = {
    # --- Мінімальна кількість спостережень ---
    "min_closed_positions": 40,        # мінімум 40 закритих позицій перед фінальним вердиктом
                                       # (було 30 — підвищено для статистичної надійності)

    # --- Якість прогнозів ---
    "min_hit_rate": 0.60,              # > 60% win rate

    # --- Payout skew (асиметрія виплат) ---
    "min_payout_skew": 0.50,           # > 50% прибутку від топ-10% угод
    "min_top10pct_wins_count": 3,      # хоча б 3 угоди у топ-10% (захист від 1 lucky win)
    "min_payout_skew_ci_low_80": 0.35, # bootstrap 80% CI нижня межа payout_skew >= 0.35
                                       # Реалізація: bootstrap 1000 resample, percentile 10-й

    # --- Час і мультиплікатор ---
    "max_time_to_resolution_days": 30, # медіана резолюції < 30 днів
    "min_avg_win_multiplier": 5.0,     # середній win > 5x entry
}
# Якщо через 60 днів не виконано → переглянути стратегію
```

**Bootstrap CI для `payout_skew_ci_low_80`:**
```python
import numpy as np

def payout_skew_bootstrap_ci(pnl_list: list[float], n_boot: int = 1000, ci: float = 0.80) -> float:
    """
    Повертає нижню межу (1 - CI)/2 bootstrap розподілу payout_skew.
    pnl_list — список realized_pnl_usd для всіх закритих tail позицій.
    """
    arr = np.array(pnl_list, dtype=float)
    if len(arr) < 10:
        return 0.0  # недостатньо даних

    skews = []
    for _ in range(n_boot):
        sample = np.random.choice(arr, size=len(arr), replace=True)
        total = sample.sum()
        if total <= 0:
            skews.append(0.0)
            continue
        top_n = max(1, len(sample) // 10)
        top_contrib = np.sort(sample)[-top_n:].sum()
        skews.append(float(top_contrib / total))

    lower_tail = (1.0 - ci) / 2.0  # для 80% CI → 10-й перцентиль
    return float(np.percentile(skews, lower_tail * 100))
```

---

## 12. Нові файли

| Файл | Призначення |
|------|-------------|
| `app/services/signals/tail_classifier.py` | Category detection, ambiguity check, mispricing score |
| `app/services/signals/tail_circuit_breaker.py` | 3-condition circuit breaker, category limits |
| `app/services/signals/base_rate.py` | Base rate estimation (all sources), deterministic LLM |
| `app/services/external/usgs.py` | USGS Earthquake API client |
| `app/services/external/binance_history.py` | Binance OHLC + Log-Normal volatility model |
| `app/services/external/openweather.py` | Weather historical data (optional) |
| `app/services/agent_stage7/tail_stage7.py` | Tail-specialized Stage7 prompt |
| `app/services/dryrun/reporter.py` | Додати `build_tail_report()` |
| `scripts/tail_event_backtest.py` | Backtest для tail signals |
| `tests/test_tail_classifier.py` | Ambiguity filter, category detection |
| `tests/test_base_rate.py` | Base rate estimator, LLM determinism |
| `tests/test_tail_circuit_breaker.py` | All 3 circuit breaker conditions |
| `alembic/versions/0020_stage17_tail_ledger.py` | 3 нові таблиці |
| `alembic/versions/0021_stage17_tail_ledger_extra_fields.py` | Backfill-додавання додаткових Stage17 полів (idempotent) |

---

## 13. Фази реалізації

### Phase 1 — Foundation (тиждень 1)
**Незалежна від основного pipeline**

- [ ] `alembic/versions/0020_stage17_tail_ledger.py` — 3 нові таблиці (ПЕРШЕ)
- [ ] `alembic/versions/0021_stage17_tail_ledger_extra_fields.py` — додаткові поля ledger/report
- [ ] `tail_classifier.py` — keyword detection + ambiguity hard block + category mapping
- [ ] `tail_circuit_breaker.py` — всі 3 умови circuit breaker + category limits
- [ ] `base_rate.py` — LLM fallback з temperature=0 + input_hash cache + prompt_version
- [ ] `tail_stage7.py` — спеціалізований промпт
- [ ] `engine.py` — додати tail signal generation
- [ ] `simulator.py` — tail sizing mode, окремий exposure budget (stage17_tail_positions)
- [ ] Тести: `test_tail_classifier.py`, `test_tail_circuit_breaker.py`
- [ ] Deploy + перший dry-run цикл

### Phase 2 — External APIs (тиждень 2)
**Додаємо реальні base rates**

- [ ] `usgs.py` — earthquake daily probability
- [ ] `binance_history.py` — crypto price target probability
- [ ] `base_rate.py` — інтеграція API джерел
- [ ] `tail_event_backtest.py` — backtest на historical Polymarket resolved
- [ ] Порівняти: LLM base rate vs USGS base rate accuracy

### Phase 3 — A/B варіації (тиждень 3)
**Тестуємо всі 3 варіації паралельно**

- [ ] `tail_narrative_fade.py` — post-event signal generation
- [ ] `engine.py` — всі три mode tags
- [ ] `reporter.py` — tail ROI по варіаціях
- [ ] Через 30 днів: вимкнути слабкі варіації

### Phase 4 — Optimization (після 60 днів)
**Коли є статистика**

- [ ] Калібрувати TAIL_MIN_MISPRICING_RATIO по даних
- [ ] Підібрати TAIL_FIXED_POSITION_PCT оптимально
- [ ] Перейти з `micro_fixed` на `micro_kelly` якщо win rate підтверджено

---

## 14. KPI

| Метрика | Ціль через 30 днів | Ціль через 90 днів |
|---------|-------------------|-------------------|
| Tail candidates/цикл | 10–20 | 15–30 |
| Tail positions/тиждень | 5–15 | 20–40 |
| `hit_rate_tail` | ≥ 60% | ≥ 65% |
| `payout_skew` | ≥ 50% | ≥ 70% |
| `avg_win_multiplier` | ≥ 5x | ≥ 10x |
| `time_to_resolution_median_days` | ≤ 21 | ≤ 14 |
| `max_concurrent_tail_positions` | ≤ 20 | ≤ 30 |
| Tail portfolio ROI | > 0% | > +30% |
| Ambiguity blocks/цикл | відстежується | < 30% від candidates |
| Circuit breaker triggers/тиждень | 0 | 0 (healthy system) |

---

## 15. Ризики

| Ризик | Ймовірність | Мітигація |
|-------|------------|-----------|
| LLM base rate галюцинує | Середня | External API verification для природних подій |
| Polymarket не виплачує edge cases | Середня | `resolution_clarity` check в Stage7 |
| Drawdown до першого великого win | Висока | Micro sizing 0.5% → max втрата на позицію $0.50 |
| Resolution ambiguity | Середня | Stage7 `resolution_clarity` check; REMOVE якщо ambiguous |
| External API недоступний | Низька | Fallback на LLM base rate |
| Кластеризація поразок (чорна смуга) | Середня | `TAIL_MAX_TOTAL_EXPOSURE_PCT=10%` жорстко обмежує |

---

## 16. Зв'язок з існуючими модулями

| Модуль | Зміна |
|--------|-------|
| `signals/engine.py` | Додати tail signal generation loop |
| `dryrun/simulator.py` | Tail sizing mode, окремий exposure budget |
| `dryrun/reporter.py` | Tail stats: ROI by variation, avg multiplier |
| `agent_stage7/factory.py` | Route tail signals до tail_stage7.py |
| `models/enums.py` | Нові SignalMode значення |
| `core/config.py` | Нові tail_* settings |
