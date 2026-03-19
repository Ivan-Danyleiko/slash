# ТЗ Stage 16 — Signal Expansion & AI Quality

**Дата:** 2026-03-19
**Статус:** ПЛАНУВАННЯ
**Пріоритет:** HIGH — підвищення ліквідності та якості сигналів після Stage 15

---

## 1. Мета

Stage 15 закрив технічні проблеми (scorer, kelly, exit). Але кількість позицій все одно мала (~1/день).
Stage 16 усуває кореневу причину: **мало кандидатів на вході** і **низька якість our_prob оцінки**.

**Цілі:**
- Збільшити candidates/цикл з ~5 до ~50–100
- Підвищити якість відбору через cross-platform consensus
- Покращити Stage7 AI через portfolio-context і historical data
- Розблокувати uncertainty_liquid з кращим фільтром

---

## 2. Поточний стан (baseline після Stage 15)

| Показник | Значення |
|----------|----------|
| Candidates/цикл | 5–6 (тільки Polymarket CLOB momentum) |
| Win rate (backtest) | 57% (momentum inverted, 23 samples) |
| Uncertainty_liquid | ВИМКНЕНО (9 resolved samples) |
| Manifold сигнали | НЕ генеруються (data є в DB, сигналів нема) |
| Stage7 our_prob | market_price ± delta (без cross-platform) |
| Stage7 контекст | немає інформації про поточний портфель |

---

## 3. Архітектура змін

```
Поточний flow:
  Polymarket CLOB → ARBITRAGE_CANDIDATE (momentum only) → Stage7 → DryRun

Новий flow:
  Polymarket CLOB     ─┐
  Polymarket non-CLOB ─┤→ ARBITRAGE_CANDIDATE → Stage7 (+ portfolio ctx) → DryRun
  Manifold            ─┘         ↑
  Metaculus consensus ───── our_prob estimate (cross-platform)
                                  ↑
                         historical resolutions (RAG)
```

---

## 4. Stage 16A — Manifold як додаткове джерело сигналів

### 4.1 Проблема

Manifold ринки вже синхронізовані в БД (таблиця `markets`, `source = "manifold"`).
Але `generate_signals` генерує `ARBITRAGE_CANDIDATE` тільки для `source = "polymarket"`.
Маємо дані — не використовуємо їх.

### 4.2 Що таке Manifold ринок для нас

- AMM (не CLOB) → entry price = `market_prob + spread_estimate`
- Play-money ринки відфільтровані (вже є `is_real_money` поле або аналог)
- Ліквідність: менша ніж Polymarket, але є ринки з реальними грошима
- Корисні як: (1) окремі торгові можливості, (2) cross-platform consensus signal

### 4.3 Що змінити в `generate_signals`

```python
# app/services/signals/engine.py

SIGNAL_SOURCES_ENABLED = ["polymarket", "manifold"]  # нова константа в .env

def _should_generate_arbitrage(market: Market) -> bool:
    """Перевіряє чи генерувати ARBITRAGE_CANDIDATE для ринку."""
    if market.source not in SIGNAL_SOURCES_ENABLED:
        return False

    # Polymarket: потрібна хоча б probability_yes
    if market.source == "polymarket":
        return market.probability_yes is not None

    # Manifold: тільки real-money ринки, з достатньою активністю
    if market.source == "manifold":
        return (
            market.probability_yes is not None
            and float(market.volume_usd or 0) >= 500    # нижча планка ніж Polymarket
            and market.resolution_time is not None
        )

    return False
```

### 4.4 Signal metadata для Manifold

```python
# Для non-CLOB Manifold сигналів:
signal.metadata_json = {
    "price_source": "gamma",           # не clob
    "market_prob": market.probability_yes,
    "volume_usd": market.volume_usd,
    "estimated_spread_pct": _estimate_spread(market.volume_usd),
    "signal_mode": "uncertainty_liquid" | "momentum",
    "platform": "manifold",
}
```

### 4.5 Обмеження і safety

```python
# Manifold сигнали отримують penalty в composite scorer (вже є через clob_bonus=0)
# Додатково: окремий cap в .env
SIGNAL_MANIFOLD_MAX_PER_CYCLE = 20   # не більше 20 Manifold кандидатів за цикл
SIGNAL_MANIFOLD_MIN_VOLUME    = 500  # мінімум $500 total volume
```

### 4.6 Очікуваний ефект

| До | Після |
|----|-------|
| ~5 Polymarket CLOB кандидатів | ~5 Polymarket CLOB + ~20–40 Manifold |
| Candidates/цикл: 5–6 | Candidates/цикл: 25–50 |

### 4.7 Файли для зміни

- `app/services/signals/engine.py` — розширити `_should_generate_arbitrage()`
- `.env` — додати `SIGNAL_SOURCES_ENABLED=polymarket,manifold`
- `tests/signals/test_engine_manifold.py` — нові тести

---

## 5. Stage 16B — Uncertainty_liquid з кращим фільтром

### 5.1 Поточний стан

Uncertainty_liquid повністю вимкнено через низьку якість (win_rate 33%, 9 resolved samples).
Причина мінусу — невідома: або direction зворотня, або sample bias, або ринок ефективний.

### 5.2 Гіпотеза: direction зворотня

Momentum: ринок рухнув угору → ми купували вгору (program) → треба купувати вниз (contrarian).
Uncertainty_liquid: ринок схилився до 70% YES → ми купували YES (програли 33%).

**Якщо інвертувати:** ринок 70% YES → купуємо NO (ставимо проти consensus).
Win rate з інверсією = 1 - 0.33 = 0.67? Треба перевірити backtest.

### 5.3 Варіант A: Інвертована uncertainty_liquid (contrarian consensus)

```python
# В _resolve_trade_direction:
if signal_mode == "uncertainty_liquid":
    # Contrarian: ставимо проти ринкового консенсусу
    # Якщо ринок каже 70% YES → ми думаємо ринок переоцінив → купуємо NO
    direction = "NO" if original_direction == "YES" else "YES"
```

**Перевірити backtest:**
```bash
python3 scripts/stage15_historical_backtest.py \
  --mode-filter uncertainty_liquid \
  --invert-uncertainty-liquid
```

Якщо win_rate > 55% після інверсії → вмикаємо з інверсією.

### 5.4 Варіант B: Більш строгий фільтр без інверсії

Умови для uncertainty_liquid (без інверсії, але суворіший відбір):

```python
UNCERTAINTY_LIQUID_MIN_DISTANCE = 0.25   # ринок має бути >= 75% або <= 25%
UNCERTAINTY_LIQUID_MIN_VOLUME   = 10_000 # більша ліквідність
UNCERTAINTY_LIQUID_MAX_DAYS     = 60     # тільки короткострокові

def _is_valid_uncertainty_liquid(market: Market, signal: Signal) -> bool:
    prob = float(market.probability_yes or 0.5)
    distance_from_50 = abs(prob - 0.5)
    days = (market.resolution_time - datetime.utcnow()).days

    return (
        distance_from_50 >= UNCERTAINTY_LIQUID_MIN_DISTANCE   # 75%+ або 25%-
        and float(market.volume_usd or 0) >= UNCERTAINTY_LIQUID_MIN_VOLUME
        and days <= UNCERTAINTY_LIQUID_MAX_DAYS
    )
```

### 5.5 Рекомендований порядок

1. Спочатку запустити backtest `--invert-uncertainty-liquid`
2. Якщо ROI > +5% → Варіант A (інвертувати)
3. Якщо ROI < 0 і з інверсією → Варіант B (строгіший фільтр без інверсії)
4. Якщо обидва < 0 → чекати ще 20+ resolved samples

### 5.6 Файли для зміни

- `scripts/stage15_historical_backtest.py` — додати `--invert-uncertainty-liquid` flag
- `app/services/dryrun/simulator.py` — `_resolve_trade_direction()` або hard reject logic

---

## 6. Stage 16C — Portfolio-Aware Stage7 AI

### 6.1 Проблема

Зараз Stage7 AI бачить тільки один сигнал ізольовано. Не знає:
- Скільки вже відкритих позицій
- Яка категорія домінує (3 позиції crypto → може не треба 4-ту)
- Який загальний exposure

Результат: AI може давати KEEP на correlated ринки → концентрація ризику.

### 6.2 Portfolio context у промпті

```python
# app/services/agent_stage7/context_builder.py (новий файл)

def build_portfolio_context(db) -> str:
    """Формує короткий опис поточного портфеля для Stage7 промпту."""
    from app.services.dryrun.reporter import get_portfolio_snapshot
    snapshot = get_portfolio_snapshot(db)

    if snapshot["open_positions"] == 0:
        return "Portfolio: empty (no open positions)"

    # Breakdown по категоріях
    category_counts = snapshot.get("category_breakdown", {})
    cat_str = ", ".join(f"{k}: {v}" for k, v in category_counts.items())

    return f"""
Current portfolio state:
- Open positions: {snapshot['open_positions']}
- Cash available: ${snapshot['cash_usd']:.0f} of ${snapshot['initial_balance_usd']:.0f}
- Total exposure: {snapshot['open_positions_pct']:.0%}
- Category breakdown: {cat_str}
- Time bucket fill: {snapshot.get('bucket_summary', 'N/A')}

Consider: avoid opening positions that increase concentration in already-heavy categories.
"""
```

### 6.3 Передача контексту в Stage7

```python
# app/services/agent_stage7/stage7_agent.py

async def evaluate_signal(signal, market, db) -> Stage7Decision:
    portfolio_ctx = build_portfolio_context(db)

    prompt = f"""
{portfolio_ctx}

---

Market to evaluate:
Title: {market.title}
Current probability: {market.probability_yes:.2%}
...
"""
    # далі як зараз
```

### 6.4 Що це дає

- AI не дає KEEP на 5-й крипто ринок якщо вже 4 відкриті
- Краще розподіл по категоріях автоматично
- Більш реалістичні kelly оцінки ("портфель вже 30% зайнятий")

### 6.5 Файли для зміни

- `app/services/agent_stage7/context_builder.py` — **новий файл**
- `app/services/agent_stage7/stage7_agent.py` — додати `portfolio_ctx` в промпт
- `app/services/dryrun/reporter.py` — додати `get_portfolio_snapshot()` функцію

---

## 7. Stage 16D — Cross-Platform Consensus для our_prob

### 7.1 Проблема

Поточна `_estimate_our_prob_yes` для більшості сигналів повертає `market_price ± delta`.
Це значить Kelly ≈ 0 (ринок сам собі і є best estimate).

Якщо **Manifold або Metaculus** дають іншу ймовірність для того самого ринку — це реальна інформація.

### 7.2 Cross-platform probability lookup

```python
# app/services/dryrun/cross_platform.py (новий файл)

def get_cross_platform_prob(signal: Signal, market: Market, db) -> float | None:
    """
    Шукає той самий ринок на інших платформах.
    Повертає mid-probability якщо знайшов, None якщо ні.

    Логіка:
    1. Шукаємо в duplicate_market_links (вже є в БД зі Stage divergence detection)
    2. Якщо є дублікат на Manifold/Metaculus → беремо їх probability_yes
    3. Повертаємо average між платформами як наш consensus estimate
    """
    from app.models import DuplicateMarketLink, Market as MarketModel

    duplicates = db.query(DuplicateMarketLink).filter(
        (DuplicateMarketLink.market_id_a == market.id) |
        (DuplicateMarketLink.market_id_b == market.id)
    ).all()

    if not duplicates:
        return None

    probs = [float(market.probability_yes)]   # починаємо з Polymarket
    for dup in duplicates:
        other_id = dup.market_id_b if dup.market_id_a == market.id else dup.market_id_a
        other_market = db.get(MarketModel, other_id)
        if other_market and other_market.probability_yes is not None:
            probs.append(float(other_market.probability_yes))

    if len(probs) < 2:
        return None  # немає cross-platform даних

    return sum(probs) / len(probs)   # simple average consensus
```

### 7.3 Інтеграція в kelly.py

```python
# app/services/dryrun/kelly.py

def estimate_our_prob_from_context(signal, market, stage7_decision, db) -> float:
    """
    Ієрархія оцінки our_prob:
    1. Cross-platform consensus (найсильніший сигнал)
    2. Stage7 evidence market_prob (якщо є)
    3. Momentum CONTRARIAN_EDGE = 0.07
    4. Fallback = market_price (kelly = 0)
    """
    # 1. Cross-platform
    cross_prob = get_cross_platform_prob(signal, market, db)
    if cross_prob is not None:
        # Вага: 60% cross-platform, 40% Polymarket
        return 0.6 * cross_prob + 0.4 * float(market.probability_yes or 0.5)

    # 2. Stage7 evidence
    if stage7_decision and stage7_decision.evidence_bundle.get("market_prob"):
        return float(stage7_decision.evidence_bundle["market_prob"])

    # 3. Momentum contrarian
    mode = str(signal.signal_mode or "").lower()
    if mode == "momentum":
        current = float(market.probability_yes or 0.5)
        move = float(signal.metadata_json.get("signed_recent_move") or
                     signal.metadata_json.get("price_move") or 0.0)
        CONTRARIAN_EDGE = 0.07
        sign = 1.0 if move >= 0 else -1.0
        return min(0.95, max(0.05, current - sign * CONTRARIAN_EDGE))

    # 4. Fallback
    return float(market.probability_yes or 0.5)
```

### 7.4 Очікуваний ефект

| Випадок | До | Після |
|---------|-----|-------|
| Polymarket 40%, Manifold 55% | our_prob=0.40, kelly=0 | our_prob=0.49, kelly>0 |
| Polymarket 60%, Metaculus 65% | our_prob=0.60, kelly=0 | our_prob=0.63, kelly>0 |
| Тільки Polymarket | our_prob=market_price | без змін (0.07 edge для momentum) |

### 7.5 Файли для зміни

- `app/services/dryrun/cross_platform.py` — **новий файл**
- `app/services/dryrun/kelly.py` — замінити `_estimate_our_prob_yes` на `estimate_our_prob_from_context`
- `app/services/dryrun/simulator.py` — передавати `db` у kelly estimator

---

## 8. Stage 16E — Historical RAG для Stage7

### 8.1 Проблема

Stage7 AI оцінює кожен ринок без контексту того що відбувалось зі схожими ринками в минулому.
Приклад: "NBA Western Conference Finals" — ми вже мали подібні ринки. Чи резолюювались вони коректно? Чи були ambiguous?

### 8.2 Historical similarity lookup

```python
# app/services/agent_stage7/historical_rag.py (новий файл)

def get_similar_resolved_markets(market: Market, db, limit=3) -> list[dict]:
    """
    Знаходить схожі вже resolved ринки для Stage7 контексту.
    Пошук по: category + keywords в title.
    """
    from app.models import Market as MarketModel
    from sqlalchemy import func

    keywords = _extract_keywords(market.title)  # видаляємо стоп-слова
    similar = (
        db.query(MarketModel)
        .filter(
            MarketModel.status == "resolved",
            MarketModel.category == market.category,
            MarketModel.source == market.source,
            # fuzzy title match через PostgreSQL similarity
            func.similarity(MarketModel.title, market.title) > 0.3
        )
        .order_by(func.similarity(MarketModel.title, market.title).desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "title": m.title,
            "resolved_value": m.source_payload.get("resolutionValue"),
            "final_probability": m.probability_yes,
            "resolution_date": str(m.resolution_time.date()) if m.resolution_time else None,
        }
        for m in similar
    ]
```

### 8.3 Формат в Stage7 промпті

```
Similar resolved markets (for context):
1. "OKC Thunder NBA WCF 2025" → resolved YES (probability was 0.72 at close)
2. "NBA WCF Game 7 2024" → resolved NO (probability was 0.61 at close)
3. "NBA Finals Winner 2024" → resolved YES (probability was 0.58 at close)

Base rate: 2/3 similar markets resolved YES (67%)
```

### 8.4 Умови використання

- Вмикається тільки якщо знайдено >= 2 схожих resolved ринки
- Не використовується якщо category = "crypto" (дуже різнорідні ринки)
- RAG context додається як окремий блок перед основним промптом

### 8.5 Вимога: PostgreSQL pg_trgm

```sql
-- Міграція: увімкнути trigram extension для similarity()
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_markets_title_trgm ON markets USING gin(title gin_trgm_ops);
```

### 8.6 Файли для зміни

- `app/services/agent_stage7/historical_rag.py` — **новий файл**
- `app/services/agent_stage7/stage7_agent.py` — додати RAG context в промпт
- Alembic міграція `0021_pg_trgm_markets` — pg_trgm extension + index

---

## 9. Параметри конфігурації (нові .env змінні)

```bash
# Stage 16A — Manifold signals
SIGNAL_SOURCES_ENABLED=polymarket,manifold
SIGNAL_MANIFOLD_MAX_PER_CYCLE=20
SIGNAL_MANIFOLD_MIN_VOLUME=500

# Stage 16B — Uncertainty liquid
UNCERTAINTY_LIQUID_ENABLED=false          # поки вимкнено, ввімкнути після backtest
UNCERTAINTY_LIQUID_MIN_DISTANCE=0.25      # abs(prob - 0.5) >= 0.25
UNCERTAINTY_LIQUID_MIN_VOLUME=10000
UNCERTAINTY_LIQUID_MAX_DAYS=60
UNCERTAINTY_LIQUID_INVERT=false           # ввімкнути якщо backtest підтвердить

# Stage 16C — Portfolio-aware Stage7
STAGE7_PORTFOLIO_CONTEXT_ENABLED=true

# Stage 16D — Cross-platform consensus
DRYRUN_CROSS_PLATFORM_PROB_WEIGHT=0.60    # вага cross-platform vs Polymarket
DRYRUN_CROSS_PLATFORM_MIN_DIFF=0.05       # мінімальна різниця щоб вважати значущою

# Stage 16E — Historical RAG
STAGE7_HISTORICAL_RAG_ENABLED=true
STAGE7_HISTORICAL_RAG_MIN_SIMILAR=2
STAGE7_HISTORICAL_RAG_LIMIT=3
```

---

## 10. Фази реалізації

### Phase 1 — Manifold сигнали (тиждень 1)
**Найбільший вплив на кількість кандидатів**

- [ ] `app/services/signals/engine.py` — розширити `_should_generate_arbitrage()`
- [ ] `.env` — `SIGNAL_SOURCES_ENABLED=polymarket,manifold`
- [ ] Тести: `tests/signals/test_engine_manifold.py`
- [ ] Deploy + перевірка candidates/цикл
- [ ] Backtest `--invert-uncertainty-liquid` → рішення по 16B

### Phase 2 — Uncertainty_liquid (тиждень 1–2)
**Залежить від результату backtest з Phase 1**

- [ ] `scripts/stage15_historical_backtest.py` — `--invert-uncertainty-liquid` flag
- [ ] Запустити backtest, оцінити ROI
- [ ] `app/services/dryrun/simulator.py` — вмикаємо з відповідною логікою
- [ ] `.env` — `UNCERTAINTY_LIQUID_ENABLED=true`, `UNCERTAINTY_LIQUID_INVERT=?`

### Phase 3 — Cross-platform our_prob (тиждень 2)
**Покращує Kelly → більші позиції на реальних арбітражах**

- [ ] `app/services/dryrun/cross_platform.py` — новий файл
- [ ] `app/services/dryrun/kelly.py` — `estimate_our_prob_from_context()`
- [ ] `app/services/dryrun/simulator.py` — передавати db у kelly estimator
- [ ] Тести: `tests/dryrun/test_cross_platform.py`

### Phase 4 — Portfolio-aware Stage7 (тиждень 2–3)
**Покращує якість, не кількість**

- [ ] `app/services/agent_stage7/context_builder.py` — новий файл
- [ ] `app/services/agent_stage7/stage7_agent.py` — portfolio_ctx в промпт
- [ ] `app/services/dryrun/reporter.py` — `get_portfolio_snapshot()`

### Phase 5 — Historical RAG (тиждень 3)
**Підвищує точність Stage7 рішень**

- [ ] Alembic `0021_pg_trgm_markets`
- [ ] `app/services/agent_stage7/historical_rag.py` — новий файл
- [ ] `app/services/agent_stage7/stage7_agent.py` — RAG context
- [ ] Тести: `tests/agent_stage7/test_historical_rag.py`

---

## 11. KPI / Definition of Done

| Метрика | Після Stage 15 | Ціль Stage 16 |
|---------|---------------|--------------|
| Candidates/цикл | 5–6 | **50–100** |
| Позицій/день | ~1 | **10–20** |
| Джерела сигналів | Polymarket CLOB only | Polymarket + Manifold |
| Signal modes активні | momentum only | momentum + uncertainty_liquid |
| Kelly > 0 на cross-platform | 0% | **30–50%** ринків |
| Stage7 KEEP rate | ~60% | ~55% (краща якість при тому ж rate) |
| Win rate (50+ closed) | 57% (23 samples) | > 55% |

---

## 12. Ризики

| Ризик | Ймовірність | Мітигація |
|-------|------------|-----------|
| Manifold ринки з неточними цінами | Середня | `clob_bonus=0` знижує score; max 20 Manifold/цикл |
| Uncertainty_liquid після інверсії все одно збиткова | Низька | Перевіряємо backtest перед вмиканням |
| Cross-platform consensus з малою різницею → false edge | Середня | `MIN_DIFF=0.05` — ігноруємо різниці < 5pp |
| RAG з неякісними historical matches | Низька | `MIN_SIMILAR=2`, `similarity > 0.3` |
| Portfolio context збільшує латентність Stage7 | Низька | Context < 200 токенів, не суттєво |
| pg_trgm extension не встановлена на сервері | Низька | Перевірити: `SELECT * FROM pg_extension WHERE extname='pg_trgm'` |

---

## 13. Зв'язок з існуючими модулями

| Модуль | Зміна |
|--------|-------|
| `signals/engine.py` | Manifold як джерело ARBITRAGE_CANDIDATE |
| `dryrun/simulator.py` | uncertainty_liquid logic, cross_platform prob |
| `dryrun/kelly.py` | `estimate_our_prob_from_context()` |
| `dryrun/reporter.py` | `get_portfolio_snapshot()` для portfolio context |
| `agent_stage7/stage7_agent.py` | portfolio ctx + RAG в промпт |
| `agent_stage7/context_builder.py` | **Новий** |
| `agent_stage7/historical_rag.py` | **Новий** |
| `dryrun/cross_platform.py` | **Новий** |
| Alembic | `0021_pg_trgm_markets` |
