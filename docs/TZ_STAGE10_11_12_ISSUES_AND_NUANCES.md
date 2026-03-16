# Аналіз ТЗ Stage 10 / 11 / 12 — Проблеми та нюанси

Дата: 2026-03-16

---

## Stage 10 — Event Replay Engine

### 1. Критичне: Historical probability timeline недоступна через Gamma/Manifold

ТЗ §4 каже "Polymarket historical (Gamma + CLOB where available)" і "Manifold historical",
але обидва API повертають **тільки поточну probability**, не часову серію.

- Gamma API: немає endpoint для "яка була ціна 2 тижні тому"
- Manifold: `probability` — поточне значення. Для часової серії потрібен `/bets` endpoint
  (реконструкція timeline через ставки) або `/v0/market/{id}/bets?before=<timestamp>`
- Metaculus: `community_prediction` у detail view — поточне. Для history потрібен
  `/questions/{id}/prediction-history/` — окремий endpoint, не згаданий у ТЗ

**Наслідок**: без time-series probability, `features_snapshot` у replay-row буде містити
ПОТОЧНУ probability (на момент збору даних), а не на `replay_timestamp`. Це lookahead
leakage навіть при правильно написаному leakage check.

**Що потрібно уточнити в ТЗ**: який саме endpoint і механізм дає нам probability(t)?

---

### 2. Критичне: Anti-lookahead — нема алгоритму детекції

ТЗ §6 каже "check `leakage_violations_count == 0`" але не визначає:
- Що саме є "порушенням lookahead" алгоритмічно?
- Як `leakage_violations_count` вираховується в коді?

Без конкретного алгоритму (наприклад: "поле `X` в features_snapshot не може мати
timestamp пізніший за `replay_timestamp - embargo_seconds`") цей check — формальність.

**Пропозиція**: у ТЗ потрібен окремий §6.1 з точним визначенням leakage rule
для кожного типу фічі (probability, volume, resolution_time, agent_decision).

---

### 3. Серйозне: 100 подій з чистими resolved outcomes — складніше ніж здається

- **Politics**: виборні цикли рідкісні, більшість поточних ринків відкриті
- **Sports**: Polymarket/Manifold мають спортивні ринки але вони короткострокові
  і часто закриваються без `resolutionProbability` (тільки YES/NO без probability value)
- **Дублікати**: один і той самий event на Polymarket + Manifold = 1 event чи 2?
  ТЗ не визначає деduplication rule для cross-platform events

Реалістичний розподіл що легко досягти: **crypto 40+ / finance 15-20 / politics 10-15 / sports 10-15**.
Для `core_categories_each >= 20` по sports і politics може знадобитись розширити
часовий горизонт збору далеко назад (1-2 роки), що ускладнює historical data access.

---

### 4. Серйозне: Stage 7 pipeline в replay режимі — LLM та labeled returns

ТЗ §2 каже "прогін Stage 7–9 policy/agent шару" але:

- **Stage 7 (LLM)**: при replay в `temperature=0` і `input_hash` кеш — якщо такого
  рядка в кеші ще немає, буде новий LLM-запит. 100 подій × Top-5 модулів = 500+ викликів
  LLM при першому прогоні (~$1-5 залежно від моделі). Потрібен cost cap у ТЗ.
- **ExecutionSimulatorV2**: використовує labeled returns з `signal_history` за lookback_days.
  В replay режимі: які returns використовувати? "Доступні на `replay_timestamp`" —
  але це означає потрібен time-indexed access до signal_history, якого зараз немає.
  Якщо брати всі поточні returns — це lookahead leakage в execution model.

---

### 5. `reason_code_stability` — метрика без визначення

ТЗ §8 включає `reason_code_stability` в перелік метрик але ніде в Stage 1-9 ця метрика
не визначена. Нема формули, нема порогу. Потрібно додати визначення
(наприклад: частка replay-runs де той самий input_hash дає ті самі reason_codes).

---

### 6. Sandbox для агентних модулів — рівень ізоляції не визначений

ТЗ §7 каже "sandbox, без приватних ключів" і "network egress check" але не визначає:
- Docker? Process-level? VM?
- Як саме блокується network egress (iptables, `--network=none`, seccomp)?
- "Top-5 модулів за активністю/репутацією" — якого типу модулі? LLM? Rule-based?
  Звідки береться shortlist? Немає переліку кандидатів.

---

### 7. Відсутній DB schema для replay artifacts

ТЗ визначає `data contract replay row` але не вказує:
- Зберігати в БД (нова таблиця `stage10_replay_rows`) чи тільки JSON/CSV?
- Якщо тільки артефакти — як відтворити replay або порівняти runs?
- Потрібна `stage10_replay_rows` таблиця або хоча б Pydantic схема

---

## Stage 11 — Production Trading Service

### 1. Критичне: py-clob-client потребує EIP-712 підписування — складніший процес

ТЗ §2 каже "Polymarket CLOB (wallet-based, USDC)" але:
- `polymarket_clob_adapter.py` потребує `CLOB_API_KEY` + `CLOB_SECRET` + `CLOB_PASSPHRASE`
  (або private key для підписування через eth-account + EIP-712)
- Polymarket CLOB підпис — складніший ніж звичайний HTTP auth: кожен ордер підписується
  приватним ключем Polygon wallet
- **Поточний стан системи**: CLOB mode зламаний (Gamma API не повертає bid/ask, окремий
  CLOB запит не реалізований). Це повинно бути виправлено ДО Stage 11, а не під час

---

### 2. Критичне: Multi-client architecture відсутня

Stage 11 згадує "клієнти" (множина) і "клієнтський звіт", але:
- Жодна таблиця в БД не має поля `client_id` або `tenant_id`
- `Stage8Position`, `Stage8Decision` — без прив'язки до клієнта
- Один приватний ключ гаманця — одна позиція. Різні клієнти потребують різних wallets
- Немає per-client exposure limits у risk_engine

**Це фундаментальна архітектурна прогалина**: без tenant isolation справжній
multi-client сервіс неможливий. Потрібно або один shared portfolio з відсотковим
розподілом, або окремий wallet/контекст per client. ТЗ не вирішує це питання.

---

### 3. Серйозне: Circuit breakers — нема порогів

ТЗ §7 визначає SOFT/HARD/PANIC але без числових значень:
- SOFT trigger: drawdown > X%? consecutive losses > N? За який period?
- HARD trigger: drawdown > Y%? risk-of-ruin > Z%?
- PANIC trigger: автоматично чи тільки manual? Хто має право reset?
- Нема визначення "вікна" для consecutive losses (5 trades? 24h? 7 days?)

Без конкретних порогів circuit breaker — декларація. Потрібен §7.1 з числами.

---

### 4. Серйозне: Idempotency на Polymarket CLOB

ТЗ §5 каже "idempotent place" але Polymarket CLOB:
- Не має server-side idempotency keys
- Order ID генерується клієнтом (hash-based)
- При network timeout: невідомо чи ордер досяг біржі
- Немає специфікації для `at-least-once` vs `exactly-once` семантики

Необхідно визначити: що робить `order_manager` при відсутності відповіді протягом
timeout? (Cancel + retry? Wait + poll fills?)

---

### 5. Серйозне: 14 днів LIMITED_EXECUTION статистично недостатньо

ТЗ §10.2 каже "стабільний мінімум 14 днів". Але:
- При консервативному Kelly (наприклад 0.10 від капіталу) і типовому ринку
  prediction markets (~5-10 торгів на тиждень) — 14 днів = ~20-30 executed trades
- 20-30 trades: неможливо статистично відрізнити edge від variance
- `p-value < 0.05` для precision > 0.5 потребує ~100+ samples при незбалансованих класах

Пропозиція: або збільшити до 30-45 днів, або зняти статистичну вимогу і зробити
pure "no critical incidents" gate без вимоги до realized return.

---

### 6. Серйозне: Custody model не визначена

ТЗ §3 виносить "Non-custodial custody provider implementation (окремий трек)" out of scope,
але тоді система або:
(a) сама тримає приватні ключі клієнтів (custodial — юридичний ризик, потрібна ліцензія)
(b) клієнти підписують кожен ордер самостійно (non-custodial — незручно для бота)
(c) hot wallet підконтрольний системі з обмеженим balance (operational model)

Варіант (c) — найреалістичніший для початку, але потребує явного визначення в ТЗ:
хто контролює гаманець, де зберігається private key, як задається max balance ліміт.

---

### 7. Середнє: Client reporting — нема формату і auth

ТЗ §4 і §11 згадують "клієнтський звіт" і `stage11_client_report_<timestamp>.csv`
але відсутні:
- Схема CSV/JSON (які поля?)
- Механізм доставки (API? email? dashboard?)
- Аутентифікація клієнта для доступу до звіту
- Чи містить звіт PnL окремого клієнта чи агрегований

---

### 8. Регуляторне: MiFID II / FCA для UA клієнтів

ТЗ §3 каже "Обхід регуляторних або platform-policy обмежень — out of scope" але не адресує:
- Automated trading service для EU clients (Польща) підпадає під **MiFID II** якщо
  service provider є юридичною особою в ЄС або обслуговує ЄС резидентів
- Для UK клієнта — **FCA** регулювання
- Polymarket сам по собі decentralized, але **service що автоматизує торгівлю** на ньому
  може кваліфікуватись як regulated activity
- Це не блокує розробку, але потрібне юридичне due diligence ДО live launch з реальними грошима

---

## Stage 12 — Multi-Source / Multi-Venue Expansion

### 1. Scoring matrix — ваги і пороги відсутні

ТЗ §7 визначає 8 осей (0..5) і `weighted_score`, але:
- Ваги кожної осі не визначені (рівні 1/8? custom?)
- Поріг для ADOPT vs PILOT vs REJECT не вказаний
- Без цього "scoring matrix" — суб'єктивна оцінка

Мінімум потрібно: ваги осей + `ADOPT >= X`, `PILOT >= Y`, `REJECT < Y`.

---

### 2. `source_contribution_delta_ev` — нема методології

ТЗ §8 включає цю метрику але:
- Як ізолювати contribution одного джерела? Causal inference потребує
  контрфактичного порівняння (з джерелом vs без)
- Без A/B тестування або held-out window це завжди confounded
- Потрібна методологія: наприклад, "re-run Stage 10 replay з і без source X
  на locked evaluation set і порівняти precision@25"

---

### 3. Cascade effects на Stage 7-9 pipeline не розглянуті

Додавання нового джерела (наприклад Smarkets) впливає на:
- Stage 7: `external_consensus` отримує нове поле → змінюється `contradiction` logic
- Stage 8: нова категорія coverage → змінюється policy profile
- Stage 9: нові labeled returns → змінюється calibration

ТЗ не описує як тестувати регресії в pipeline після підключення нового connector.
Потрібен "connector impact test" в acceptance gate §5.

---

### 4. "OpenClaw/community/інших" — невизначений список кандидатів

ТЗ §3 і §6 згадують готові модулі для evaluation але:
- "OpenClaw" — невідомий проект у контексті prediction markets
- "community" — не специфічно
- Без конкретного переліку кандидатів, security audit з §6 не має об'єкту

---

### 5. Quarterly vendor review — нема процесу

ТЗ §12 каже "quarterly review vendor/compliance ризиків" але:
- Хто проводить? (технічна команда? юрист?)
- Які inputs? (нові санкційні списки? API ToS зміни? security advisories?)
- Що є output? (оновлений Tier status? disable connector?)

---

## Крос-cutting проблеми (всі три ТЗ)

### 1. Відсутні DB migrations для нових таблиць

- Stage 10: `stage10_replay_rows` (якщо в БД) — нема в ТЗ
- Stage 11: `orders`, `fills`, `audit_trail`, `client_positions` — нема в ТЗ
- Stage 12: `connector_scorecard` — нема в ТЗ

Кожен Stage потребує явного розділу "DB schema changes + migration plan".

---

### 2. LLM cost estimation відсутня

- Stage 10: 100 events × 5 agent stacks × Stage 7 call = 500+ LLM calls
- При gpt-4o-mini ($0.002/call): ~$1 для одного повного прогону
- При gpt-4o ($0.01/call): ~$5
- При multi-policy scenario sweeps (18 scenarios): може вийти $50-100+ за повний benchmark
- Ні в одному ТЗ немає cost cap або бюджету на LLM під час replay

---

### 3. Механізм переходу між режимами SHADOW→LIMITED→FULL не автоматизований

ТЗ Stage 11 §4 визначає режими але не визначає:
- Хто і як перемикає режими? Manual через config? API endpoint?
- Чи є automated gate (система сама переходить в LIMITED якщо метрики pass)?
- Чи є rollback trigger для автоматичного повернення в SHADOW?
- Відповідь на це в Stage 8 є (rollback triggers §12.1), але для Stage 11 execution — ні

---

### 4. Відсутній "replay vs live consistency" check

Stage 10 дає baseline на historical data. Stage 11 live execution може відрізнятись через:
- Market impact (у Stage 10 ми не рухали ринок, у Stage 11 рухаємо)
- Slippage реалізований vs очікуваний
- Timing: replay може ставити рішення в "ідеальний" момент

Потрібен окремий §"replay-to-live drift monitoring" в Stage 11 з KPI
`slippage_drift_vs_stage10` і `ev_drift_vs_stage10_baseline`.

---

## Пріоритизація виправлень

| # | Проблема | Stage | Пріоритет |
|---|---|---|---|
| 1 | Historical probability timeline — джерело не визначене | S10 | 🔴 Критично |
| 2 | Anti-lookahead алгоритм не специфікований | S10 | 🔴 Критично |
| 3 | Multi-client architecture відсутня в БД | S11 | 🔴 Критично |
| 4 | CLOB adapter потребує EIP-712 підпису — складніший ніж описано | S11 | 🔴 Критично |
| 5 | Circuit breaker threshold values відсутні | S11 | 🟠 Серйозно |
| 6 | Custody model не вибрана | S11 | 🟠 Серйозно |
| 7 | Stage 7 labeled returns в replay mode = lookahead leakage | S10 | 🟠 Серйозно |
| 8 | DB migrations не специфіковані | S10/11/12 | 🟠 Серйозно |
| 9 | LLM cost cap відсутній | S10 | 🟡 Середньо |
| 10 | Scoring matrix ваги не визначені | S12 | 🟡 Середньо |
| 11 | 14 днів LIMITED_EXECUTION статистично недостатньо | S11 | 🟡 Середньо |
| 12 | Регуляторний статус для EU/UK клієнтів не адресований | S11 | 🟡 Середньо |
| 13 | `reason_code_stability` без формули | S10 | 🔵 Дрібно |
| 14 | `source_contribution_delta_ev` без методології | S12 | 🔵 Дрібно |
| 15 | Client reporting формат і auth відсутні | S11 | 🔵 Дрібно |
