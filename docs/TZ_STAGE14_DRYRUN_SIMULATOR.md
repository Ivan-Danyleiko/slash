# ТЗ Stage 14 — Dry-Run Paper Trading Simulator

**Дата:** 2026-03-17
**Статус:** ВИКОНАННЯ

---

## 1. Мета

Побудувати симулятор паперової торгівлі (dry-run) з віртуальним рахунком $100, який:
- автоматично відкриває позиції на основі сигналів + рішень AI-агента (Stage 7)
- розраховує розмір позиції за критерієм Келлі (3–5% від балансу)
- відстежує нереалізований та реалізований P&L
- виводить звіт: win rate, ROI, коефіцієнт прибутковості, AI-висновки

**Dry-run ≠ Stage11** (Stage11 — реальні ордери). Dry-run — повністю ізольований симулятор без відправлення ордерів.

---

## 2. Що вже є (аналіз системи)

### 2.1 Stage11 (реальна торгівля)
- `Stage11Client`, `Stage11Order`, `Stage11Fill`, `Stage11ClientPosition` — повний цикл реальних ордерів
- `Stage11RiskEngine` — circuit breakers (SOFT/HARD/PANIC)
- `Stage11OrderManager`, `Stage11ExecutionRouter` — відправка ордерів через CLOB API
- **Проблема:** Stage11 потребує підпису гаманцем (MetaMask/Polygon). Dry-run не потребує.

### 2.2 Stage7 Shadow Agent
- Рішення: KEEP / MODIFY / REMOVE / SKIP
- Поля в `Stage7AgentDecision`: `kelly_fraction`, `expected_ev_pct`, `market_prob`, `divergence_score`
- **Проблема:** агент не каже напрямок (BUY YES чи BUY NO). Напрямок береться з `Signal.signal_direction`.

### 2.3 Signals
- `Signal.signal_direction` — YES / NO (є в моделі)
- `Signal.metadata_json` — може містити `ask_price`, `bid_price`
- `Market.best_ask_yes`, `Market.best_bid_yes` — реальні ціни CLOB
- **Проблема:** ринки не мають `resolution_value` — не знаємо результат ринку в БД.

### 2.4 Polymarket CLOB
- Публічний API `/book?token_id=` — bid/ask без ключа
- Інтегровано в `PolymarketCollector` через `source_payload`
- **Проблема:** token_id зберігається у `source_payload` ринку, потрібно витягувати для mark-to-market.

### 2.5 Дані без резолюції
- `SignalHistory.resolved_success` — є поле, але заповнення залежить від labeler
- `Market.status` — може стати "resolved" через Gamma API
- **Проблема:** Gamma API іноді не одразу оновлює статус; потрібно polling + fallback через `resolution_time`.

---

## 3. Проблемні місця (Risk Analysis)

| # | Проблема | Вплив | Рішення |
|---|----------|-------|---------|
| P1 | Немає `resolution_value` в DB | Не можемо закрити позицію з реальним P&L | Polling Gamma API по market status="resolved" + резолюція з `resolutionValue` |
| P2 | AI не вказує напрямок | Не знаємо BUY YES чи NO | Використовувати `Signal.signal_direction` (вже є) |
| P3 | Kelly fraction може бути нульовим | Позиція 0% — нічого не відкривається | Fallback: якщо kelly=0 → мінімум 3% якщо EV>0 |
| P4 | CLOB ціни не оновлюються між sync циклами | Unrealized P&L застарілий | Celery task кожні 30 хв оновлює mark price відкритих позицій |
| P5 | Stage11 і dry-run можуть конфліктувати | Плутанина в звітах | Повністю окремі таблиці `dryrun_portfolio` / `dryrun_positions` |
| P6 | $100 не вистачить для 3-5% якщо EV малий | Позиції < $3 — нереалістично | Мінімальний ринковий розмір $1 (Polymarket приймає) |
| P7 | Немає стоп-лосу | Позиція може висіти вічно | Auto-close: якщо mark_price < 50% від entry або resolution_time минув |

---

## 4. Архітектура

```
Signals DB → DryrunSimulator → DryrunPortfolio ($100 virtual)
                ↓                     ↓
         Stage7Decision         DryrunPosition[]
         kelly_fraction              ↓
         signal_direction     mark-to-market (CLOB)
                                     ↓
                              DryrunReporter
                                     ↓
                        /api/v1/admin/dryrun/report
```

### 4.1 Моделі DB

#### `DryrunPortfolio`
```
id, name, initial_balance_usd, current_cash_usd,
total_realized_pnl_usd, total_unrealized_pnl_usd,
created_at, updated_at
```

#### `DryrunPosition`
```
id, portfolio_id, signal_id, market_id, platform,
direction (YES/NO), entry_price, mark_price,
notional_usd, shares_count,
status (OPEN/CLOSED/EXPIRED),
open_reason (kelly, ev, ai_decision),
close_reason (resolved_yes, resolved_no, stop_loss, expired),
entry_kelly_fraction, entry_ev_pct,
realized_pnl_usd, unrealized_pnl_usd,
opened_at, closed_at, resolution_deadline
```

### 4.2 Position Sizing Logic

```python
kelly = stage7_decision.kelly_fraction or 0.0
ev = stage7_decision.expected_ev_pct or 0.0

# Clamp Kelly to [3%, 5%] якщо EV > 0
if ev > 0 and kelly > 0:
    position_pct = max(0.03, min(0.05, kelly))
elif ev > 0.02:
    position_pct = 0.03  # мінімум при слабкому Kelly
else:
    return  # не відкривати

notional = portfolio.current_cash_usd * position_pct
entry_price = market.best_ask_yes  # якщо direction=YES
shares = notional / entry_price
```

### 4.3 Entry умови

Відкриваємо позицію якщо:
1. `Stage7AgentDecision.llm_decision == "KEEP"`
2. `Signal.signal_direction in ("YES", "NO")`
3. `Signal.signal_type == "ARBITRAGE_CANDIDATE"`
4. `Market.best_ask_yes is not None` (є CLOB ціна)
5. `portfolio.current_cash_usd >= notional`
6. Немає відкритої позиції по цьому ринку

### 4.4 Exit умови

- **Резолюція:** `Market.status == "resolved"` → P&L = (win=1.0 vs ask) або (loss=0 vs ask)
- **Stop-loss:** mark_price < entry_price * 0.5
- **Expiry:** `Market.resolution_time` пройшов і ринок не resolved → закрити за поточною ціною
- **AI REMOVE:** якщо нова Stage7 decision = REMOVE → закрити за mark_price

### 4.5 Mark-to-Market

Celery task кожні 30 хв:
1. Знайти всі OPEN позиції
2. Для кожної: fetch `market.source_payload["yes_token_id"]`
3. Запит `/book?token_id=` → best_bid_yes (ціна якщо продавати)
4. Оновити `mark_price`, перерахувати `unrealized_pnl_usd`

---

## 5. API Endpoints

| Метод | URL | Опис |
|-------|-----|------|
| POST | `/api/v1/admin/dryrun/run` | Запустити цикл симуляції (відкрити нові позиції) |
| GET | `/api/v1/admin/dryrun/report` | Повний звіт: портфель + позиції + статистика |
| POST | `/api/v1/admin/dryrun/refresh-prices` | Оновити mark prices (CLOB) |
| POST | `/api/v1/admin/dryrun/reset` | Скинути портфель до $100 |

### 5.1 Структура звіту

```json
{
  "portfolio": {
    "cash_usd": 87.20,
    "open_positions_usd": 11.50,
    "total_value_usd": 98.70,
    "realized_pnl_usd": -1.30,
    "unrealized_pnl_usd": -0.80,
    "roi_pct": -1.30
  },
  "stats": {
    "total_positions": 5,
    "open": 3,
    "closed": 2,
    "win_rate": 0.50,
    "avg_win_usd": 2.10,
    "avg_loss_usd": 3.70,
    "kelly_expectation": 0.024,
    "profit_probability_pct": 45.0
  },
  "positions": [...],
  "ai_summary": "Shadow mode. 5 trades, 2 closed. Current ROI: -1.3%. Kelly expectation suggests breakeven needs 3 more resolved markets."
}
```

---

## 6. Celery Tasks

| Task | Розклад | Що робить |
|------|---------|-----------|
| `dryrun_run_cycle` | кожні 30 хв | Сканує нові KEEP-сигнали, відкриває позиції |
| `dryrun_refresh_prices` | кожні 30 хв | Оновлює mark prices через CLOB |
| `dryrun_check_resolutions` | кожні 60 хв | Перевіряє resolved ринки, закриває позиції |

---

## 7. Фази виконання

### Phase 1 — Моделі + Симулятор ✅ ВИКОНАНО
- [x] Додати `DryrunPortfolio`, `DryrunPosition` в `models.py`
- [x] Створити `app/services/dryrun/simulator.py`
- [x] Створити `app/services/dryrun/reporter.py`
- [x] Додати endpoints в `admin.py`
- [x] Alembic міграція `0017_dryrun_simulator`

### Phase 2 — Mark-to-Market ✅ ВИКОНАНО
- [x] Celery task `dryrun_refresh_prices`: refresh CLOB prices для open positions
- [x] Stop-loss автозакриття (mark_price < entry * 0.5)
- [x] Time-exit: закриття при low EV після TIME_EXIT_DAYS=14
- [x] Take-profit: закриття при 65% захопленого максимального прибутку

### Phase 3 — Resolution Tracking ✅ ВИКОНАНО
- [x] Polling Gamma API для resolved ринків (`check_resolutions`)
- [x] Auto-close з реальним P&L
- [x] `stage11_reconcile` Celery task (кожні 10 хв)

### Phase 4 — Перша реальна позиція ✅ 2026-03-18
- [x] Відкрито першу позицію: **OKC Thunder NBA Western Conference Finals** (Polymarket)
  - Entry: $0.51 YES, spread 2.0%, notional $5, resolution 2026-06-16
  - kelly=0.389, ev=1.95%, daily_ev=0.022%

---

## 8. Спряження з існуючими модулями

| Модуль | Використання |
|--------|-------------|
| `Stage7AgentDecision` | kelly_fraction, llm_decision, expected_ev_pct |
| `Signal` | signal_direction, signal_type, market_id |
| `Market` | best_ask_yes, best_bid_yes, source_payload (token_id), resolution_time, status |
| `PolymarketCollector._fetch_clob_top()` | Повторне використання для mark-to-market |
| `Stage11RiskEngine` | Перевикористати логіку circuit breaker для dry-run |

---

**Висновок:** Phase 1 можна реалізувати прямо зараз. Resolution tracking — головне обмеження для реального backtest результату. Dry-run дасть realtime paper P&L але не retroactive результати.
