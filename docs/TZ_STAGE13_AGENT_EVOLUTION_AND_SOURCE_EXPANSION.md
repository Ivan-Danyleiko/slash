# ТЗ Stage 13: Agent Evolution, Self-Learning та Multi-Source Expansion

## 1. Контекст і мотивація

Stage 7 побудував shadow-режим AI-агента (LLM verifier) та інфраструктуру для збору рішень.
Stage 12 описав framework для підключення нових джерел.

Stage 13 об'єднує два напрямки:
1. **Агент**: перетворити stateless LLM-verifier на self-learning agentic систему з пам'яттю, інструментами та feedback loop.
2. **Джерела даних**: підключити Kalshi (data-only), Betfair (data + торгівля з UA KYC), покращити Polymarket CLOB.

Ключовий контекст:
- Поточний агент не навчається — кожне рішення з нуля, без пам'яті про минулі результати.
- 68 resolved сигналів — мало для валідації; більше джерел = швидший feedback loop.
- Polymarket має два API: **Gamma** (поточний) і **CLOB** (для виконання угод, частково закодований).
- KYC обмеження: Kalshi — тільки USA, Betfair — приймає UA документи.

---

## 2. Scope

### In Scope

**Блок A — Agent Evolution:**
1. Self-learning context: агент отримує статистику власних минулих рішень перед кожним новим.
2. Claude API (Anthropic) як primary LLM для deep reasoning.
3. Agentic tools: web search, news fetch, cross-reference — для топ-сигналів.
4. Feedback loop: resolved outcomes → оновлення контексту наступного запуску.
5. MODIFY рішення: агент повинен давати кількісне коригування впевненості, а не тільки KEEP/SKIP.

**Блок B — Source Expansion:**
1. IBKR ForecastEx collector: data + execution для macro event contracts.
2. Polymarket CLOB: повна активація bid/ask для точного execution.
3. Збільшення ліміту Polymarket: з 100 до 500+ ринків.

**Блок C — Profit Validation:**
1. Мінімум 200 resolved DIVERGENCE сигналів для статистичної валідації.
2. Dashboard: win rate по divergence buckets, cost-adjusted EV, Kelly sizing.
3. Acceptance gate: чи можна переходити з shadow до real trading.

### Out of Scope

1. Повна автономна торгівля без підтвердження людини.
2. Kalshi trading (KYC обмеження).
3. Fine-tuning власних моделей.

---

## 3. Поточний стан (baseline)

| Компонент | Стан |
|-----------|------|
| Stage 7 shadow agent | ✅ працює, Groq+Gemini, rate-limited |
| Polymarket Gamma API | ✅ 100 ринків, оновлення 24h |
| Polymarket CLOB | ⚠️ частково (код є, `clob_enabled=false`) |
| Kalshi collector | ⚠️ код є, `kalshi_enabled=false`, немає API key |
| Betfair | ❌ немає коллектора |
| Agent feedback loop | ❌ немає |
| Claude API | ❌ не підключений |
| Resolved сигнали | 68 total (36 DIVERGENCE) — мало |

---

## 4. Блок A: Agent Evolution

### A.1 Self-Learning Context (рівень 1, дешево)

Перед кожним рішенням агент отримує зведену статистику своїх минулих результатів:

```python
# Будується з stage7_agent_decisions + signal_history.resolved_success
agent_memory = {
    "my_past_decisions": {
        "DIVERGENCE": {
            "KEEP_total": 40, "KEEP_win_rate": 0.71,
            "KEEP_when_divergence_gt_060": {"total": 19, "win_rate": 1.00},
            "KEEP_when_divergence_020_040": {"total": 8, "win_rate": 0.00},
        },
        "ARBITRAGE_CANDIDATE": {
            "KEEP_total": 12, "KEEP_win_rate": 0.08,  # погано
        },
        "RULES_RISK": {
            "KEEP_total": 3, "KEEP_win_rate": 1.00,  # але мало даних
        }
    },
    "calibration": {
        "overall_brier_score": 0.21,
        "overconfident_on": ["ARBITRAGE_CANDIDATE"],
    }
}
```

Це дає агенту пам'ять без перенавчання моделі. Реалізація:
- Нова функція `build_agent_memory_context(db) -> dict` у `tools.py`.
- Вставляється в system prompt перед кожним рішенням.
- Оновлюється раз на добу (кешується в Redis).

**Очікуваний ефект**: агент перестане давати KEEP на ARBITRAGE сигналах (win rate 12.9%).

### A.2 Claude API Integration

Провайдер: `claude-haiku-4-5` для screening, `claude-sonnet-4-6` для deep analysis.

```python
# app/services/agent_stage7/stack_adapters/claude_adapter.py
class ClaudeAdapter:
    name = "claude"
    def __init__(self, model="claude-haiku-4-5-20251001", ...): ...
    def decide(self, payload: Stage7AdapterInput) -> dict: ...
```

Інтеграція через Anthropic SDK (`anthropic` пакет).
Пріоритет у FallbackAdapter: Groq → Gemini → **Claude Haiku** → PlainApi.

Вартість оцінка:
- Claude Haiku: ~$0.001 за 1000 токенів → $0.002/сигнал → $6/місяць на 3000 сигналів.
- Claude Sonnet: ~$0.015/сигнал → тільки для топ-20 сигналів/тиждень.

`.env` додати:
```
ANTHROPIC_API_KEY=sk-ant-...
STAGE7_CLAUDE_MODEL=claude-haiku-4-5-20251001
STAGE7_CLAUDE_ENABLED=true
```

### A.3 Agentic Tools для топ-сигналів

Для сигналів з `divergence > 0.40` і `kelly > 0.02` агент отримує доступ до інструментів:

```
Tool 1: web_search(query) → останні новини по темі ринку
Tool 2: fetch_resolution_criteria(market_url) → читає повний текст resolution rules
Tool 3: get_similar_past_markets(title) → повертає схожі resolved ринки з outcomes
```

Архітектура:
- `ClaudeAgentAdapter` (окремий від `ClaudeAdapter`) — використовує `tool_use`.
- Максимум 3 tool calls на сигнал (cost control).
- Timeout 30 секунд.
- Тільки для `cost_mode=normal` і `divergence > 0.40`.

Приклад reasoning:
```
Ринок: "Чи виграє X вибори 2026?"
Kalshi: 72%, Polymarket: 45% → divergence = 0.27

Claude шукає: "X election 2026 latest news"
Знаходить: нещодавнє скандал → переосмислює → MODIFY -15%
Або: нічого нового → KEEP (ціна просто відстала)
```

### A.4 MODIFY Рішення з кількісним коригуванням

Поточна проблема: агент видає KEEP або SKIP, ніколи MODIFY.

Новий промпт примушує давати числове коригування:
```json
{
  "decision": "MODIFY",
  "confidence_adjustment": -0.12,
  "reason_codes": ["weak_kelly", "single_platform_source"],
  "sizing_recommendation": "reduce_to_half_kelly"
}
```

Пороги для MODIFY (додати в system prompt):
- `kelly 0.005–0.015` → MODIFY -5% to -10%
- `consensus_spread 0.10–0.20` → MODIFY -8%
- `n_samples < 5` → MODIFY -15% (мало даних)
- `days_to_resolution < 3` → MODIFY -20% (занадто близько)

---

## 5. Блок B: Source Expansion

### B.1 Kalshi — ЗАБЛОКОВАНО ❌

**Статус**: повністю виключено з roadmap.

**Причини:**
1. Trading: вимагає US documents (SSN/ITIN) — недоступно для UA резидентів.
2. Data API: CloudFront geo-block для всіх non-US IP адрес (`403 - configured to block access from your country`).
3. Навіть US VPS для читання даних — надлишкова складність без можливості торгувати.

**Альтернатива**: IBKR ForecastEx (B.3) покриває ті самі фінансові event contracts.

### B.2 Betfair — ВИКЛЮЧЕНО ❌

**Статус**: виключено — платформа орієнтована на спорт/скачки, не фінансові події.

---

### B.3 IBKR ForecastEx — Data + Execution (замість Kalshi)

**Що таке IBKR ForecastEx**: event contracts на Interactive Brokers. Аналог Kalshi але доступний міжнародним клієнтам.

**KYC**: приймає UA документи (паспорт). Реєстрація 1-2 тижні.
**Ринки**: Fed rate decisions, CPI, NFP, GDP, S&P 500 level — фінансові macro events.
**Валюта**: USD.
**API**: TWS API (через IBKR Trader Workstation або Gateway).

Нові файли:
```
app/services/collectors/ibkr_forecastex.py  — читання event contracts
app/services/execution/ibkr.py              — розміщення ордерів (Stage 11+)
```

Schema маппінг:
```python
# IBKR ForecastEx → NormalizedMarketDTO
contract.conId → external_market_id
contract.lastPrice → probability_yes
contract.description → title
contract.expiry → resolution_time
```

Пріоритет категорій (cross-platform з Polymarket):
1. Fed rate → Polymarket "Will Fed raise rates?"
2. CPI → Polymarket "Will CPI be above X?"
3. NFP → Polymarket economic markets

`.env` додати:
```
IBKR_ENABLED=true
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
```

**Кроки для підключення:**
1. Зареєструватись на interactivebrokers.com (UA паспорт).
2. Встановити IBKR Gateway на сервері.
3. Написати collector через ib_insync або ibapi.

### B.4 Polymarket CLOB — Повна Активація

**Два API Polymarket:**

| | Gamma API | CLOB API |
|--|-----------|----------|
| URL | `gamma-api.polymarket.com` | `clob.polymarket.com` |
| Дані | probability, metadata | bid/ask, order book, fills |
| Авторизація | не потрібна | API key + L1 signature |
| Поточний стан | ✅ активний | ⚠️ код є, вимкнений |
| Для чого | збір даних | виконання угод |

Активація CLOB:
```
POLYMARKET_CLOB_ENABLED=true
POLYMARKET_CLOB_API_KEY=...   # з polymarket.com account
POLYMARKET_CLOB_API_BASE_URL=https://clob.polymarket.com
```

Що дає CLOB:
- Точний spread (`best_bid` vs `best_ask`).
- Реалістична ціна входу (не `probability` яка може бути stale).
- Можливість виставляти limit orders.
- Execution source = `clob_api` замість `gamma_api`.

Збільшення ліміту маркетів:
```python
# Поточний: limit=100
# Новий: limit=500, з пагінацією
params = {"limit": 100, "offset": 0, "active": True}
# Повторювати поки є ринки
```

---

## 6. Блок C: Profit Validation Gate

### C.1 Мінімальні умови для переходу до real trading

| Метрика | Мінімум | Цільове |
|---------|---------|---------|
| Resolved DIVERGENCE signals | 200 | 500 |
| Win rate (divergence > 0.40) | > 55% | > 65% |
| Win rate (divergence > 0.60) | > 75% | > 90% |
| Brier score | < 0.25 | < 0.20 |
| Kelly fraction середній | > 0.02 | > 0.05 |
| Agent MODIFY/REMOVE accuracy | > 50% | > 65% |

### C.2 Validation Dashboard (новий API endpoint)

```
GET /analytics/research/profit_validation
```

Відповідь:
```json
{
  "status": "SHADOW_NOT_READY",
  "resolved_total": 68,
  "resolved_needed": 200,
  "divergence_buckets": {
    "0.10-0.20": {"n": 1, "win_rate": 1.00, "kelly": 0.02},
    "0.20-0.40": {"n": 8, "win_rate": 0.00, "kelly": -0.05},
    "0.40-0.60": {"n": 8, "win_rate": 0.13, "kelly": 0.01},
    "0.60+":     {"n": 19, "win_rate": 1.00, "kelly": 0.18}
  },
  "recommendation": "Тільки divergence > 0.60 показує стабільний edge. Потрібно більше даних.",
  "estimated_days_to_ready": 14
}
```

### C.3 Telegram Notification при досягненні gate

Коли `resolved_total >= 200` і `win_rate(0.60+) >= 0.75`:
```
🎯 Stage 13 Acceptance Gate досягнуто!
Win rate (div>0.60): 87% (n=45)
Kelly: 0.16
Рекомендація: перехід до LIMITED_GO
```

---

## 7. Пріоритет виконання

### Фаза 1 (тиждень 1) — Швидкі wins ✅ ВИКОНАНО
1. ~~Kalshi~~ — заблоковано, виключено.
2. ✅ Polymarket: збільшено до 500 активних ринків з пагінацією.
3. ✅ Polymarket CLOB: активовано `/book?token_id=` без авторизації, реальний bid/ask.
4. Agent memory context: додати статистику минулих рішень в промпт.

### Фаза 2 (тиждень 2-3) — Claude API
1. Зареєструватись на console.anthropic.com, отримати API key.
2. Написати `ClaudeAdapter`.
3. Додати Claude Haiku в FallbackAdapter chain.
4. MODIFY рішення з числовим коригуванням.

### Фаза 3 (тиждень 3-4) — Betfair
1. Зареєструватись на Betfair (UA паспорт).
2. Написати `BetfairCollector`.
3. Додати cross-platform scoring Betfair ↔ Polymarket.

### Фаза 4 (місяць 2) — Agentic Tools
1. Web search tool для Claude.
2. News fetch tool.
3. Similar markets lookup.
4. Profit validation gate dashboard.

---

## 8. Технічні деталі

### Нові файли
```
app/services/agent_stage7/stack_adapters/claude_adapter.py
app/services/agent_stage7/stack_adapters/claude_agent_adapter.py  (з tool use)
app/services/agent_stage7/tools/web_search.py
app/services/agent_stage7/tools/news_fetch.py
app/services/agent_stage7/memory.py  (agent_memory_context builder)
app/services/collectors/betfair.py
app/services/research/profit_validation.py
app/api/routes/profit_validation.py
```

### Нові env змінні
```
ANTHROPIC_API_KEY=sk-ant-...
STAGE7_CLAUDE_MODEL=claude-haiku-4-5-20251001
STAGE7_CLAUDE_ENABLED=true
STAGE7_AGENTIC_TOOLS_ENABLED=false   # увімкнути в фазі 4
STAGE7_AGENTIC_MIN_DIVERGENCE=0.40   # мінімум для tool use
KALSHI_ENABLED=true
BETFAIR_ENABLED=false                # після реєстрації
BETFAIR_APP_KEY=
BETFAIR_USERNAME=
BETFAIR_PASSWORD=
POLYMARKET_CLOB_ENABLED=true
```

### Нові таблиці БД
```sql
-- Пам'ять агента (кеш статистики)
CREATE TABLE agent_memory_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    memory_json JSON NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Profit validation snapshots
CREATE TABLE profit_validation_snapshots (
    id SERIAL PRIMARY KEY,
    status VARCHAR(32),
    metrics_json JSON NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 9. Acceptance Criteria

1. Kalshi підключений як data source → кількість DIVERGENCE сигналів зросла ≥ 2x.
2. Claude API працює в FallbackAdapter → ≥ 80% сигналів отримують реальне LLM рішення.
3. MODIFY рішення: агент видає MODIFY для ≥ 10% сигналів (vs 0% зараз).
4. Agent memory: ARBITRAGE win rate в LLM рішеннях ≤ 20% (агент навчився уникати).
5. Profit validation gate: dashboard показує реальний стан з рекомендацією.
6. Betfair: ≥ 50 cross-platform пар Betfair↔Polymarket на тиждень.
7. Усі 143 тести проходять.

---

## 10. Ризики

| Ризик | Ймовірність | Мітигація |
|-------|-------------|-----------|
| ~~Kalshi~~ | — | виключено повністю |
| IBKR реєстрація займає довго | середня | почати реєстрацію паралельно |
| Claude API коштує більше очікуваного | середня | жорсткий budget cap, Haiku замість Sonnet |
| Agentic tools повільні (>30s) | висока | timeout + fallback до simple decision |
| 200+ resolved не підтверджують edge | середня | pivot: фокус на RULES_RISK або нові стратегії |
