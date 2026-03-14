# ТЗ — Stage 5 Historical Backfill & Validation

## 1. Мета

Швидко отримати достатній історичний датасет для Stage 5 (`Signal Quality Research`) і виконати повну перевірку алгоритмів на історичних даних без очікування довгого production-вікна.

Результат: формалізоване рішення `KEEP/MODIFY/REMOVE` по signal types на основі репрезентативного historical sample + готовий пакет артефактів для аудиту.

## 2. Scope

У межах цього ТЗ:

1. Імпорт історичних даних із зовнішніх провайдерів і локальної БД.
2. Нормалізація даних у формат Stage 5 (`signal_history` compatible).
3. Labeling історичних записів по горизонтах `1h/6h/24h/resolution`.
4. Запуск повного Stage 5 research batch.
5. Формальна перевірка acceptance критеріїв.

Поза межами:

1. Реальний live trading/execution.
2. Оптимізація ML-моделей beyond Stage 5 baseline.
3. UX-зміни в Telegram продукті.

## 3. Джерела даних (priority order)

### 3.1 Mandatory baseline

1. Локальні таблиці сервісу: `markets`, `market_snapshots`, `signals`, `signal_history`, `job_runs`, `user_events`.

### 3.2 External enrichment

1. `Manifold` historical dumps / API.
2. `Polymarket` Gamma/CLOB historical data.
3. `Metaculus` timeseries (для аналітичного референсу).
4. (Optional) `Kalshi` historical endpoints.

## 4. Data Contract для backfill

Кожен historical record після нормалізації має містити мінімум:

1. `signal_type`
2. `platform`
3. `market_id` (внутрішній або canonical external mapping)
4. `timestamp`
5. `probability_at_signal`
6. `related_market_probability` (для divergence/event-cluster, якщо доступно)
7. `divergence` (якщо застосовно)
8. `liquidity`
9. `volume_24h`
10. labels: `probability_after_1h`, `probability_after_6h`, `probability_after_24h`
11. `resolved_probability` (якщо доступно)
12. `simulated_trade` JSON (execution assumptions + costs)

## 5. Вимоги до обсягу вибірки

Мінімальні цілі:

1. Загалом `>= 2000` labeled historical rows.
2. Для `DIVERGENCE` `>= 500` labeled rows.
3. Для кожного активного signal type `>= 150` labeled rows.
4. Частка `INSUFFICIENT_DATA` у фінальному per-type рішенні `<= 25%`.

Stretch goals:

1. `>= 5000` labeled rows total.
2. `>= 1000` divergence rows.

## 6. План реалізації

### Phase A — Ingestion

1. Реалізувати/оновити скрипт ingest для historical sources.
2. Вести `source_tag` для кожного запису (`local`, `manifold_dump`, `polymarket_api`, ...).
3. Заборонити дублікати через idempotent ключ:
   `(<platform>, <external_market_id>, <timestamp_bucket>, <signal_type>)`.

### Phase B — Normalization + Labeling

1. Перетворити records у schema сумісну з `signal_history`.
2. Проставити `probability_after_1h/6h/24h` із historical timeseries.
3. Якщо горизонт недоступний, ставити `NULL` і логувати `missing_label_reason`.

### Phase C — Research Batch

1. Запустити one-shot batch:
   `scripts/stage5_track_batch.py`
2. Згенерувати:
   - `stage5_batch_<ts>.json`
   - `stage5_export_<ts>.csv`
3. Зафіксувати `stack_readiness` і `readiness_gate`.

### Phase D — Acceptance

1. Перевірити критерії секції 9 цього ТЗ.
2. Сформувати фінальний висновок:
   - `Ready for production decisioning`
   - `Needs more data`
   - `Needs model/rules adjustments`

## 7. Команди запуску (reference)

### 7.1 Local fallback (SQLite)

```bash
DATABASE_URL=sqlite:///artifacts/research/stage5_local.db \
REDIS_URL=redis://localhost:6379/0 \
.venv/bin/python scripts/stage5_track_batch.py
```

### 7.2 Production-like (Postgres)

```bash
DOCKER_API_VERSION=1.44 POSTGRES_PASSWORD=<your_password> docker compose up -d db redis api
```

Після цього:

```bash
curl -sS http://127.0.0.1:8000/analytics/research/stack-readiness
curl -sS "http://127.0.0.1:8000/analytics/research/readiness-gate?days=30&horizon=6h"
```

## 8. Метрики якості даних (Data Quality Gates)

Blocking gates:

1. `null_probability_at_signal_rate <= 2%`
2. `invalid_probability_range_rate == 0%`
3. `negative_volume_or_liquidity_rate == 0%`
4. `duplicate_key_collisions <= 0.5%`

Warning gates:

1. `unlabeled_6h_share <= 20%`
2. `unlabeled_24h_share <= 35%`
3. `cross_platform_mapped_share >= 40%` (для divergence-focused run)

## 9. Acceptance Criteria (DoD)

ТЗ вважається виконаним, якщо одночасно:

1. `stack_readiness.advanced_ready == true`
2. `stack_readiness.has_blocking_issues == false`
3. `signal_types.decision_counts.INSUFFICIENT_DATA <= 25%` від total requested
4. `readiness_gate.status in {PASS, WARN}` (FAIL — не приймається)
5. Є валідні артефакти:
   - JSON batch report
   - CSV export
   - experiment registry entries (`tracked_runs >= 10`)
6. Для `DIVERGENCE` є статистично значима вибірка (`>= 500 labeled`)

## 10. Артефакти (обов'язково)

1. `artifacts/research/stage5_batch_<timestamp>.json`
2. `artifacts/research/stage5_export_<timestamp>.csv`
3. `artifacts/research/experiments.jsonl`
4. Короткий human summary (`docs/TZ_V3_STATUS.md` update)

## 11. Ризики і mitigation

1. API/rate limits провайдерів:
   - Mitigation: batch windows, retries, local cache.
2. Неповні labels на довгих горизонтах:
   - Mitigation: fallback horizon analysis (`6h`) + explicit confidence flags.
3. Schema drift у зовнішніх API:
   - Mitigation: adapter layer + source version tags.
4. Низька репрезентативність по окремим signal types:
   - Mitigation: decision `INSUFFICIENT_DATA`, не force-fit.

## 12. Rollback Plan

Якщо ingestion/backfill впливає на стабільність:

1. Вимкнути external backfill jobs.
2. Повернутись до baseline `local signal_history only`.
3. Залишити Stage 5 в режимі `research-only` без policy update.
4. Зберегти partial artifacts для post-mortem.

## 13. Final Output Template

Після завершення виконавець надає:

1. `Coverage`: скільки rows, labels, signal types.
2. `Gate status`: PASS/WARN/FAIL + причини.
3. `Decisions`: KEEP/MODIFY/REMOVE/INSUFFICIENT_DATA.
4. `Recommended config updates`: thresholds/ranking changes.
5. `Next iteration plan` (якщо залишились data gaps).
