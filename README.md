# Prediction Market Scanner (MVP)

Prediction Market Scanner is a market-intelligence backend for prediction markets. It is **not** a trading bot.

## Server
- **IPv4**: `173.242.53.177`
- **IPv6**: `2a00:7a60:0:35b1::2`
- SSH: `ssh root@173.242.53.177`

## Documentation
- Full technical docs are in [`docs/`](./docs/README.md).

## What it does
- Collects market data from Manifold, Metaculus, and Polymarket (partial).
- Normalizes markets into one schema.
- Runs deterministic analyzers:
  - weird market checks
  - duplicate/similar market detection
  - divergence across similar markets
  - rules risk scoring
- cooldown-based signal deduplication (anti-spam)
- Generates and stores ranked signals.
- Exposes REST API.
- Delivers signals via Telegram bot commands.

## Architecture overview
- `app/services/collectors`: platform collectors + sync service
- `app/services/analyzers`: deterministic analysis layer
- `app/services/signals`: signal generation engine
- `app/api/routes`: FastAPI routes
- `app/bot`: aiogram bot handlers
- `app/tasks`: Celery worker and jobs
- `app/models`: SQLAlchemy models
- `alembic`: migrations

## Stack
Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis, Celery, aiogram, Docker.

## Run locally with Docker
1. Copy env:
```bash
cp .env.example .env
```
2. Start services:
```bash
docker compose up --build
```
Quick status diagnostics:
```bash
./scripts/doctor.sh
```
3. Run migrations and seed (from `api` container):
```bash
docker compose exec api alembic upgrade head
docker compose exec api python scripts/seed.py
```

## Run without Docker
1. Install deps:
```bash
pip install -e .
```
2. Set envs from `.env.example`.
3. Run migrations:
```bash
alembic upgrade head
python scripts/seed.py
```
4. Start API:
```bash
uvicorn app.main:app --reload
```
5. Start worker:
```bash
celery -A app.tasks.worker.celery_app worker -B -l info
```
6. Start bot:
```bash
python -m app.bot.bot_app
```

## REST API
- `GET /health`
- `GET /markets`
  - optional filters: `platform`, `status`, `category`
- `GET /markets/{id}`
- `GET /markets/{id}/analysis`
- `GET /signals`
  - optional filters: `signal_type`, `platform`, `min_confidence`
- `GET /signals/{id}`
- `GET /signals/top`
- `GET /signals/latest`
- `GET /analytics/duplicates`
- `GET /analytics/liquidity-risk`
- `GET /analytics/rules-risk`
- `GET /analytics/divergence`
- `GET /analytics/kpi`
- `GET /analytics/retention`
- `GET /watchlist`
- `POST /watchlist/add`
- `POST /watchlist/remove`
- `GET /digest`
- `GET /user`
- `GET /me`
- `GET /plans`
- `POST /admin/sync-markets`
  - optional query param: `platform=manifold|metaculus|polymarket`
- `POST /admin/run-analysis`
- `POST /admin/send-test-signal`

Admin endpoints require header: `X-API-Key: <ADMIN_API_KEY>`.

## Telegram commands
`/start`, `/help`, `/top`, `/signals`, `/watchlist`, `/add <market_id>`, `/remove <market_id>`, `/digest`, `/me`, `/plans`

## Trigger jobs
```bash
curl -X POST http://localhost:8000/admin/sync-markets -H "X-API-Key: change-me"
curl -X POST "http://localhost:8000/admin/sync-markets?platform=manifold" -H "X-API-Key: change-me"
curl -X POST http://localhost:8000/admin/run-analysis -H "X-API-Key: change-me"
```

## Analysis pipeline
The deterministic analysis flow runs in this order:
1. `detect_duplicates`:
   - fuzzy title matching with normalized text
   - only cross-platform and reasonably comparable markets (category/time window)
2. `analyze_rules`:
   - keyword-based rules-risk scoring and flags
   - weird-market checks for malformed probabilities
3. `detect_divergence`:
   - absolute probability distance for duplicate pairs
4. `generate_signals`:
   - emits `DUPLICATE_MARKET`, `DIVERGENCE`, `RULES_RISK`, `WEIRD_MARKET`, `WATCHLIST`
   - applies 24h cooldown dedup strategy

You can run each phase via Celery tasks:
- `detect_duplicates`
- `analyze_rules`
- `detect_divergence`
- `generate_signals`

## Telegram product layer (Stage 4)
- `/top` ranking formula:
  - `score = 0.6*divergence + 0.3*liquidity - 0.2*rules_risk`
- plan gates:
  - `FREE`: 3 signals/day, watchlist up to 3 markets
  - `PRO`: 20 signals/day, watchlist up to 20 markets
  - `PREMIUM`: high limits (MVP approximation of unlimited)
- digest:
  - compact daily market summary designed to reduce spam
- tracked events:
  - `signal_sent`
  - `market_opened`
  - `watchlist_added`

## Scheduler jobs
- `daily_digest`
- `signal_push` (real Telegram send via Bot API when `TELEGRAM_BOT_TOKEN` is configured)
- `cleanup_old_signals`
- `update_watchlists`

## Validate collectors quickly
```bash
python scripts/validate_collectors.py
```
The script runs sync for Manifold and Metaculus separately and prints saved market counts.

## Fast troubleshooting
- `./scripts/doctor.sh`:
  - checks container state
  - checks `/health`
  - checks DB auth/query
  - checks bot-to-DB connection
  - prints recent error lines from `bot` and `api` logs
- if you changed `.env`, restart cleanly:
```bash
docker compose up -d --build --force-recreate
```

## Assumptions
- Manifold API uses millisecond timestamps (`createdTime`, `closeTime`).
- Metaculus may require explicit `User-Agent` and can require token access depending on endpoint policy.
- Polymarket integration is partial and field-mapping may require updates with latest docs.
- Liquidity score is proxy-based when order-book details are missing.
- Duplicate matching is deterministic and may miss semantic paraphrases.

## Known limitations / TODO
- No payment integration.
- No advanced auth (MVP-level identity via Telegram ID / header).
- No frontend dashboard.
- Duplicate detection uses fuzzy title matching only.
- Rules analysis is keyword-based and deterministic.
- Telegram push scheduling is minimal; currently on-demand command flow.
- Polymarket collector fields (`probability`, `volume24h`, `liquidity`) are partially inferred and require API schema confirmation.
- No semantic/LLM matching yet; future upgrade can improve duplicate and rules interpretation.
