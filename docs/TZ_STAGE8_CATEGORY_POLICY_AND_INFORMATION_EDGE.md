# ТЗ Stage 8: Category Policy, Rules-Field Verification, and Execution Readiness

## 1. Контекст

Stage 5-7 побудували research/shadow контур, але бізнес-ризик залишається:
1. у prediction markets edge залежить не тільки від математики;
2. ключові фактори: `категорія ринку + resolution rules + зовнішня консистентність + execution costs`;
3. реальне виконання без формального pre-execution proof неприпустиме.

Ціль Stage 8: додати category-aware decision layer, який споживає Stage 7 результати і дає фінальний verdict:
`EXECUTE_ALLOWED / SHADOW_ONLY / BLOCK`.

## 2. Scope

### 2.1 In Scope

1. Category classifier з 5 канонічними категоріями.
2. Category policy profiles з конкретними numeric thresholds.
3. Rules-field verifier:
   - `rules_ambiguity_score`,
   - `resolution_source_confidence`,
   - `dispute_risk_flag`.
4. Stage 8 decision gate з Hard/Soft/Warning логікою.
5. Shadow execution ledger (what-if PnL) + acceptance метрики.

### 2.2 Out of Scope

1. Повна заміна Stage 7 verification.
2. Неконтрольовані live news/social ingestion.
3. Auto-trading без Stage 8 acceptance.

## 3. Stage 8 dependency model (критично)

Stage 8 НЕ дублює Stage 7 API fetches.
Pipeline:

```text
Signal
 -> Stage7 decision + evidence_bundle (existing)
 -> Stage8 category classifier
 -> Stage8 internal gate v2 (category-aware)
 -> Stage8 rules gate
 -> Stage8 external context router (consumes Stage7 evidence_bundle)
 -> Stage8 decision gate
 -> stage8_decisions + stage8_shadow_ledger
```

## 4. Category Classifier Specification

## 4.1 Канонічні категорії

1. `crypto`
2. `finance`
3. `sports`
4. `politics`
5. `other`

## 4.2 Алгоритм (обов'язковий)

1. Якщо `Market.category` не порожній:
   - нормалізувати через mapping table у канонічну категорію.
2. Якщо `Market.category` порожній:
   - keyword matching по `title + description`.
3. Рахувати `category_confidence` (0..1):
   - exact taxonomy match = 0.95,
   - strong keyword cluster = 0.75-0.90,
   - weak keyword cluster = 0.60-0.74,
   - інакше `other` з confidence < 0.60.
4. Якщо `category_confidence < 0.60`:
   - примусово `other`,
   - reason code: `category_low_confidence_fallback`.

## 4.3 Multi-category handling

Якщо сигнал потрапляє одразу в кілька категорій:
1. вибрати категорію з найбільшим confidence;
2. зберегти secondary categories у `evidence_bundle.secondary_categories`;
3. policy застосовується лише для primary category.

## 5. Category Policy Profiles (numeric defaults)

Реалізувати 2 профілі:
1. `bootstrap_v1` (даних ще мало, більш м'який).
2. `production_v1` (строгий, для limited/go rollout).

### 5.1 bootstrap_v1

Поля профілю (всі обов'язкові):
- `min_edge_after_costs` — мінімальний post-cost edge для KEEP
- `min_liquidity_usd` — мінімальна ліквідність ринку
- `max_spread_cents` — максимальний bid-ask spread у центах
- `max_rules_ambiguity_score` — hard block threshold для ambiguity score
- `max_cross_platform_contradiction` — hard block threshold для cross-platform spread
- `min_ttr_hours` — мінімум годин до resolution (time-to-resolution)
- `min_freshness_minutes` — max вік останнього market update

```python
CATEGORY_POLICY_BOOTSTRAP_V1 = {
    "crypto": {
        "min_edge_after_costs": 0.030,
        "min_liquidity_usd": 500,
        "max_spread_cents": 5,
        "max_rules_ambiguity_score": 0.30,
        "max_cross_platform_contradiction": 0.20,
        "min_ttr_hours": 2,
        "min_freshness_minutes": 30,
    },
    "finance": {
        "min_edge_after_costs": 0.025,
        "min_liquidity_usd": 1000,
        "max_spread_cents": 6,
        "max_rules_ambiguity_score": 0.20,
        "max_cross_platform_contradiction": 0.15,
        "min_ttr_hours": 4,
        "min_freshness_minutes": 60,
    },
    "sports": {
        "min_edge_after_costs": 0.020,
        "min_liquidity_usd": 2000,
        "max_spread_cents": 6,
        "max_rules_ambiguity_score": 0.10,
        "max_cross_platform_contradiction": 0.15,
        "min_ttr_hours": 1,
        "min_freshness_minutes": 15,
    },
    "politics": {
        "min_edge_after_costs": 0.040,
        "min_liquidity_usd": 3000,
        "max_spread_cents": 8,
        "max_rules_ambiguity_score": 0.25,
        "max_cross_platform_contradiction": 0.25,
        "min_ttr_hours": 24,
        "min_freshness_minutes": 60,
    },
    "other": {
        "min_edge_after_costs": 0.050,
        "min_liquidity_usd": 500,
        "max_spread_cents": 10,
        "max_rules_ambiguity_score": 0.20,
        "max_cross_platform_contradiction": 0.20,
        "min_ttr_hours": 6,
        "min_freshness_minutes": 120,
    },
}
```

### 5.2 production_v1 (strict)

`production_v1` наслідує всі поля `bootstrap_v1` і перевизначає stricter параметри.
Тобто якщо поле не задане в `production_v1`, використовується значення з `bootstrap_v1`.

```python
CATEGORY_POLICY_PRODUCTION_V1 = {
    "crypto": {
        "min_edge_after_costs": 0.035,
        "min_liquidity_usd": 10000,
        "max_rules_ambiguity_score": 0.25,
        "max_cross_platform_contradiction": 0.15,
        "max_spread_cents": 3,
        "min_ttr_hours": 2,
    },
    "politics": {
        "min_edge_after_costs": 0.045,
        "min_liquidity_usd": 50000,
        "max_rules_ambiguity_score": 0.20,
        "max_cross_platform_contradiction": 0.20,
        "max_spread_cents": 5,
        "min_ttr_hours": 24,
    },
    "sports": {
        "min_edge_after_costs": 0.025,
        "min_liquidity_usd": 5000,
        "max_rules_ambiguity_score": 0.08,
        "max_cross_platform_contradiction": 0.12,
        "max_spread_cents": 4,
        "min_ttr_hours": 2,
    },
    "finance": {
        "min_edge_after_costs": 0.030,
        "min_liquidity_usd": 10000,
        "max_rules_ambiguity_score": 0.15,
        "max_cross_platform_contradiction": 0.12,
        "max_spread_cents": 3,
        "min_ttr_hours": 4,
    },
    "other": {
        "min_edge_after_costs": 0.055,
        "min_liquidity_usd": 1000,
        "max_rules_ambiguity_score": 0.18,
        "max_cross_platform_contradiction": 0.18,
        "max_spread_cents": 8,
        "min_ttr_hours": 6,
    },
}
```

## 6. Rules-Field Verifier

## 6.1 Weighted ambiguity score (обов'язково)

```python
AMBIGUITY_WEIGHTS = {
    "sole discretion": 0.50,
    "at our discretion": 0.50,
    "final determination": 0.45,
    "editorial decision": 0.45,
    "team decision": 0.40,
    "subjective": 0.35,
    "if deemed": 0.30,
    "consensus": 0.25,
    "may be resolved by": 0.25,
    "if unavailable": 0.20,
    "if applicable": 0.15,
    "in the event of": 0.10,
}

def compute_rules_ambiguity_score(rules_text: str) -> float:
    t = (rules_text or "").lower()
    score = sum(w for token, w in AMBIGUITY_WEIGHTS.items() if token in t)
    # Penalty: no explicit resolution source cited.
    if not any(s in t for s in ["coinmarketcap", "coingecko", "reuters", "ap ", "official"]):
        score += 0.20
    # Penalty: time-bounded condition without explicit timezone.
    if any(word in t for word in ["by ", "before ", "at "]) and not any(tz in t for tz in ["utc", "est", "gmt"]):
        score += 0.10
    return min(1.0, score)
```

## 6.2 Resolution source confidence

```python
PLATFORM_RESOLUTION_CONFIDENCE = {
    "POLYMARKET": 0.85,
    "MANIFOLD": 0.60,
    "METACULUS": 0.75,
}
```

## 6.3 Dispute risk flag

`dispute_risk_flag = true`, якщо:
1. `rules_ambiguity_score >= category.max_rules_ambiguity_score`, або
2. `(platform == "MANIFOLD" and resolution_source_confidence < 0.70)`.

Опційно (warning-only, не hard requirement): якщо в payload знайдено platform-specific dispute markers, додати reason code `platform_dispute_marker_detected`.

## 7. Edge after costs and sizing

## 7.1 Мінімальна формула post-cost edge

```python
edge_after_costs = predicted_edge - platform_fee_rate - spread_cost_estimate - slippage_estimate - lockup_penalty
```

Початкові defaults:
1. `platform_fee_rate = 0.02` для Polymarket.
2. `spread_cost_estimate = 0.5 * (ask - bid)` або proxy із liquidity model.
3. `slippage_estimate` з execution model.
4. `lockup_penalty` category/platform-aware (малий, але не нуль).

## 7.2 Kelly для binary outcomes (shadow only)

```python
kelly_fraction = edge / (P * (1 - P))
```

де:
1. `edge = predicted_prob - market_price`
2. `P = market_price`

Обмеження:
1. використовувати capped Kelly: `kelly_fraction = min(max(kelly, 0.0), 0.25)` (cap = 0.25 = 25% bankroll max),
2. тільки у shadow ledger до Stage 8 GO.

## 7.3 Dynamic limits near resolution

```python
if ttr_hours > 168: limit = base_limit
elif ttr_hours > 24: limit = base_limit * 0.70
elif ttr_hours > 6:  limit = base_limit * 0.40
elif ttr_hours > 1:  limit = base_limit * 0.20
else:                limit = 0.0
```

## 8. Decision mapping (обов'язково)

## 8.1 Stage 8 output contract

```json
{
  "decision": "KEEP|MODIFY|REMOVE|SKIP",
  "execution_action": "EXECUTE_ALLOWED|SHADOW_ONLY|BLOCK",
  "reason_codes": [],
  "hard_block_reason": null,
  "evidence_bundle": {}
}
```

## 8.2 Mapping rules

1. `decision=SKIP` -> `execution_action=BLOCK`
2. `decision=REMOVE` -> `execution_action=BLOCK`
3. `decision=MODIFY` -> `execution_action=SHADOW_ONLY`
4. `decision=KEEP`:
   - Hard gate fail -> `BLOCK`
   - Soft gate fail -> `SHADOW_ONLY`
   - тільки якщо hard+soft pass -> `EXECUTE_ALLOWED`

## 8.3 Hard / Soft / Warning gates

Hard gates (будь-який fail -> BLOCK):
1. `market_active`
2. `liquidity_minimum`
3. `spread_check`
4. `ambiguity_score_hard_limit`
5. `dispute_risk_flag_clear` — `dispute_risk_flag` must be False
6. `resolution_source_confidence_min` — `resolution_source_confidence >= 0.65`
7. `position_limit`
8. `resolution_date_valid`
9. `platform_health`
10. `duplicate_position_check`
11. `data_sufficient_for_acceptance` — Stage 7 shadow flag must be True

Soft gates (fail -> SHADOW_ONLY):
1. `cross_platform_consensus`
2. `staleness_check`
3. `correlation_budget`
4. `event_proximity`
5. `signal_age`

Warning gates (fail -> log only):
1. `ambiguity_score_moderate`
2. `no_metaculus_equivalent`
3. `market_new_creator`
4. `thin_crowd`

## 9. Data models (mandatory schema)

## 9.1 stage8_decisions table

```python
class Stage8Decision(Base):
    __tablename__ = "stage8_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    stage7_decision_id: Mapped[int | None] = mapped_column(ForeignKey("stage7_agent_decisions.id"))
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    category_confidence: Mapped[float | None] = mapped_column(Float)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_ambiguity_score: Mapped[float | None] = mapped_column(Float)
    resolution_source_confidence: Mapped[float | None] = mapped_column(Float)
    dispute_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edge_after_costs: Mapped[float | None] = mapped_column(Float)
    base_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSON)
    hard_block_reason: Mapped[str | None] = mapped_column(String(256))
    evidence_bundle: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

## 9.2 stage8_positions table (exposure tracking)

Потрібна окрема таблиця для:
1. `open_exposure_per_event`
2. `open_exposure_per_category`
3. `position_limit checks`

Без цього Section 8 hard gate `position_limit` технічно неперевіряємий.

Мінімальна схема:

```python
class Stage8Position(Base):
    __tablename__ = "stage8_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    stage8_decision_id: Mapped[int | None] = mapped_column(ForeignKey("stage8_decisions.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)  # YES/NO
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN/CLOSED/CANCELED
    notional_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float)
    exposure_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Рекомендовані індекси:
1. `(status, category)`
2. `(status, event_key)`
3. `(market_id, status)`
4. `(opened_at)`

## 10. Required Metrics (обов'язкові output)

Всі метрики обчислюються в `stage8_batch.py` і `stage8_final_report.py`.

### Per category:

| Метрика | Опис |
|---|---|
| `edge_after_costs_mean` | Середній post-cost edge для KEEP рішень |
| `edge_after_costs_ci_low_80` | 80% bootstrap CI нижня межа (має бути > 0 для GO) |
| `brier_score` | Середньоквадратична помилка прогнозів (менше = краще) |
| `precision_at_keep` | Частка KEEP рішень що завершились успішно |
| `false_keep_rate` | Частка KEEP рішень що завершились провалом |
| `rules_ambiguity_block_rate` | Частка сигналів заблокованих через ambiguity |
| `cross_platform_contradiction_rate` | Частка сигналів з contradiction soft/hard fail |
| `executable_signals_per_day` | Кількість EXECUTE_ALLOWED per day (не SHADOW_ONLY) |
| `na_resolution_rate` | Частка N/A outcomes у shadow ledger |

### Global:

| Метрика | Опис |
|---|---|
| `limited_go_readiness` | Частка категорій що виконали LIMITED_GO умови (0.0–1.0) |
| `coverage_stage8_decisions` | Частка сигналів з Stage 8 рішенням |
| `agent_reason_stability` | Стабільність reason_codes між запусками (аналог Stage 7) |
| `cost_per_decision` | USD витрат на одне Stage 8 рішення |
| `rollback_trigger_count` | Кількість спрацювань rollback triggers за period |
| `walkforward_negative_window_share` | Частка walkforward windows з від'ємним edge |
| `scenario_sweeps_positive` | Кількість позитивних сценаріїв з 18 (з Stage 7 sweeps) |

## 11. Acceptance Criteria (revised)

Stage 8 вважається виконаним лише якщо:
1. Stage 7 data sufficiency precondition виконана:
   - `resolved_rows_total >= 30`
   - `keeps_with_resolution >= 10`
   - `walkforward_windows_total >= 3`
2. Coverage:
   - overall `coverage_stage8_decisions >= 90%`
   - per-category coverage `>= 70%` для core categories.
3. Profitability proof:
   - `bootstrap_ci_low_80 > 0`
   - `walkforward_negative_window_share <= 0.30`
   - `scenario_sweeps_positive >= 12/18`
4. Baseline definition фіксована:
   - `baseline_precision = Stage 7 precision@KEEP` на тому ж періоді та категорії.
5. Є мінімум 1 `LIMITED_GO` у множині `{crypto, finance, sports}`.
   - `other` не зараховується як primary success category.

## 12. Timeline (realistic)

1. Phase A (2-3 тижні):
   - schema + classifier + policy profiles + rules verifier + tests.
2. Phase B (2-3 тижні):
   - Stage 7 integration + decision gate + shadow ledger + tests.
3. Phase C (4-6 тижнів):
   - validation (залежить від live data accumulation).
4. Phase D (1-2 тижні):
   - canary rollout + rollback validation + final report.

Орієнтир total: `9-14 тижнів`.

## 12.1 Rollback triggers (обов'язково)

Rollback запускається, якщо будь-що з цього виконується:
1. `edge_after_costs_ci_low_80 <= 0` протягом 3 послідовних daily runs.
2. `false_keep_rate > 0.35` протягом 7 днів.
3. `rules_ambiguity_block_rate` падає нижче очікуваного профілю (ознака пропуску rules-risk) протягом 3 днів.
4. `cross_platform_contradiction_rate > max_contradiction + 0.10` протягом 3 днів.
5. `platform_health_hard_fail_count >= 3` за 24 години.

Rollback action:
1. `execution_action => SHADOW_ONLY` для всіх категорій.
2. freeze останнього policy version.
3. автоматичний incident report у `artifacts/research/stage8_rollback_<timestamp>.md`.

## 13. Implementation structure

```text
app/services/agent_stage8/
  __init__.py
  category_classifier.py
  category_policy_profiles.py
  rules_field_verifier.py
  internal_gate_v2.py
  external_context_router.py
  decision_gate.py
  store.py

app/services/research/
  stage8_shadow_ledger.py
  stage8_batch.py
  stage8_final_report.py
```

Інтеграції:
1. `app/models/models.py` (+ Stage8Decision, Stage8Position).
2. `app/core/config.py` (+ stage8_* settings).
3. `app/api/routes/analytics.py` (+ stage8 endpoints).
4. `scripts/stage8_track_batch.py`.

## 14. Final DoD

1. Усі критичні специфікації формалізовані:
   - classifier,
   - thresholds,
   - ambiguity algorithm,
   - decision mapping,
   - schema.
2. Stage 8 споживає Stage 7 evidence без дублювання зовнішніх fetch.
3. Є доказовий шлях до LIMITED_GO без обхідних рішень і без black-box override.
