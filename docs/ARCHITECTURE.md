# Architecture

## Компоненти

1. `api` (FastAPI): публічні та адмін endpoint-и.
2. `worker` (Celery + beat): періодичний запуск pipeline.
3. `bot` (Telegram): продуктовий шар для користувачів.
4. `db` (PostgreSQL): ринки, аналізи, сигнали, quality-метрики.
5. `redis`: брокер/бекенд задач Celery.

## Pipeline сигналів

1. Sync ринків із провайдерів (Manifold/Metaculus/Polymarket).
2. `detect_duplicates()`:
   - broad candidate pass,
   - strict evaluation pass,
   - persistence `DuplicateMarketPair` + `DuplicatePairCandidate`.
3. `analyze_rules()` + `liquidity` аналіз.
4. `detect_divergence()` для strict duplicate пар.
5. `generate_signals()`:
   - duplicate/divergence сигнали,
   - rules-risk (`explicit_rules_risk` / `missing_rules_risk`),
   - arbitrage (`momentum` / `uncertainty_liquid`),
   - execution-analysis + score breakdown.
6. `quality_snapshot_job()` зберігає щоденні KPI у `signal_quality_metrics`.

## Основні таблиці

1. `markets`, `market_snapshots`, `platforms`.
2. `rules_analyses`, `liquidity_analyses`.
3. `duplicate_market_pairs`, `duplicate_pair_candidates`.
4. `signals` (з `signal_mode`, `score_breakdown_json`, `execution_analysis`, `updated_at`).
5. `signal_generation_stats` (денні лічильники/caps).
6. `signal_quality_metrics` (daily quality telemetry).
