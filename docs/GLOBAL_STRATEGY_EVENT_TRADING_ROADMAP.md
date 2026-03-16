# Global Strategy: Event Trading Roadmap

## 1. Контекст і ціль

Мета проєкту:
1. Побудувати прибутковий event-based trading контур (не HFT, не біржовий скальпінг).
2. Довести на історичних подіях, що система дає стабільний post-cost edge.
3. Запустити production-сервіс для клієнтів із контрольованим ризиком і прозорою аналітикою.

Ключова ідея:
1. Не чекати 6+ місяців накопичення live-даних.
2. Використати історичні події (resolved outcomes) через event-replay.
3. Далі перейти в live rollout зі Stage-gates.

## 2. Три сходинки (програма виконання)

## 2.1 Сходинка 1: Історичний прогін алгоритму (Event Replay Engine)

Ціль:
1. Зібрати та прогнати мінімум 100 подій у різних галузях.
2. Зрозуміти, що реально працює, а що ні.
3. Перевірити чи потрібні окремі алгоритми по категоріях.

Обсяг:
1. Події мінімум у 4 категоріях:
   - politics,
   - sports,
   - crypto,
   - finance/macro.
2. Для кожної події:
   - повна часова лінія прогнозів до резолюції,
   - snapshot features у моменті `t`,
   - replay рішення агента/політики,
   - dry-run execution,
   - post-hoc outcome evaluation.

Обов’язкові правила:
1. Строге anti-lookahead:
   - у момент `t` дозволені тільки дані, доступні на `t`.
2. Direction-aware evaluation:
   - YES/NO логіка успіху.
3. Post-cost only:
   - усі рішення валідні тільки після врахування fees/spread/slippage.

KPI для завершення Сходинки 1:
1. `events_total >= 100`
2. `coverage_by_category >= 20 подій` у кожній core-категорії.
3. Є baseline і top-3 policy профілі по:
   - precision@25,
   - EV after costs,
   - Brier/ECE.
4. Є висновок:
   - один універсальний policy чи category-specific policies.

Deliverables:
1. Event replay dataset + schema.
2. Replay batch artifacts (`json/csv`).
3. Comparative report по агентах/алгоритмах.
4. Рішення для переходу в Сходинку 2 (`GO/WARN/NO_GO`).

## 2.2 Сходинка 2: Повний trading-сервіс

Ціль:
1. Перевести validated policy у production execution-контур.
2. Дати клієнтам керований сервіс з прозорим контролем ризику.

Обсяг:
1. Перший execution venue:
   - Polymarket CLOB (crypto settlement).
2. Режими:
   - shadow,
   - limited execution,
   - full execution (після gate).
3. Обов’язкові модулі:
   - order routing,
   - fill tracking,
   - PnL attribution,
   - circuit breakers,
   - audit trail per decision.

KPI для завершення Сходинки 2:
1. Стабільний `LIMITED_GO` або `GO` на live-window.
2. Відсутність critical execution errors.
3. Контрольований drawdown і risk-of-ruin в межах policy.
4. Прозорий клієнтський звіт:
   - чому ставка/чому skip,
   - expected vs realized outcome.

Deliverables:
1. Trading service API + worker pipeline.
2. Client-facing reporting (daily/weekly).
3. Incident/rollback playbook.

## 2.3 Сходинка 3: Поступове підключення всіх сервісів

Ціль:
1. Розширити data+execution покриття без втрати якості.
2. Підключати джерела в порядку ROI та операційної простоти.

Принцип підключення:
1. Спочатку venue з crypto settlement і простим входом.
2. Потім regulated venue з KYC/compliance.
3. Кожен новий connector проходить окремий acceptance-gate.

Порядок (перший пріоритет):
1. Polymarket CLOB (повна стабілізація).
2. Metaculus + Manifold (anchor + validation).
3. Далі за можливістю:
   - Kalshi (де доступно),
   - Smarkets/Betfair (після compliance).

KPI для завершення Сходинки 3:
1. Multi-source consensus стабільний (не менше двох валідних джерел у більшості кейсів).
2. Кожне нове джерело має позитивний contribution в out-of-sample метриках.
3. Жоден connector не є single point of failure.

Deliverables:
1. Source integration scorecard.
2. Reliability/latency/cost report per source.
3. Оновлений routing policy по venue.

## 3. Дослідницький контур (що обов’язково перевіряємо)

## 3.1 Алгоритми і категорії

Потрібно явно перевірити:
1. Один global policy vs category-specific policies.
2. Чи дає category-specific кращий post-cost edge.
3. Які фічі найбільш корисні для кожної категорії.

## 3.2 Агенти та нейромоделі

Потрібно оцінити:
1. Який агентний стек кращий як verification-layer.
2. Які API моделей кращі по:
   - стабільності reason-codes,
   - latency,
   - ціні,
   - governance fit.
3. Де LLM корисний:
   - rules ambiguity, contradiction checks.
4. Де LLM заборонений:
   - direct EV/probability forecasting як єдине джерело.

## 3.3 Джерела даних

Потрібно перевірити:
1. Яких джерел бракує для edge.
2. Які з нових джерел реально додають точність, а які тільки шум.
3. Чи виправдана вартість/складність кожного нового джерела.

## 4. Критерії переходу між сходинками

## 4.1 Перехід 1 -> 2

Умови:
1. `>=100` replay events.
2. Є мінімум 1 policy-кандидат з позитивним post-cost профілем.
3. Нема критичних leakage/overfit прапорців.

## 4.2 Перехід 2 -> 3

Умови:
1. Live limited rollout стабільний.
2. Виконуються risk-gates.
3. Є ресурс на підключення нових connector-ів без деградації core execution.

## 5. Ризики і контроль

Основні ризики:
1. Дані неякісні або неповні -> хибний edge.
2. Overfitting на історичних подіях.
3. Vendor/API drift.
4. Compliance limitations per client/platform.

Контроль:
1. Locked evaluation windows.
2. Walk-forward + embargo.
3. Feature flags per platform.
4. Clear fallback modes (`EXECUTE_ALLOWED` / `SHADOW_ONLY` / `DATA_ONLY`).

## 6. Пріоритет на поточний момент

Що робимо зараз:
1. Завершуємо Stage 9 стабілізацію (дані + execution realism).
2. Формуємо окреме ТЗ на Сходинку 1:
   - Event Replay Engine на 100 подій.
3. Після replay-вердикту формуємо ТЗ на Сходинку 2 (production trading service).
4. Паралельно готуємо каталог інтеграцій для Сходинки 3.

## 7. Наступні документи (розбиття цього roadmap)

Після цього документа створюються три окремі ТЗ:
1. `TZ_STAGE10_EVENT_REPLAY_100_EVENTS.md` (Сходинка 1)
2. `TZ_STAGE11_PRODUCTION_TRADING_SERVICE.md` (Сходинка 2)
3. `TZ_STAGE12_MULTI_SOURCE_MULTI_VENUE_EXPANSION.md` (Сходинка 3)
