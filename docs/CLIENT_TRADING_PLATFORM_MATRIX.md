# Client Trading Platform Matrix (UA Clients)

Цей документ фіксує практичну матрицю доступності платформ для клієнтів з українськими документами (у т.ч. при проживанні за межами України) і рекомендований порядок запуску trading-стеку.

## 1. Platform Matrix

| Платформа | Тип | Читання даних | API ключ | Торгівля | KYC | Доступ для UA-клієнтів (з Польщі/UK) | Реальні гроші |
|---|---|---|---|---|---|---|---|
| Polymarket Gamma | Prediction market | Публічне | Ні | Немає (data-only) | Ні | Так | Ні (data-only) |
| Polymarket CLOB | Prediction market | Публічне | Ні (для читання), trading creds через wallet | Crypto wallet (MetaMask/Rabby) | Немає | Так (non-US) | Так (USDC) |
| Manifold | Prediction market | Публічне | Опційно | Вбудований акаунт | Немає | Так | Ні (play money) |
| Metaculus | Forecasting | API token | Так | Немає | Немає | Так | Ні |
| Kalshi | Regulated DCM | Частково публічне | Для authenticated сценаріїв | Акаунт + KYC | Потрібен | Обмежено (US-centric policy) | Так (USD) |
| Smarkets | Exchange | API | Так | Акаунт + KYC | Потрібен | Залежить від compliance профілю | Так (GBP/EUR) |
| Betfair Exchange | Exchange | API | Так | Акаунт + KYC | Потрібен | Залежить від compliance профілю | Так (GBP/EUR) |
| Augur / Gnosis Omen | DEX | Публічне | Ні | Crypto wallet | Немає | Так | Так (DAI/ETH) |

## 2. Practical Conclusions

1. Для швидкого запуску без KYC-бар’єру:
   - `Polymarket CLOB` (реальні кошти, wallet-based),
   - `Manifold` (sandbox/play-money для dry-run логіки),
   - `Metaculus` (reference prior).
2. `Kalshi` не використовувати як blocking dependency для MVP-клієнтів з UA-документами:
   - тримати як optional connector з `KALSHI_ENABLED=false` до підтвердження доступу/комплаєнсу.
3. `Smarkets/Betfair` — окремий regulated-канал для Phase 2:
   - тільки після окремого KYC+compliance onboarding per client.

## 3. Recommended Rollout for Current Clients

1. Phase A (зараз):
   - Data + signal + policy: `Polymarket Gamma + Metaculus + Manifold`.
   - Trading execution: `Polymarket CLOB` (feature-flagged).
2. Phase B:
   - Додати regulated venue (`Smarkets` або `Betfair`) для диверсифікації execution.
3. Phase C:
   - Advanced multi-venue routing + execution optimizer.

## 4. Compliance-by-Design Rules

1. Жодної гарантії execution на всіх venue для будь-якого клієнта.
2. Перевірка доступності/дозволеності платформи виконується до запуску execution:
   - `execution_enabled_<platform>=true/false`.
3. Якщо venue недоступний:
   - автоматично `SHADOW_ONLY` або `DATA_ONLY`,
   - без hard-fail всього пайплайна.

## 5. Stage 10 Direction (Trading-first)

Наступне ТЗ (Stage 10) має бути орієнтоване на клієнтів, які вже готові до execution:

1. Client onboarding profile:
   - residency/compliance matrix,
   - enabled venues,
   - max exposure and risk profile.
2. Trading orchestration:
   - execution policy per venue,
   - fill quality + slippage logging,
   - failover routing.
3. Product layer:
   - dashboard (PnL, fills, edge decay),
   - client-visible reports/alerts,
   - transparent reason-codes for every trade/no-trade decision.
