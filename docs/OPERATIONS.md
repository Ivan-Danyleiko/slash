# Operations

## Стандартний цикл

1. `POST /admin/sync-markets`
2. `POST /admin/run-analysis`
3. `POST /admin/quality-snapshot`
4. Перевірка:
   - `/analytics/quality?days=7`
   - `/analytics/duplicate-shadow`
   - `/signals/top`

## Docker deploy

1. `docker compose up -d --build api worker bot`
2. Перевірка health:
   - `docker compose ps`
   - `curl http://localhost:8000/health`

## Діагностика duplicate

1. Подивитись загальний розподіл:
   - `/analytics/platform-distribution`
   - `/analytics/cross-platform-pairs`
2. Подивитись strict fail причини:
   - `/analytics/duplicate-drop-reasons`
3. Підібрати shadow пороги без прод-впливу:
   - `/analytics/duplicate-shadow?broad_threshold=...&broad_relaxed_fuzzy_min=...`

## Моніторинг KPI

1. `zero_move_arbitrage_ratio(momentum)`
2. `missing_rules_share_top_window`
3. `avg_signal_diversity_top_window`
4. `avg_actionable_rate`
5. `simulated_edge_mean`, `top5_utility_daily`
