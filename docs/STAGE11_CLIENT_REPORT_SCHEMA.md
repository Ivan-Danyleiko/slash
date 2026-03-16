# Stage 11 Client Report Schema

## Endpoint

`GET /analytics/research/stage11/client-report`

## JSON fields

Top-level:
1. `generated_at` (ISO timestamp)
2. `window_days` (int)
3. `summary` (object)
4. `rows` (list)
5. `trade_details` (list; per-trade explainability)

`summary`:
1. `clients`
2. `orders_total`
3. `fills_total`
4. `realized_pnl_usd_total`
5. `trade_details_total`

`rows[]`:
1. `client_id`
2. `client_code`
3. `runtime_mode`
4. `orders`
5. `fills`
6. `realized_pnl_usd`

`trade_details[]`:
1. `client_id`
2. `client_code`
3. `order_id`
4. `signal_id`
5. `market_id`
6. `status`
7. `side`
8. `size_bucket`
9. `notional_usd`
10. `requested_price`
11. `last_error`
12. `event_types` (ordered audit events)
13. `audit_event_count`
14. `created_at`

## CSV export

`GET /analytics/research/stage11/client-report?as_csv=true`

Columns:
`client_id,client_code,runtime_mode,orders,fills,realized_pnl_usd`
