# Research Brief: Prediction Market Trading System Optimization

**Мета:** Знайти найкращі алгоритми, підходи та практики для оптимізації системи торгівлі на ринках передбачень (prediction markets). Система вже побудована і працює — потрібно знайти що покращити, щоб заробляти більше.

---

## 1. Контекст: Що таке наша система

### 1.1 Загальний опис

Ми будуємо автоматизовану систему, яка:
1. Збирає дані з платформ prediction markets (Polymarket, Manifold, Metaculus)
2. Знаходить сигнали (можливості для торгівлі)
3. Фільтрує їх через AI-агент
4. Відкриває паперові позиції (dry-run симулятор)

**Prediction market** — це ринок, де люди ставлять на бінарні результати ("Чи переможе X?" → YES/NO). Ціна YES-токена = implied probability (0.0–1.0). Якщо купити YES за $0.40 і ринок резолюється YES → отримуєш $1.00 за кожен $0.40 вкладений.

### 1.2 Платформи

**Polymarket** (головна):
- Децентралізована платформа на Polygon (EVM)
- CLOB (Central Limit Order Book) — реальний order book з bid/ask цінами
- Ліквідні ринки: $10k–$10M+ total volume
- Ринки: спорт, політика, крипта, макро
- API: публічний Gamma API (метадані) + CLOB API (ціни, order book)

**Manifold**:
- Платформа з play-money та реальними грошима
- AMM (автоматичний маркет-мейкер), не CLOB
- Широкий спектр ринків, менша ліквідність
- Корисна для: виявлення нових ринків, cross-platform порівняння

**Metaculus**:
- Агрегатор прогнозів (не ставки, але community probability)
- Немає реальної торгівлі, але є community consensus probability
- Корисна для: calibration reference, divergence detection

### 1.3 Типи сигналів які ми генеруємо

**ARBITRAGE_CANDIDATE** — головний тип для торгівлі:
- Підтипи: `momentum` (ціна різко рухалась) і `uncertainty_liquid` (ринок далеко від 50/50 + достатньо ліквідний)
- Генерується тільки для Polymarket ринків
- Вимагає: наявність CLOB bid/ask цін

**DIVERGENCE** — крос-платформна розбіжність:
- Той самий ринок є на двох платформах з різними цінами
- Поки не торгується в dry-run (технічна обмеженість)

**RULES_RISK** — неоднозначні умови резолюції ринку

### 1.4 AI-фільтр (Stage 7)

Кожен сигнал проходить через AI:
- Input: опис ринку, ціна, EV, ліквідність, правила резолюції
- Output: рішення KEEP / MODIFY / REMOVE / SKIP
- Також генерує: `kelly_fraction`, `expected_ev_pct`, `confidence`
- Реалізовано через: Groq (Llama 3.1) → Gemini 2.5 Flash → OpenRouter (fallback chain)

### 1.5 Dry-Run симулятор (поточний стан)

Поточні параметри:
```
Портфель: $100 (paper money)
Position sizing: 3–5% від балансу (= $3–5 per trade)
Hard limits:
  - Max spread: 8%
  - Min volume: $5,000
  - Max days to resolution: 180
  - Requires CLOB price (best_ask_yes)
Stop-loss: якщо ціна впала на 50% від entry
Take-profit: якщо захоплено 65% від максимального можливого прибутку
Time-exit: після 14 днів з EV < 3%
```

**Поточна проблема:** Відкривається лише ~4–5 позицій на день з 3500+ ринків (conversion rate 0.14%).

---

## 2. Проблеми для дослідження

---

### ПРОБЛЕМА A: Оцінка торгових можливостей (Entry Scoring)

#### Поточний стан
Система відкидає кандидатів по окремих порогових значеннях:
- `daily_ev < 0.05%` → відкинути
- `spread > 8%` → відкинути
- `volume < $5,000` → відкинути
- `confidence < 0.35` → відкинути
- `kelly = 0` → відкинути або мінімальна ставка

Це "waterfall" підхід — кожен фільтр незалежний, між ними немає trade-off.

#### Проблема
Ринок може мати: низький `daily_ev` але дуже низький spread і великий volume → статистично це хороша угода, але система відкидає. Або навпаки: проходить всі фільтри але насправді погана якість.

#### Питання для дослідження
1. **Composite scoring для prediction market entry signals** — які підходи існують? Як комбінувати EV, Kelly, spread, ліквідність, confidence в один score?
2. **Expected Value calculation для prediction markets** — як правильно рахувати EV враховуючи bid/ask spread? Чи варто рахувати EV по bid-ціні (selling price) чи ask (buying price)?
3. **Calibration score** — як враховувати наскільки "добре калібрований" ринок? Є дослідження щодо каліброваності Polymarket?
4. **Feature importance** для prediction market profitability — що найбільше корелює з profitable trades: volume, spread, implied probability, distance from 50/50, days to resolution?

**Що шукати:** academic papers, Polymarket research, LessWrong posts, PredictIt/Kalshi analyses, Manifold statistics.

---

### ПРОБЛЕМА B: Розрахунок Kelly Fraction

#### Поточний стан
Kelly fraction надходить від AI (LLM), що ненадійно — LLM часто повертає kelly=0 або некоректні значення. Fallback — мінімальна ставка 3%.

**Класична формула Kelly:**
```
f* = (p*b - q) / b
де: p = probability of win, q = 1-p, b = net odds (profit per dollar bet)
```

Для prediction market де YES коштує $0.40:
- b = (1.0 - 0.40) / 0.40 = 1.5 (якщо win)
- Якщо ми думаємо real_prob = 0.55 (а ринок каже 0.40):
- f* = (0.55 * 1.5 - 0.45) / 1.5 = (0.825 - 0.45) / 1.5 = 0.25 (25%)

#### Проблема
1. Kelly може давати великі ставки (25%+) — ризиковано
2. Fractional Kelly (f*/4 або f*/2) — як вибрати дільник?
3. Як врахувати невпевненість в нашій оцінці `real_prob`? (модель може помилятися)
4. Як Kelly масштабується коли є **кілька відкритих позицій одночасно**?

#### Питання для дослідження
1. **Fractional Kelly для prediction markets** — який дільник (1/4, 1/3, 1/2) рекомендується для початкового portfolo з невизначеною edge?
2. **Kelly з урахуванням model uncertainty** — якщо наша оцінка `real_prob` може відрізнятися від ринкової на ±10%, як це впливає на розмір ставки?
3. **Portfolio Kelly (simultaneous bets)** — як правильно рахувати розмір кожної позиції коли є 5–10 відкритих одночасно? Чи потрібна кореляційна матриця?
4. **Drawdown-adjusted Kelly** — є варіанти Kelly, які оптимізують не тільки EV але й drawdown?
5. **Benchmarks** — які типові kelly fractions у profitable prediction market traders? Дані з Polymarket/Manifold leaderboards?

**Що шукати:** Kelly criterion papers, prediction market trading guides, Manifold/Polymarket power users strategies, quantitative finance literature.

---

### ПРОБЛЕМА C: Торгівля без CLOB (Non-CLOB Markets)

#### Поточний стан
Наша система вимагає `best_ask_yes` (CLOB ціну) для відкриття позиції. Тільки ~20% ринків мають цю ціну (ті що мають достатню ліквідність для CLOB запитів).

Решта 80% ринків мають лише `market_prob` — implied probability з Gamma API (середньозважена через AMM або community estimate).

#### Питання для дослідження
1. **Чи можна торгувати на Polymarket без CLOB ціни?** Яка різниця між gamma `market_prob` і реальною CLOB mid-price? Наскільки вони відрізняються?
2. **AMM-based entry price estimation** — для ринків з AMM (не CLOB), як оцінити реальну ціну входу з урахуванням slippage?
3. **Liquidity proxy metrics** — якщо немає CLOB, що є найкращим proxy для ліквідності: total volume? Open interest? 24h volume? Кількість traders?
4. **Симуляція без CLOB** — для dry-run paper trading, чи коректно використовувати `market_prob` як entry price? Які корекції потрібні (slippage estimate, spread estimate)?

---

### ПРОБЛЕМА D: Volume як фільтр — чи потрібен?

#### Поточний стан
Мінімальний volume: $5,000. Якщо volume нижче — ринок відкидається.

#### Питання для дослідження
1. **Яку роль відіграє total volume для prediction market ліквідності?** Це корелює з order book depth?
2. **Чи потрібен volume filter якщо є CLOB bid/ask ціна?** CLOB ціна вже означає що хтось стоїть в order book — тоді навіщо volume мінімум?
3. **Minimum bet size на Polymarket** — яка реальна мінімальна ставка? Чи є ринки де мінімум > $10?
4. **Slippage vs volume** — якою є типова залежність між total volume і slippage для bet size $50–500?
5. **Volume vs Open Interest** — що краще корелює з ліквідністю і можливістю виходу з позиції?

---

### ПРОБЛЕМА E: Time-Horizon Portfolio Management

#### Поточний стан
Немає обмеження на розподіл позицій по часу. Система може відкрити 5 позицій де всі резолюються через 150 днів — весь капітал заморожений на 5 місяців.

#### Питання для дослідження
1. **Time-bucketed capital allocation** — чи є дослідження щодо оптимального розподілу capital across time horizons для prediction markets?
2. **Liquidity premium for near-term markets** — ринки що резолюються скоро (7–30 днів) зазвичай мають більший daily EV? Чи є дані?
3. **Opportunity cost calculation** — як порівнювати ринок з 90 днів +3% total EV vs ринок з 7 днів +1% total EV? Це 0.033%/день vs 0.143%/день — очевидно другий кращий.
4. **Optimal portfolio turnover** — як часто оновлювати позиції для максимального ROI? Щодня? Щотижня?
5. **Time-decay для prediction markets** — як змінюється ціна ринку з наближенням до resolution? Чи є "theta decay" аналог?

---

### ПРОБЛЕМА F: Cross-Platform Arbitrage

#### Поточний стан
Система виявляє однакові ринки на різних платформах (duplicate detection). Але не торгує цим арбітражем.

**Приклад:**
- Polymarket: "Biden буде президентом 2025?" → YES = $0.04
- Manifold: той самий ринок → YES = $0.08
- Реальна арбітражна можливість: купити YES на Polymarket, продати (або ставити NO) на Manifold

#### Питання для дослідження
1. **Cross-platform prediction market arbitrage** — чи є дослідження, чи це profitable? Які ризики?
2. **Execution risk** — основна проблема арбітражу: обидві платформи мають різний час резолюції, різні правила. Як це враховувати?
3. **Information arbitrage vs price arbitrage** — різниця між: (a) ринок на Polymarket дає нову інформацію для Manifold ринку і (b) реальний price arbitrage де обидві позиції хеджують одна одну.
4. **Manifold AMM arbitrage** — чи можна automated-торгувати на Manifold? Є API для виставлення ставок?
5. **Вартість виконання** — Polymarket потребує Polygon транзакцій (gas fees). Як це впливає на мінімальний розмір profitable арбітражу?

---

### ПРОБЛЕМА G: Оптимальна Стратегія Виходу (Exit Strategy)

#### Поточний стан
```
Stop-loss: mark_price < entry_price * 0.50  (тобто ціна впала в 2 рази)
Take-profit: mark_price >= entry + (1 - entry) * 0.65  (захоплено 65% max прибутку)
Time-exit: після 14 днів якщо unrealized EV < 3%
```

#### Питання для дослідження
1. **Оптимальний stop-loss для prediction markets** — 50% дуже агресивний (ринок може відновитись). Які дослідження є по оптимальному stop-loss рівню?
2. **Take-profit strategy** — коли краще закривати: фіксований %, trailing stop, чи при певній implied probability threshold?
3. **Trailing stop для prediction markets** — якщо YES купили за $0.40 і ціна виросла до $0.75, trailing stop від $0.75 чи від $0.40?
4. **Resolution signal detection** — як рано ринок "знає" правильний результат? За скільки днів до резолюції implied probability стає > 90%?
5. **Time-decay exit** — чи варто продавати позицію якщо resolution > 60 днів, але EV все одно є? Калькуляція opportunity cost?
6. **AI REMOVE signal** — якщо AI наступного дня каже REMOVE на вже відкриту позицію, чи закривати негайно? Дослідження по mean-reversion в prediction markets.

---

### ПРОБЛЕМА H: AI-Assisted Signal Filtering

#### Поточний стан
AI отримує опис ринку і повертає KEEP/MODIFY/REMOVE/SKIP + kelly_fraction + confidence. Проблеми:
- LLM може галюцинувати kelly значення
- SKIP кешується в БД, старі помилкові SKIP блокують нові аналізи
- AI не враховує поточний стан портфеля

#### Питання для дослідження
1. **Prompting для фінансового аналізу** — які prompting техніки дають найкращі результати для оцінки prediction market opportunities? Chain-of-thought? Few-shot з прикладами?
2. **LLM для Kelly calculation** — чи є дослідження де LLM рахує Kelly fraction? Що точніше: LLM чи deterministic formula?
3. **Structured output для фінансових рішень** — як структурувати LLM output щоб отримати надійні числові значення (не галюцинації)?
4. **Portfolio-aware AI filtering** — як подавати в контекст AI поточний стан портфеля щоб уникнути концентрації ризиків?
5. **Confidence calibration для LLM** — як LLM confidence score корелює з реальною accuracy? Чи варто довіряти LLM впевненості?
6. **RAG для market context** — чи корисно додавати historical resolution data аналогічних ринків до контексту AI?

---

### ПРОБЛЕМА I: Метрики для оцінки ефективності системи

#### Поточний стан
Відстежуємо: win rate, ROI, realized/unrealized P&L, Kelly expectation.

#### Питання для дослідження
1. **Prediction market trading metrics** — які метрики важливіші для prediction market trading vs traditional trading? Sharpe ratio, Sortino ratio, Brier score?
2. **Brier Score для власних прогнозів** — як рахувати Brier score для нашої системи? Чи варто це впроваджувати?
3. **Backtesting для prediction markets** — як правильно backtestувати стратегію? Проблема: ринки не повторюються.
4. **Bankroll growth rate** — як рахувати очікуваний ріст портфелю з урахуванням Kelly? Geometric mean vs arithmetic mean.
5. **Statistical significance** — скільки trades потрібно для статистично значимого висновку про profitability? При win rate 55% і std deviation?
6. **Alpha decay** — як виявити що наш edge зникає (market becomes more efficient)?

---

### ПРОБЛЕМА J: Додаткові джерела ринків і даних

#### Поточний стан
Polymarket — основне джерело. Manifold і Metaculus підключені але не торгуються.

#### Питання для дослідження
1. **Prediction market platforms comparison 2024–2025** — які платформи найбільш ліквідні і profitable для trading? Polymarket vs Kalshi vs Metaculus vs PredictIt (US only)?
2. **Kalshi API** — чи є публічний API Kalshi для ринків і цін? Яка ліквідність?
3. **News-based signal generation** — чи є open-source системи які генерують prediction market signals на основі новин?
4. **Alternative data sources** — які альтернативні дані корисні для prediction market edge: Twitter sentiment, prediction market volume spikes, court filings, sports statistics?
5. **Metaculus community forecast quality** — наскільки точні Metaculus community forecasts? Чи є calibration data?
6. **Prediction market aggregators** — чи є сервіси які агрегують ціни з кількох prediction platforms в одному API?

---

### ПРОБЛЕМА K: Специфіка Polymarket CLOB

#### Поточний стан
Ми використовуємо Polymarket CLOB тільки для отримання bid/ask цін. Реальна торгівля потребує підпису гаманця (MetaMask/Polygon).

#### Питання для дослідження
1. **Polymarket CLOB mechanics** — як працює order book на Polymarket? Market orders vs limit orders? Які типові spreads для різних market sizes?
2. **Polymarket CLOB API документація** — де знайти повну документацію? Які endpoints для: order placement, order cancellation, fill history?
3. **Gas fees на Polygon** — яка типова вартість транзакції для Polymarket trade? Чи є smart contract abstraction?
4. **Polymarket bot trading** — чи є відомі automated trading bots на Polymarket? Які стратегії вони використовують? (публічна інформація)
5. **Minimum order size** — яка реальна мінімальна ставка на Polymarket в доларах?
6. **Order book depth analysis** — для $100 ставки, яке типове slippage? Є дослідження по Polymarket market microstructure?

---

## 3. Що шукати (загальні запити)

Крім вузькоспеціалізованих питань, корисно знайти:

1. **"Prediction market trading strategy"** — будь-які публічні стратегії, playbooks
2. **"Polymarket arbitrage strategy"** — конкретно для Polymarket
3. **"Kelly criterion prediction markets"** — як люди застосовують Kelly
4. **"Manifold Markets trading bot"** — open-source bots з відомими стратегіями
5. **"Prediction market alpha"** — звідки береться edge? (academic або blog)
6. **LessWrong prediction markets posts** — лучший community для теорії
7. **"Superforecaster portfolio"** — як superforecasters диверсифікують прогнози
8. **"Metaculus calibration study"** — accuracy Metaculus community
9. **GitHub: polymarket trading bot** — open-source implementations
10. **"Prediction market market making"** — стратегії маркет-мейкерів

---

## 4. Формат відповіді

Для кожної проблеми хочемо отримати:

1. **Найкращий знайдений підхід** — з посиланням на джерело
2. **Конкретний алгоритм або формула** — якщо є
3. **Trade-offs** — переваги і недоліки підходу
4. **Benchmark значення** — які типові числа у практиків?
5. **Що не працює** — відомі підводні камені

Якщо є кілька підходів — порівняти їх таблицею.

---

## 5. Пріоритет

**Найважливіші питання (почати з них):**

1. **Composite entry scoring** (Проблема A) — найбільший вплив на кількість угод
2. **Kelly fraction calculation** (Проблема B) — найбільший вплив на розмір угод
3. **Volume filter значення** (Проблема D) — простий але важливий параметр
4. **Optimal exit strategy** (Проблема G) — впливає на win rate
5. **Time-horizon management** (Проблема E) — capital efficiency

**Менш критичні але важливі:**
- Cross-platform arbitrage (Проблема F)
- Non-CLOB trading (Проблема C)
- Additional sources (Проблема J)

---

*Контекст: система побудована на Python/FastAPI/PostgreSQL/Celery. Рішення мають бути реалізовані як Python-функції або алгоритми. Ми не обмежені конкретними бібліотеками.*
