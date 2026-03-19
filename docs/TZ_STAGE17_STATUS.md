# Stage 17 Status

## Поточний стан
- Статус: `implementation_complete`
- Дата оновлення: `2026-03-19`

## Закрито по функціоналу
- Tail signal generation (`TAIL_EVENT_CANDIDATE`) у `SignalEngine`.
- Hard ambiguity block (rule-based, без LLM виклику).
- Base-rate estimator:
  - USGS Poisson для natural disaster.
  - Binance log-normal для crypto level.
  - Historical prior fallback.
- Tail LLM reviewer (deterministic):
  - `temperature=0`,
  - `prompt_version_hash`,
  - `input_hash`,
  - cache TTL 1h.
- Tail executor cycle:
  - open/mark/resolve/close,
  - market-level de-dup of OPEN positions,
  - provider-degraded breaker integration.
- Tail circuit breaker:
  - budget hard stop,
  - consecutive-loss cooldown,
  - API degraded block.
- Category caps:
  - crypto / natural_disaster / political_stability / sports_outcome / regulatory / zero_event.
- Stage17 report + batch + tracking + endpoints + celery schedules.
- Dryrun integration:
  - `run_simulation_cycle` повертає `tail` блок,
  - `refresh_mark_prices` / `check_resolutions` виконують Stage17 mark/resolve pass,
  - `/admin/dryrun/report` містить `tail_report`.
- Tail ledger schema:
  - `stage17_tail_positions`,
  - `stage17_tail_fills`,
  - `stage17_tail_reports`,
  - extra fields via idempotent follow-up migration.

## Міграції
- `0020_stage17_tail_ledger.py`
- `0021_stage17_tail_ledger_extra_fields.py`

## Тести
- Stage17 suites: `18 passed`
- Additional Stage17/TZ-name suites: `27 passed`
- Регрес (Stage10/11/15/17 + tail suites): `52 passed`

## Відомі операційні next steps (runtime)
- Apply Alembic migrations (`0020`, `0021`) у target environment.
- Увімкнути Stage17 flags у `.env` за rollout-планом.
- Run Stage17 API smoke (`/research/stage17/*`) після міграцій.
