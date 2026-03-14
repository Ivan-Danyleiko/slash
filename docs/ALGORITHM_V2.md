# Algorithm v2.1

## 1) ARBITRAGE modes

1. `momentum`:
   - створюється тільки коли `recent_move >= SIGNAL_MODE_MOMENTUM_MIN_MOVE`.
2. `uncertainty_liquid`:
   - створюється при низькому move, якщо ринок близько до 0.5 і проходить liquidity/volume фільтри.
   - confidence обмежується `SIGNAL_MODE_UNCERTAINTY_MAX_SCORE`.

Додатково в metadata зберігаються `signal_mode`, `recent_move`, `distance_from_50`, `snapshot_age_hours`.

## 2) RULES_RISK modes

1. `explicit_rules_risk` — через rules keyword matching.
2. `missing_rules_risk` — fallback лише при strict liquidity/volume + keyword exclusion.
3. Daily cap на generation: `SIGNAL_RULES_MISSING_DAILY_CAP`.
4. Кандидати fallback сортуються за quality (confidence proxy), потім береться top-N.

## 3) Duplicate 2-stage + shadow

1. Stage 1 broad: relaxed thresholds для candidate recall.
2. Stage 2 strict: overlap/jaccard/anchor + geo/date/asset/entity constraints.
3. Drop reasons пишуться в `duplicate_pair_candidates`.
4. Shadow-порівняння режимів: `strict`, `balanced`, `aggressive` через endpoint `/analytics/duplicate-shadow`.

## 4) Скоринг

`score_total = 0.35*edge + 0.25*liquidity + 0.15*freshness + 0.20*confidence - 0.30*risk_penalties`

Для кожного нового сигналу зберігається:

1. `signal_mode`
2. `score_breakdown_json`
3. `execution_analysis`

## 5) Top-selection

1. V2 gating (`is_top_eligible`) фільтрує слабкі сигнали.
2. Контроль частки `missing_rules_risk` у топ-вікні.
3. Rollback-флаг:
   - `SIGNAL_TOP_USE_V2_SELECTION=false` повертає legacy відбір.
