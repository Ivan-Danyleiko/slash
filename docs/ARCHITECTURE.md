# Architecture

## Компоненти

1. `api` (FastAPI): публічні та адмін endpoint-и.
2. `worker` (Celery + beat): періодичний запуск pipeline.
3. `bot` (Telegram): продуктовий шар для користувачів.
4. `db` (PostgreSQL): ринки, аналізи, сигнали, quality-метрики.
5. `redis`: брокер/бекенд задач Celery.

## Pipeline сигналів

1. **Sync** ринків із провайдерів (Manifold/Metaculus/Polymarket CLOB).
2. **`detect_duplicates()`** — broad + strict pass, persistence `DuplicateMarketPair`.
3. **`analyze_markets()`** — rules + liquidity аналіз, divergence candidates.
4. **`detect_divergence()`** — дивергенція для strict-duplicate пар.
5. **`generate_signals()`**:
   - duplicate/divergence сигнали
   - rules-risk (`explicit_rules_risk` / `missing_rules_risk`)
   - arbitrage (`momentum` / `uncertainty_liquid`, midpoint band ±25%)
   - execution-analysis + score breakdown
6. **Stage 7** — AI-verification layer (FallbackAdapter: groq→gemini→openrouter):
   - internal gate → external verifier → decision composer
   - рішення: KEEP / MODIFY / REMOVE / SKIP
   - кеш по `input_hash`
7. **Dry-run simulator** — паперовий портфель $100:
   - відкриває позиції на KEEP-сигналах з CLOB bid/ask
   - hard limits: spread ≤8%, resolution ≤180 днів, volume ≥$5k
   - mark-to-market через CLOB API
8. **`quality_snapshot_job()`** — щоденні KPI у `signal_quality_metrics`.

## Polymarket CLOB інтеграція

- Колектор отримує до 3000 ринків (Pass 1) + до 500 near-term (Pass 2)
- CLOB bid/ask завантажується тільки для ринків з ліквідністю ≥$1000
- Дані зберігаються у `Market.best_bid_yes`, `best_ask_yes`, `spread_cents`

## Основні таблиці

1. `markets`, `market_snapshots`, `platforms`
2. `rules_analyses`, `liquidity_analyses`
3. `duplicate_market_pairs`, `duplicate_pair_candidates`
4. `signals` (з `signal_mode`, `score_breakdown_json`, `execution_analysis`, `signal_direction`)
5. `signal_generation_stats` (денні лічильники/caps)
6. `signal_quality_metrics` (daily quality telemetry)
7. `stage7_agent_decisions` (AI рішення з кешем по `input_hash`)
8. `stage8_decisions` (Stage 8 shadow ledger, залежить від stage7)
9. `dryrun_portfolio`, `dryrun_positions` (паперовий портфель)
10. `signal_history` (для labeling та resolution tracking)

## Performance indexes (міграція 0018)

- `ix_signal_type_created_at` — фільтр по типу + сортування
- `ix_signal_market_id_type` — join з stage7
- `ix_stage7_signal_decision` — join по signal_id + decision
- `ix_stage7_created_at` — останнє рішення
- `ix_market_snapshot_market_fetched` — останній snapshot per market
- `ix_dryrun_positions_portfolio_status_deadline` — відкриті позиції
- `ix_market_resolution_time_platform` — near-term фільтр
