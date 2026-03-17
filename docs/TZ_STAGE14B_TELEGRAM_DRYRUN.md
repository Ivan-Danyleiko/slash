# ТЗ Stage 14B — Telegram Bot: Dry-Run Portfolio

**Дата:** 2026-03-17
**Статус:** ✅ ВИКОНАНО

---

## 1. Аналіз поточного бота

### Що є:
- `/start`, `/help`, `/plans` — онбординг
- `/signals`, `/top` — сигнали (з лімітом по плану)
- `/watchlist`, `/add`, `/remove` — список спостереження
- `/digest` — щоденний дайджест
- `/me` — профіль користувача

### Проблеми/обмеження:
- Немає жодного відображення dry-run портфеля
- Немає обмеження на admin-команди (будь-хто може /top)
- Команди `/portfolio`, `/positions`, `/pnl` — відсутні
- Дайджест не включає дані dry-run

---

## 2. Нові команди

| Команда | Доступ | Опис |
|---------|--------|------|
| `/portfolio` | admin | Загальний баланс, P&L, відкриті позиції |
| `/positions` | admin | Список відкритих позицій з деталями |
| `/pnl` | admin | Статистика: win rate, ROI, Kelly, прогноз |

**Admin** = тільки `TELEGRAM_CHAT_ID` з `.env` (власник бота).

---

## 3. Дизайн повідомлень

### `/portfolio` — огляд портфеля
```
💼 Dry-Run Portfolio
━━━━━━━━━━━━━━━━
💵 Cash:      $87.20
📦 In trades:  $11.50  (3 open)
🏦 Total:      $98.70

📈 Realized:   -$1.30
📉 Unrealized: -$0.80
📊 ROI:        -1.30%

🎯 Win rate:  50%  (1/2 closed)
⚡ Kelly E:   0.0240
🎲 Profit prob: 50.0%
```

### `/positions` — відкриті позиції
```
📂 Open Positions — 3

1️⃣ YES · Polymarket
Will Bitcoin hit $1M before GTA VI?
Entry $0.486 → Now $0.490  +$0.08 (+1.6%)
$3.00 · 6.17 shares · Kelly 3.0%
⏳ Deadline: 2027-01-01

2️⃣ YES · Polymarket
Will Republicans control Senate after 2026?
Entry $0.500 → Now $0.480  -$0.12 (-4.0%)
$3.00 · 6.00 shares · Kelly 3.0%
⏳ Deadline: 2026-11-10
```

### `/pnl` — P&L статистика
```
📊 P&L Report
━━━━━━━━━━━━━
Closed:   2  ·  Won: 1  ·  Lost: 1
Win rate: 50.0%
Avg win:  +$2.10
Avg loss: -$3.70

Kelly E(V):      0.0240
Profit prob:    50.0%
ROI:            -1.30%
Initial:        $100.00
Current value:  $98.70

Shadow mode · Not financial advice
```

---

## 4. Архітектура

- Нові функції в `TelegramProductService`: `get_dryrun_portfolio_text()`, `get_dryrun_positions_text()`, `get_dryrun_pnl_text()`
- Нові хендлери в `bot_app.py`: `/portfolio`, `/positions`, `/pnl`
- Admin guard: `_is_admin(message)` перевіряє `message.chat.id == settings.telegram_chat_id`
- Дані: `build_report()` з `app.services.dryrun.reporter`

---

## 5. Проблемні місця

| # | Проблема | Рішення |
|---|----------|---------|
| B1 | Нема позицій без реальних даних | Відображати "No positions yet" |
| B2 | Повідомлення може перевищити 4096 символів | Обрізати до 5 позицій + "...+N more" |
| B3 | Markdown в назвах ринків може зламати форматування | Екранувати спецсимволи |
| B4 | `TELEGRAM_CHAT_ID` — рядок або число | Кастувати до str для порівняння |
