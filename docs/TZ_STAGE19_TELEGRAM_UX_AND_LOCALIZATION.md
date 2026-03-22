# ТЗ Stage19: Telegram UX, Локалізація та Надійність Доставки

**Статус:** Draft
**Дата:** 2026-03-22
**Файли:** `app/bot/bot_app.py`, `app/tasks/jobs.py`, `app/services/telegram_product.py`, нові модулі

---

## 1. Мета

Зробити Telegram-інтерфейс продуктово читабельним, консистентним українською мовою, без спаму, дублювання та втрат повідомлень.

---

## 2. Аудит — підтверджені проблеми

### Критичні (P0)

| # | Проблема | Місце | Деталі |
|---|---|---|---|
| C1 | Stage17 без `parse_mode` | `jobs.py:91` | OPEN/WIN/DAILY йдуть plain-text, `*bold*` не рендериться |
| C2 | Stage17 шле лише в `telegram_chat_id` | `jobs.py:80` | `telegram_admin_ids` і підписники не отримують сповіщень |
| C3 | Нема retry/backoff на 429/5xx | `jobs.py:89`, `jobs.py:411` | Втрати повідомлень при rate-limit |
| C4 | Silent failure без логування | `jobs.py:95` | `Exception` ковтається, причина не фіксується |
| C5 | Технічні поля у user-facing тексті | `jobs.py:402–408` | `ARBITRAGE_CANDIDATE`, `Execution assumptions: v2_empirical_labeled_returns`, raw utility float |
| C6 | WIN без форматування чисел | `jobs.py:121` | `profit_usd` без `:.2f`, може бути `None` або `612.4999999` |
| C7 | Stage17 DAILY — сирі float/enum | `jobs.py` | `hit_rate=0.090909`, `roi=0.027853`, `final=NO_GO_DATA_PENDING` |

### Середні (P1)

| # | Проблема | Місце | Деталі |
|---|---|---|---|
| M1 | Змішана мова в одному повідомленні | `bot_app.py:103`, `telegram_product.py:357`, `jobs.py:112` | EN/UA/мікс без логіки |
| M2 | Два різні формати одного сигналу | `bot_app.py:171` vs `jobs.py:401` | `/signals` і auto-push виглядають по-різному |
| M3 | Непослідовна квота сигналів | `bot_app.py:152`, `bot_app.py:191` | `/signals` і `/top` списують ліміт так само як auto-push |
| M4 | Небезпечний URL у MarkdownV2 | `bot_app.py:170`, `bot_app.py:210`, `bot_app.py:235` | URL зі спецсимволами ламає рендер |
| M5 | UX-конфлікт у підказці dryrun | `telegram_product.py:271` | Текст каже `/dryrun run`, команда фактично `/dryrun` |
| M6 | Широка таблиця позицій | `telegram_product.py:362` | Monospace-таблиця нечитабельна на 320px (мобільний) |
| M7 | `/start` без форматування | `bot_app.py:120` | `await message.answer(text)` без `parse_mode` |
| M8 | `/help` — один рядок команд | `bot_app.py:127` | Нуль структури і описів |
| M9 | `/me` — мінімум даних | `bot_app.py:300` | Немає квоти, watchlist count, статистики |
| M10 | `/plans` — заглушка | `bot_app.py:135` | `"FREE, PRO, PREMIUM (payments TBD)"` |
| M11 | signal_push без фільтра підписки | `jobs.py:343` | Шле всім `User`, включно з `INACTIVE` |

### Нефункціональні (P2)

| # | Проблема | Деталі |
|---|---|---|
| N1 | Три дублікати `_escape_markdown_v2` | `bot_app.py:29`, `telegram_product.py:260`, `jobs.py:71` — три реалізації одного хелпера |
| N2 | URL не санітизується в посиланнях | `f"[Open Market]({market.url})"` без escape |
| N3 | Немає метрик доставки | Не відомо скільки 429/403/timeout трапляється |
| N4 | N+1 у signal_push при кількох юзерах | Pool завантажується один раз — ок, але per-user top-rank query може бути важкою |

---

## 3. Scope

```
app/
  bot/
    bot_app.py                    — редизайн команд
  tasks/
    jobs.py                       — signal_push + stage17 alerts
  services/
    telegram_product.py           — форматування portfolio/positions
    telegram_i18n.py              — NEW: словник UA-текстів
    telegram_templates.py         — NEW: єдині шаблони повідомлень
    telegram_delivery.py          — NEW: retry/backoff/routing/dedup
```

---

## 4. Функціональні вимоги

### 4.1 Єдина мова — українська

- 100% user-facing текстів українською
- Заборонено мікс UA/EN в одному повідомленні
- Службові enum (`ARBITRAGE_CANDIDATE`, `NO_GO_DATA_PENDING`) не показувати — лише людські назви
- Таблиці: або всі заголовки UA, або всі EN (без мікс)

**Маппінг enum → UA:**

| Enum | UA назва |
|---|---|
| `ARBITRAGE_CANDIDATE` | Арбітраж |
| `TAIL_EVENT_CANDIDATE` | Хвостова подія |
| `RULES_RISK` | Ризик правил |
| `DIVERGENCE` | Розбіжність |
| `EXECUTE_ALLOWED` | Дозволено |
| `SHADOW_ONLY` | Тільки спостереження |
| `BLOCK` | Заблоковано |
| `NO_GO_DATA_PENDING` | Недостатньо даних |
| `NO_GO` | Не рекомендовано |
| `LIMITED_GO` | Обмежено дозволено |

### 4.2 Єдиний формат сигналу

Одна `render_signal_message()` для: auto-push, `/signals`, `/top`.

**Шаблон:**
```
{emoji} *{тип_UA}*
{заголовок}
📊 Впевненість: {confidence:.0%} · {платформа}
📈 Edge після витрат: {edge:+.1%}
⏳ До закриття: {days} дн
{лінк}
```

Прибрати з усіх трьох місць: `Execution assumptions`, `Utility (exec)`, `cost impact`, `Disclaimer` (окремо від сигналу).

### 4.3 Stage17 повідомлення з MarkdownV2

**OPEN:**
```
🔥 *{emoji_категорії} Нова позиція відкрита*

📌 {заголовок}
🏷 {категорія_UA} · {платформа} · {дні} дн

💡 Наша оцінка: *{наша_%:.1f}%* vs ринок *{ринок_%:.1f}%*
📐 Коеф: *x{koef:.2f}* · Ставка: *${bet:.2f}*
```

**WIN:**
```
🎉 *Виграш\\!*

📌 {заголовок}
💰 Прибуток: *\\+${profit:.2f}* \\({roi:+.1f}%\\)
📐 Коеф: x{koef:.2f}
```

**DAILY:**
```
📊 *Stage17 — Щоденний звіт*

✅ Відкриті позиції: *{open_positions}*
🎯 Влучність: *{hit_rate:.1f}%* \\({wins}/{total}\\)
💹 ROI: *{roi:+.1f}%*
📐 Середній коеф: *x{avg_koef:.2f}*

⚖️ Вердикт: {вердикт_UA}
```

Усі числа: відсотки через `:.1f`, суми через `:.2f`. Заборонено raw float типу `0.090909`.

### 4.4 Канали доставки Stage17

Конфіг через env (`STAGE17_DELIVERY_MODE`):

| Режим | Отримувачі |
|---|---|
| `channel_only` (default) | `telegram_chat_id` |
| `admins` | `telegram_chat_id` + `telegram_admin_ids` |
| `subscribers` | всі `User` з активною підпискою |

### 4.5 Антиспам і дедуплікація

- Один сигнал — не більше одного разу на юзера в межах 24h вікна
- Глобальний throttle: max 10 push на юзера за 30 хв
- `signal_push` фільтрує `User.subscription_status != INACTIVE`

### 4.6 Retry з backoff

```python
# telegram_delivery.py
for attempt in range(3):
    resp = send(...)
    if resp.status_code == 200:
        break
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        sleep(retry_after)
    elif resp.status_code >= 500:
        sleep(2 ** attempt)
    else:
        log_error(resp)
        break
```

### 4.7 Команди — редизайн

**`/start`** — з `parse_mode="MarkdownV2"`, вітальний текст UA, структура секцій.

**`/help`:**
```
📖 *Доступні команди*

📡 *Сигнали*
/top — топ сигнали прямо зараз
/signals — останні сигнали
/digest — зведення за сьогодні

👁 *Слідкування*
/watchlist — мій список
/add <id> — додати ринок
/remove <id> — видалити

👤 *Профіль*
/me — мій профіль і ліміти
/plans — тарифи
```

**`/me`:**
```
👤 *Мій профіль*

🏷 @{username}
📦 Тариф: {план_UA}
📊 Статус: {статус_UA}

📡 Сигнали сьогодні: {sent}/{limit}
👁 Слідкую: {watchlist_count} ринків
```

**`/plans`:**
```
💎 *Тарифи*

🆓 *Безкоштовний*
• 5 сигналів на день
• Watchlist: 3 ринки

⭐ *PRO* — незабаром
• Без ліміту сигналів
• Пріоритетні сповіщення
```

### 4.8 Mobile-first позиції

Прибрати широку псевдотаблицю. Замість неї — картки:

```
📂 *Відкриті позиції — {n}*
Вкладено: ${total:.2f} · P&L: {pnl:+.2f}$

*1\. {заголовок[:50]}*
↗ YES · x{koef:.1f} · ${notional:.2f}
📅 До закриття: {days} дн · P&L: {pnl:+.1f}%

*2\. {заголовок[:50]}*
...
```

### 4.9 Безпечний Markdown і лінки

Єдиний хелпер в `telegram_i18n.py`:
```python
def escape_mv2(text: str) -> str: ...
def safe_url(url: str) -> str:  # escape тільки ')' і '\\'
    ...
def link(text: str, url: str) -> str:
    return f"[{escape_mv2(text)}]({safe_url(url)})"
```

Заборонити сирі `f"[text]({url})"` без `safe_url()`.

---

## 5. Нефункціональні вимоги

### 5.1 Observability доставки

Логувати кожну невдалу відправку:
```python
logger.warning("telegram_send_failed", extra={
    "chat_id": chat_id,
    "status_code": resp.status_code,
    "reason": classify_error(resp),  # "rate_limit" | "blocked" | "timeout" | "parse_error"
    "attempt": attempt,
})
```

Метрики (через `job_runs.details` або окремий лог):
- `telegram_sent_total`
- `telegram_failed_total`
- `telegram_retry_total`
- `telegram_dedup_skipped_total`

### 5.2 Продуктивність

- Без N+1 при формуванні push-пакету (pool завантажується один раз)
- p95 підготовки push-пакету < 1 сек (без мережі Telegram)

---

## 6. Структура нових файлів

### `app/services/telegram_i18n.py`

```python
SIGNAL_TYPE_NAMES = {
    "ARBITRAGE_CANDIDATE": "Арбітраж",
    "TAIL_EVENT_CANDIDATE": "Хвостова подія",
    ...
}

VERDICT_NAMES = {
    "NO_GO_DATA_PENDING": "Недостатньо даних",
    "NO_GO": "Не рекомендовано",
    ...
}

CATEGORY_NAMES = {
    "geopolitical_event": "Геополітика",
    "election": "Вибори",
    ...
}

def escape_mv2(text: str) -> str: ...
def safe_url(url: str) -> str: ...
def link(label: str, url: str) -> str: ...
```

### `app/services/telegram_templates.py`

```python
def render_signal_message(signal, market, *, settings) -> str: ...
def render_stage17_open(item: dict) -> str: ...
def render_stage17_win(item: dict) -> str: ...
def render_stage17_daily(summary: dict) -> str: ...
def render_help(is_admin: bool) -> str: ...
def render_profile(user, sent_today: int, limit: int, watchlist_count: int) -> str: ...
def render_plans() -> str: ...
```

### `app/services/telegram_delivery.py`

```python
def send_message(token: str, chat_id: str, text: str, *, parse_mode="MarkdownV2") -> bool: ...
    # retry з backoff, логування

def resolve_recipients(settings, *, mode: str, db) -> list[str]: ...
    # channel_only | admins | subscribers

def is_deduped(db, user_id: int, signal_id: int, *, window_hours: int = 24) -> bool: ...
```

---

## 7. Acceptance Criteria

- [ ] 100% user-facing повідомлень Telegram українською
- [ ] Відсутні raw enum (`ARBITRAGE_CANDIDATE`, `NO_GO_DATA_PENDING`) у повідомленнях
- [ ] Stage17 OPEN/WIN/DAILY рендеряться з `parse_mode="MarkdownV2"`
- [ ] WIN/DAILY не містять raw float (`0.090909`), тільки форматовані значення
- [ ] Єдиний шаблон `render_signal_message()` використовується в auto-push, `/signals`, `/top`
- [ ] Для 429/5xx є мінімум 2 повторні спроби з backoff
- [ ] `signal_push` фільтрує `INACTIVE` юзерів
- [ ] `/help` має структурований список з описами
- [ ] `/me` показує квоту (sent/limit) і watchlist count
- [ ] `/positions` читабельний на 320px без горизонтального скролу
- [ ] Єдиний `escape_mv2()` / `safe_url()` — без дублікатів у трьох файлах
- [ ] Невдалі відправки логуються з причиною (`rate_limit`, `blocked`, `parse_error`)

---

## 8. Етапи реалізації

### P0 — Критичний UX/надійність (1–2 дні)

1. `telegram_delivery.py` — retry/backoff + логування
2. `telegram_templates.py` — `render_stage17_open/win/daily()` з MarkdownV2
3. Підключити `parse_mode` у `_send_stage17_telegram_messages`
4. Виправити WIN: `:.2f` для `profit_usd`, fallback для `None`
5. DAILY: відсотки замість raw float, `VERDICT_NAMES` замість enum

### P1 — Продуктовість (2–4 дні)

6. `telegram_i18n.py` — словник + хелпери
7. `render_signal_message()` — єдиний шаблон, підключити до push/signals/top
8. `/help`, `/me`, `/plans`, `/start` — редизайн UA
9. `/positions` — картки замість таблиці
10. `signal_push` — фільтр `INACTIVE` + dedup вікно

### P2 — Масштабування (1 тиждень)

11. `resolve_recipients()` + `STAGE17_DELIVERY_MODE`
12. Throttling per-user (max N push/30 хв)
13. Метрики доставки в `job_runs.details`

---

## 9. Залежності

- Не чіпати Stage7/Stage8/Stage17 бізнес-логіку
- Не змінювати схему БД
- Зворотна сумісність: якщо нові модулі не підключені — стара поведінка зберігається
