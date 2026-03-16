# Stage 11 Risk and Rollback

## Circuit Breaker Thresholds

1. `SOFT`:
   - daily drawdown <= `-1.5%`, or
   - consecutive losses >= `4`.
2. `HARD`:
   - daily drawdown <= `-3.0%`, or
   - weekly drawdown <= `-5.0%`, or
   - consecutive losses >= `7`.
3. `PANIC`:
   - daily drawdown <= `-6.0%`, or
   - execution error rate (1h) >= `10%`, or
   - reconciliation gap > configured USD cap.

## Rollback Policy

1. `PANIC` => automatic rollback to `SHADOW`.
2. `HARD` => no promotion, manual review required.
3. `HARD/PANIC` reset must be manual and audited.

## Audit Requirements

1. Every pre-trade block has reason codes.
2. Every order intent has checksumed audit event.
3. Every rollback action has explicit event payload.

