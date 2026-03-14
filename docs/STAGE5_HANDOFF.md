# Stage 5 Handoff

Короткий сценарій запуску Stage 5 research після деплою.

## 1. Перевірка даних

1. `GET /analytics/signal-history?days=30`
2. `GET /analytics/research/progress?target_samples=500&lookback_days=7`
3. `GET /analytics/research/data-quality?days=30`

### 1.1 Historical Backfill (Manifold)

За потреби прискорити набір історичних семплів:

```bash
DATABASE_URL=<db_url> REDIS_URL=<redis_url> \
.venv/bin/python scripts/ingest_historical.py --provider manifold --max-pages 30 --page-size 200 --start-days 120
```

Для реальних часових рядів (bets) з авто-лейблами `1h/6h/24h`:

```bash
DATABASE_URL=<db_url> REDIS_URL=<redis_url> \
.venv/bin/python scripts/ingest_historical.py \
  --provider manifold_bets \
  --max-pages 80 \
  --page-size 200 \
  --start-days 365 \
  --warmup-market-pages 20 \
  --max-missing-market-fetches 1000
```

Примітки:

1. Для `/markets` і `/bets` використовується cursor-пагінація `before=<id>` (не timestamp).
2. `manifold_bets` warmup апдейтить тільки `markets`, без зайвого запису в `signal_history`.
3. Якщо `contractId` відсутній у локальній БД, скрипт добирає market через `/market/{id}`.

### 1.2 Cross-Platform Market Bootstrap (Metaculus + Polymarket)

Щоб покращити cross-platform matching перед `manifold_bets`, додайте market universe:

```bash
DATABASE_URL=<db_url> REDIS_URL=<redis_url> \
.venv/bin/python scripts/ingest_historical.py \
  --provider xplat_markets \
  --max-pages 20 \
  --page-size 200 \
  --start-days 365
```

Доступні також окремі провайдери:

1. `--provider metaculus_markets` (потребує `METACULUS_API_TOKEN`)
2. `--provider polymarket_markets`

Після bootstrap рекомендується запускати `manifold_bets` на ту саму БД для формування `DIVERGENCE`.

## 2. Базовий аналіз сигналів

1. `GET /analytics/research/signal-types?days=30&horizon=6h`
2. `GET /analytics/research/divergence-thresholds?days=30&horizon=6h`
3. `GET /analytics/research/divergence-decision?days=30&horizon=6h`
4. `GET /analytics/research/signal-types/optimize?signal_type=DIVERGENCE&days=365&horizon=6h`
5. `GET /analytics/research/signal-types/optimize?signal_type=RULES_RISK&days=365&horizon=6h`

## 3. Виконуваність і ризик

1. `GET /analytics/research/monte-carlo?days=30&horizon=6h`
2. `GET /analytics/research/liquidity-safety?days=30&position_sizes=50,100,500`
3. `GET /analytics/research/signal-lifetime?days=30`

## 4. Порівняння підходів

1. `GET /analytics/research/ranking-formulas?days=30&horizon=6h`
2. `GET /analytics/research/platform-comparison?days=30&horizon=6h`
3. `GET /analytics/research/market-categories?days=30&horizon=6h`
4. `GET /analytics/research/event-clusters?days=30&horizon=6h`

## 5. Операційні/продакшн-контроли

1. `GET /analytics/research/provider-reliability?days=7`
2. `GET /analytics/research/provider-contract-checks`
3. `GET /analytics/research/stack-readiness`
4. `GET /analytics/research/ab-testing?days=30`
5. `GET /analytics/research/ethics`

## 6. Фінальні артефакти

1. `GET /analytics/research/final-report?days=30&horizon=6h`
2. `GET /analytics/research/readiness-gate?days=30&horizon=6h`
3. `GET /analytics/research/export-package?days=30&horizon=6h`
4. `GET /analytics/research/export-package.csv?days=30&horizon=6h`

## 7. Tracking (опційно)

Для ключових звітів є `POST .../track` endpoint-и, які записують run у experiment registry.

## 8. One-shot Local Batch

Для локального прогону всіх Stage 5 report+tracking кроків одним запуском:

```bash
DATABASE_URL=sqlite:///artifacts/research/stage5_local.db \
REDIS_URL=redis://localhost:6379/0 \
.venv/bin/python scripts/stage5_track_batch.py
```

Скрипт генерує:

1. `artifacts/research/stage5_batch_<timestamp>.json`
2. `artifacts/research/stage5_export_<timestamp>.csv`
