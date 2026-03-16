# Stage 11 Trading Runbook

## Runtime Modes

1. `SHADOW`: order intents only, no real execution.
2. `LIMITED_EXECUTION`: controlled execution mode after manual approval.
3. `FULL_EXECUTION`: full mode after acceptance criteria.
4. Transition endpoint: `POST /analytics/research/stage11/runtime-mode`
   - `SHADOW -> LIMITED_EXECUTION` requires Stage11 limited gate pass.
   - `LIMITED_EXECUTION -> FULL_EXECUTION` requires full acceptance pass.

## Daily Operator Checklist

1. Check `GET /health`.
2. Check `GET /analytics/research/stage11/risk`.
3. Check `GET /analytics/research/stage11/execution`.
4. Check `GET /analytics/research/stage11/client-report`.
5. Trigger `POST /analytics/research/stage11/track` and store artifact.
6. Verify `checks` block:
   - `shadow_stable_14d`
   - `reconciliation_completeness_ge_95pct`
   - `execution_error_rate_below_threshold`
7. If `unknown_submit_open > 0`, run `POST /analytics/research/stage11/reconcile`.
8. For manual ops:
   - `GET /analytics/research/stage11/orders/{order_id}`
   - `POST /analytics/research/stage11/orders/{order_id}/refresh-status`
   - `POST /analytics/research/stage11/orders/{order_id}/cancel`
9. Readiness checks:
   - `GET /analytics/research/stage11/tenant-isolation`
   - `GET /analytics/research/stage11/final-readiness`
   - `POST /analytics/research/stage11/final-readiness/track`

## Unknown Submit Recovery

1. Orders with timeout move to `UNKNOWN_SUBMIT`.
2. Recovery window is bounded by `STAGE11_MAX_UNKNOWN_RECOVERY_SEC`.
3. After window expires, system performs safe-cancel semantics (`CANCELLED_SAFE`) and logs audit event.
4. Scheduler runs reconcile every 10 minutes (`stage11_reconcile` beat task).

## Venue Adapter Modes

1. `STAGE11_VENUE=POLYMARKET_CLOB` (Stage 11 v1 supported execution venue).
2. `STAGE11_VENUE_DRY_RUN=true`:
   - synthetic venue ids,
   - safe for integration validation,
   - no real market placement.
3. `STAGE11_VENUE_DRY_RUN=false`:
   - live adapter mode,
   - requires valid CLOB credentials and operational readiness checks.
