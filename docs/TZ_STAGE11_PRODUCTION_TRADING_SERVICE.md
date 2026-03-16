# ТЗ Stage 11: Production Trading Service

## 1. Мета

Запустити керований production trading-сервіс на базі validated policy з Stage 10:
1. Спочатку `SHADOW` -> потім `LIMITED_EXECUTION` -> потім `FULL_EXECUTION`.
2. Мінімізувати execution-ризики через hard guardrails та circuit breakers.
3. Дати клієнту прозору аналітику: чому ставка, чому пропуск, очікуване vs реалізоване.

## 2. In Scope

1. Execution venue v1: Polymarket CLOB (wallet-based, USDC).
2. Сервіс ордерів: place/cancel/status/fills/reconcile.
3. Position sizing, exposure limits, kill-switch.
4. Multi-client tenant isolation.
5. Client reporting API (daily/weekly + per-trade explainability).
6. Incident handling + rollback playbook.

## 3. Out of Scope

1. Авто-експансія на всі біржі одразу.
2. Обхід регуляторних або platform-policy обмежень.

## 4. Custody model (обов'язкове рішення до старту)

Допустимі варіанти:
1. `CLIENT_SIGNED` (non-custodial): клієнт підписує ордери.
2. `MANAGED_HOT_WALLET`: виділений бот-гаманець з жорстким risk-cap.

Заборонено стартувати Stage 11 без затвердженого custody mode.

## 5. Multi-tenant architecture

1. Усі execution-сутності мають `client_id`.
2. Ізоляція лімітів та позицій тільки per-client.
3. Жодного shared order book state без tenant scope.

DB моделі (мінімум):
1. `clients`.
2. `client_wallets`.
3. `orders`.
4. `fills`.
5. `client_positions`.
6. `trading_audit_events`.

## 6. Архітектура

1. `decision_pipeline` (Stage 7/8/9) -> `execution_router` -> `venue_adapter`.
2. `order_manager`:
   - idempotent place,
   - retry with backoff,
   - fill reconciliation,
   - unknown-timeout recovery.
3. `risk_engine`:
   - pre-trade checks,
   - post-trade controls,
   - circuit breaker states.
4. `audit_store`:
   - trade intent,
   - sent order payload,
   - exchange response,
   - final fill summary.

## 7. Безпека

1. Приватні ключі/секрети тільки через secrets manager.
2. Жодного логування ключів.
3. Key rotation policy.
4. Least-privilege runtime.
5. Signed payload checksum в аудиті.

## 8. Risk Guardrails

Hard pre-trade blocks:
1. `data_sufficient_for_acceptance == false`.
2. `market_inactive_or_resolving_soon`.
3. `rules_ambiguity_score` вище hard-limit.
4. `expected_edge_after_costs < min_edge_threshold`.
5. `client_exposure_limit_exceeded`.
6. `provider_health_degraded`.

Circuit breakers (числові пороги):
1. `SOFT` якщо будь-що:
   - daily realized drawdown <= -1.5%,
   - consecutive losses >= 4.
2. `HARD` якщо будь-що:
   - daily realized drawdown <= -3.0%,
   - weekly drawdown <= -5.0%,
   - consecutive losses >= 7.
3. `PANIC` якщо будь-що:
   - daily realized drawdown <= -6.0%,
   - execution_error_rate_1h >= 10%,
   - reconciliation_gap_usd > configured cap.
4. `HARD/PANIC` reset тільки manual + audited reason.

## 9. Idempotency / timeout semantics

1. Client-side idempotency key: `client_id + signal_id + policy_version + side + size_bucket`.
2. Якщо place timeout:
   - статус `UNKNOWN_SUBMIT`,
   - poll order status/fills до `MAX_UNKNOWN_RECOVERY_SEC`,
   - за потреби safe-cancel.
3. Заборонений blind retry без reconciliation.

## 10. KPI / SLO

Execution SLO:
1. order placement success rate.
2. p95 execution latency.
3. reconciliation completeness.

Trading KPI:
1. realized post-cost return.
2. drawdown.
3. precision@K on executed trades.
4. slippage drift vs expected.

## 11. Acceptance Criteria (Stage 11)

1. `SHADOW` стабільний мінімум 14 днів без critical incidents.
2. `LIMITED_EXECUTION` мінімум 30 днів або мінімум 100 executed trades.
3. `execution_error_rate` нижче порогу.
4. Немає security/secrets incident.
5. `realized_post_cost_return` не нижче Stage 10 baseline у межах tolerance.
6. Є повний audit trail для 100% виконаних ордерів.

## 12. Deliverables

1. `docs/STAGE11_TRADING_RUNBOOK.md`.
2. `docs/STAGE11_RISK_AND_ROLLBACK.md`.
3. `docs/STAGE11_CLIENT_REPORT_SCHEMA.md`.
4. `artifacts/research/stage11_execution_<timestamp>.json`.
5. `artifacts/research/stage11_client_report_<timestamp>.csv`.

## 13. DB schema / migrations

1. Міграція: `0014_stage11_trading_core.py`.
2. Індекси:
   - `orders(client_id, created_at)`,
   - `fills(client_id, order_id)`,
   - `client_positions(client_id, market_id)`.

## 14. API

1. `GET /analytics/research/stage11/execution`.
2. `GET /analytics/research/stage11/risk`.
3. `GET /analytics/research/stage11/client-report`.
4. `POST /analytics/research/stage11/track`.

## 15. Режимний state machine

1. Перехід `SHADOW -> LIMITED` тільки через gate endpoint/manual approve.
2. Перехід `LIMITED -> FULL` тільки після acceptance.
3. Автовідкат у `SHADOW` при `PANIC` або policy breach.

## 16. Gate до Stage 12

1. Stage 11 acceptance виконано.
2. Risk engine стабільний на реальному execution.
3. Multi-tenant isolation пройшла audit.
