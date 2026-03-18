# Operations

## Сервер

- **IPv4:** `173.242.53.177`
- **IPv6:** `2a00:7a60:0:35b1::2`
- SSH: `ssh root@173.242.53.177`
- Проект: `/root/prediction_market_scanner`

## Стандартний цикл (автоматичний через Celery Beat)

| Задача | Розклад | Що робить |
|--------|---------|-----------|
| `sync_all_platforms` | кожні 15 хв | Синк ринків Manifold/Metaculus/Polymarket |
| `analyze_markets` | кожні 15 хв (+2 хв offset) | Rules + liquidity аналіз, divergence candidates |
| `detect_duplicates` | кожні 2 год | Дублікати між платформами |
| `analyze_rules` | кожні 20 хв (+4 хв) | Ризик-аналіз правил |
| `detect_divergence` | кожні 20 хв (+6 хв) | Дивергенція між дублікатами |
| `generate_signals` | кожні 20 хв (+8 хв) | Генерація сигналів |
| `stage7_evaluate` | кожні 30 хв | AI-оцінка сигналів (KEEP/SKIP) |
| `stage11_reconcile` | кожні 10 хв | Reconcile dry-run позицій |
| `label_signal_history` | кожні 15 хв | Labeling сигналів (всі горизонти: 15m/30m/1h/6h/24h) |
| `label_signal_history_resolution` | щогодини (+10 хв) | Labeling по фактичній резолюції |
| `signal_push` | кожні 30 хв | Push сигналів у Telegram |
| `update_watchlists` | щогодини | Оновлення вотчлистів |
| `daily_digest` | 09:00 UTC | Щоденний дайджест |
| `provider_contract_checks` | щогодини (+40 хв) | Перевірка контрактів провайдерів |
| `quality_snapshot` | 00:10 UTC | Денний знімок якості |
| `cleanup_old_signals` | 03:00 UTC | Видалення старих сигналів |
| `cleanup_signal_history` | 03:20 UTC | Видалення старих записів history |
| `stage7/8/9/10/11 track` | нічні задачі (02:45–03:25) | Звіти та трекінг по стадіях |

## Docker deploy (сервер)

```bash
ssh root@173.242.53.177
cd /root/prediction_market_scanner
git pull origin main
DOCKER_BUILDKIT=0 docker compose build
docker compose up -d --no-build

# Застосувати міграції
docker exec prediction_market_scanner-worker-1 python3 -c "
from alembic.config import Config; from alembic import command; import os
cfg = Config('/app/alembic.ini')
cfg.set_main_option('sqlalchemy.url', os.environ['DATABASE_URL'])
command.upgrade(cfg, 'head')
"
```

## Dry-run симулятор

```bash
# Відкрити нові позиції
curl -X POST http://localhost:8000/admin/dryrun/run -H "x-api-key: <KEY>"

# Звіт
curl http://localhost:8000/admin/dryrun/report -H "x-api-key: <KEY>"

# Оновити mark prices
curl -X POST http://localhost:8000/admin/dryrun/refresh-prices -H "x-api-key: <KEY>"

# Скинути портфель
curl -X POST http://localhost:8000/admin/dryrun/reset -H "x-api-key: <KEY>"
```

## Ручний запуск задачі

```bash
docker exec prediction_market_scanner-worker-1 \
  celery -A app.tasks.worker.celery_app call <task_name>
```

## Діагностика

```bash
# Логи worker в реальному часі
docker logs -f prediction_market_scanner-worker-1

# Stage7 рішення
docker exec prediction_market_scanner-db-1 psql -U postgres -d prediction_scanner -c \
  "SELECT signal_id, decision, reason_codes FROM stage7_agent_decisions ORDER BY created_at DESC LIMIT 20;"

# Відкриті dry-run позиції
docker exec prediction_market_scanner-db-1 psql -U postgres -d prediction_scanner -c \
  "SELECT m.title, dp.direction, dp.entry_price, dp.notional_usd, dp.status FROM dryrun_positions dp JOIN markets m ON dp.market_id=m.id WHERE dp.status='OPEN';"
```

## Діагностика duplicate

1. `/analytics/platform-distribution`
2. `/analytics/cross-platform-pairs`
3. `/analytics/duplicate-drop-reasons`
4. `/analytics/duplicate-shadow?broad_threshold=...`

## Моніторинг KPI

1. `zero_move_arbitrage_ratio(momentum)`
2. `missing_rules_share_top_window`
3. `avg_signal_diversity_top_window`
4. `avg_actionable_rate`
5. `simulated_edge_mean`, `top5_utility_daily`

## Очищення диску (сервер)

```bash
docker image prune -a --force
docker builder prune --force
docker container prune --force
docker volume prune --force
```
