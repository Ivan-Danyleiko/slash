# Autonomous Trade Bot — Research Context

> **Purpose:** Standalone document for deep research collaboration. No codebase access required.
> Contains all algorithms, data models, current metrics, known gaps, and concrete research questions.
> Goal: find and prioritize improvements that move the system from signal scanner → profitable autonomous trade bot.

---

## 0. System Purpose & Current State

**What it does now:**
- Monitors 4 prediction markets (Polymarket, Manifold, Metaculus, Kalshi) via REST APIs
- Detects 6 signal types (divergence, duplicate, rules risk, liquidity risk, weird market, arbitrage)
- Filters signals through LLM agent (Stage 7) + category policy (Stage 8)
- Simulates execution, runs historical backtesting (Stage 10)
- Has production order execution engine ready (Stage 11) — in SHADOW mode

**Stage 10 Backtesting: PASS** (as of 2026-03-16)
```json
{
  "final_decision": "PASS",
  "post_cost_ev_ci_low_80": 0.01865,
  "scenario_sweeps_positive": 18,
  "leakage_violations_count": 0,
  "core_category_positive_ev_candidates": 1,
  "walkforward_available": false
}
```

**Critical data gap:** 2,595 rows in signal_history, all with `signal_id=None` (pre-screening divergence candidates, not fully-processed pipeline signals). Zero rows with labeled `probability_after_6h`. EV is positive only because of edge-proxy model (not real PnL).

---

## 1. Data Inventory

### Tables and Row Counts (approximate, production DB)

| Table | ~Rows | Notes |
|-------|-------|-------|
| `markets` | ~15,000 | Active + resolved across 4 platforms |
| `market_snapshots` | ~200,000+ | Probability history per market, timestamped |
| `signal_history` | ~2,595 | All with `signal_id=None`, source_tag="local" |
| `signals` | ~500 | Generated pipeline signals |
| `duplicate_market_pairs` | ~3,000 | Detected cross-platform pairs |
| `stage7_agent_decisions` | ~0 | LLM agent not yet connected in prod |
| `stage8_decisions` | ~0 | Not yet connected in prod |
| `stage10_replay_rows` | ~2,595 | From last batch run |
| `stage11_orders` | ~0 | Shadow mode, no fills yet |

### Signal History Schema (critical table)

```sql
signal_history:
  id                      SERIAL PK
  signal_id               INT NULL        -- NULL for pre-screening rows
  signal_type             VARCHAR         -- DIVERGENCE, RULES_RISK, etc.
  timestamp               TIMESTAMPTZ     -- when signal was emitted
  platform                VARCHAR         -- polymarket, manifold, etc.
  source_tag              VARCHAR         -- "local", "manifold_bets_api", etc.
  market_id               INT FK
  probability_at_signal   FLOAT           -- p at emission time
  related_market_probability FLOAT NULL   -- p of counterpart (for DIVERGENCE)
  divergence              FLOAT NULL      -- |p_a - p_b|
  liquidity               FLOAT NULL
  volume_24h              FLOAT NULL
  signal_direction        VARCHAR NULL    -- YES / NO (often NULL in legacy rows)
  resolved_outcome        VARCHAR NULL    -- YES / NO / VOID
  resolved_success        BOOL NULL       -- legacy success flag
  resolved_probability    FLOAT NULL      -- final market probability
  probability_after_1h    FLOAT NULL      -- ← EMPTY (no labeling job yet)
  probability_after_6h    FLOAT NULL      -- ← EMPTY (main walkforward horizon)
  probability_after_24h   FLOAT NULL      -- ← EMPTY
  predicted_edge_after_costs_pct FLOAT NULL  -- from Stage 9 execution sim
  features_snapshot       JSONB NULL      -- full signal features at emission
```

### Market Schema (key fields)

```sql
markets:
  id, external_id, platform_id, title, description
  probability_yes         FLOAT           -- current mid-price
  best_bid_yes            FLOAT NULL      -- CLOB bid (Polymarket only)
  best_ask_yes            FLOAT NULL      -- CLOB ask (Polymarket only)
  spread_cents            FLOAT NULL
  volume_24h              FLOAT NULL
  liquidity_value         FLOAT NULL
  is_neg_risk             BOOL            -- Polymarket neg-risk flag
  resolution_time         TIMESTAMPTZ NULL
  category                VARCHAR NULL
  source_payload          JSONB           -- raw API response
    └── manifold_bets_history: [{probability, timestamp}, ...]
    └── metaculus_prediction_history: [{p, ts}, ...]
```

---

## 2. Core Algorithms (Full Code)

### 2.1 Duplicate Detection

```python
# Uses rapidfuzz (Levenshtein) for title similarity
from rapidfuzz.fuzz import ratio, token_set_ratio

class DuplicateDetector:
    # Noise words removed before comparison:
    NOISE_WORDS = {"will", "be", "the", "a", "an", "by", "before", ...}

    # Token aliases (normalizes semantically equivalent tokens):
    TOKEN_ALIASES = {
        "bitcoin": "btc", "ethereum": "eth", "solana": "sol",
        "trump": "donald_trump", "biden": "joe_biden",
        "republican": "gop", "us": "usa",
    }

    # Profiles:
    # strict:     token_set_ratio >= 90  (high precision)
    # balanced:   token_set_ratio >= 75  (default)
    # aggressive: token_set_ratio >= 60  (high recall)

    def score(self, title_a: str, title_b: str) -> float:
        # 1. Normalize: lowercase, remove punctuation, apply TOKEN_ALIASES
        # 2. Remove NOISE_WORDS
        # 3. Apply PHRASE_ALIASES (regex: "s&p 500" → "sp500")
        # 4. Compute token_set_ratio (handles word reordering)
        # 5. Boost: +5 if same GEO_WORDS, +5 if same ASSET_WORDS
        # 6. Penalty: -10 if different date tokens (months/years)
        return score  # [0, 100]
```

**Problem:** Purely lexical. Misses:
- "Will Bitcoin exceed $100k?" vs "BTC/USD above 100000 at year end?" → ~55 score (missed)
- Cross-language equivalents
- Paraphrase duplicates with no common tokens

### 2.2 Signal Ranking — Two Formulas

```python
# Legacy rank_score (default when V2 disabled):
def rank_score(signal):
    return (
        0.60 * signal.divergence_score
      + 0.30 * signal.liquidity_score
      - 0.20 * signal.rules_risk_score
      + 0.25 * signal.confidence_score
      + {ARBITRAGE_CANDIDATE: 0.10, DIVERGENCE: 0.08, WEIRD_MARKET: 0.04,
         RULES_RISK: 0.02, DUPLICATE_MARKET: 0.01}[signal.signal_type]
      + {"momentum": 0.08, "uncertainty_liquid": 0.01,
         "explicit_rules_risk": 0.02, "missing_rules_risk": -0.03}[signal.signal_mode]
    )

# V2 appendix_c_score (when SIGNAL_TOP_APPENDIX_C_ENABLED=true):
def appendix_c_score(signal, settings):
    # All weights configurable via env vars:
    return (
        settings.signal_rank_weight_edge         * edge             # default 0.35
      + settings.signal_rank_weight_liquidity    * liquidity        # default 0.25
      + settings.signal_rank_weight_exec_safety  * execution_safety # default 0.20
      + settings.signal_rank_weight_freshness    * freshness        # default 0.10
      + settings.signal_rank_weight_confidence   * confidence       # default 0.10
      - risk_penalties                                               # rules_risk_score
    )
```

**Problem:** Static weights have never been optimized on labeled return data. No personalization per signal type or category. No temporal decay on edge estimates.

### 2.3 Execution Simulation — V1 and V2

```python
# V1: No orderbook (active by default, SIGNAL_EXECUTION_MODEL=v1)
class ExecutionSimulator:
    ASSUMPTIONS_VERSION = "v1_naive_no_orderbook"

    def simulate(self, *, market, confidence_score, liquidity_score, recent_move, signal_type):
        prob = market.probability_yes or 0.5
        edge_core = abs(prob - 0.5) * 2.0        # 0 at midpoint, 1 at extremes
        move_boost = min(1.0, recent_move / 0.2)
        expected_edge = min(1.0,
            0.55 * edge_core + 0.25 * confidence + 0.20 * move_boost
        )
        slippage = min(0.05, 100.0 / max(volume_24h, 1.0) * 0.01)
        utility = (expected_edge - slippage) * (0.4 + 0.6 * liquidity) * time_penalty
        return {"expected_edge": expected_edge, "utility_score": utility, ...}

# V2: Empirical + Bayesian prior (SIGNAL_EXECUTION_MODEL=v2)
class ExecutionSimulatorV2:
    ASSUMPTIONS_VERSION = "v2_empirical_labeled_returns"

    def simulate(self, *, market, confidence_score, liquidity_score, signal_type):
        # Pull last N days of signal_history for same signal_type
        returns = self._empirical_returns(signal_type, market.id)

        # Empirical stats
        hit_rate = len(wins) / len(returns) if returns else 0.5
        avg_win = mean(wins) or 0.0
        avg_loss = mean(losses) or 0.0
        empirical_edge = hit_rate * avg_win - (1 - hit_rate) * avg_loss

        # Bayesian blend with category prior
        w = min(1.0, len(returns) / min_samples)  # weight: 0→1 as data grows
        prior = {
            "crypto": settings.signal_execution_v2_prior_crypto,    # e.g. 0.03
            "finance": settings.signal_execution_v2_prior_finance,   # e.g. 0.02
            "sports": settings.signal_execution_v2_prior_sports,     # e.g. 0.015
            "politics": settings.signal_execution_v2_prior_politics, # e.g. 0.025
        }[category]
        expected_edge = w * empirical_edge + (1 - w) * prior

        # Platform-specific costs:
        # Polymarket: fee(0.001 DCM) + spread(1%) + slippage + gas(2$) + bridge(0.5$)
        # Kalshi: taker_fee(coeff * p * (1-p)) + maker_fee + spread + slippage
        # Manifold: spread + slippage (no fee)
        costs = self._costs_pct(market, platform, volume_24h, liquidity_value)

        ev_after_costs = expected_edge - costs
        return {"predicted_edge_after_costs_pct": ev_after_costs, ...}
```

**Critical problem:** V2 currently falls back to prior 100% of the time because `probability_after_6h = NULL` for all rows → zero empirical data → `w = 0.0`.

### 2.4 Asymmetric Prediction Market Payoff

```python
# The correct payoff model for binary prediction markets:
def _prediction_market_return(row, *, won: bool) -> float:
    predicted_edge = row.get("predicted_edge_after_costs_pct") or 0.0
    if predicted_edge != 0.0:
        # Already net-of-cost pipeline edge: keep symmetric
        return predicted_edge if won else -abs(predicted_edge)

    signal_id = row.get("signal_id") or 0
    if signal_id <= 0:
        # Legacy pre-screening rows: use edge proxy
        edge = divergence * 0.5  # or abs(p - 0.5) * 0.5
        return edge if won else -abs(edge)

    # Full pipeline rows: asymmetric payoff
    p = clamp(probability_t, 0.01, 0.99)
    if direction == "YES":
        return (1.0 - p) if won else -p       # bought YES at p
    else:  # NO
        return p if won else -(1.0 - p)       # sold YES / bought NO at (1-p)

# Why this matters (key insight):
# YES signal at p=0.30, win_rate=40%:
#   Symmetric:  EV = 0.4*edge - 0.6*edge = -0.2*edge ≈ -1% (looks negative!)
#   Asymmetric: EV = 0.4*(0.70) - 0.6*(0.30) = 0.28 - 0.18 = +10% (correct!)
# Low-price YES signals have high leverage — symmetric model underestimates EV massively.
```

### 2.5 Direction Inference

```python
def _infer_signal_direction(history_row, signal) -> str | None:
    # 1. Explicit field (most reliable)
    if history_row.signal_direction in {"YES", "NO"}:
        return history_row.signal_direction

    signal_type = normalize(history_row.signal_type)

    # 2. RULES_RISK: always NO (ambiguous rules → bet against resolution)
    if signal_type == "RULES_RISK":
        return "NO"

    # 3. DIVERGENCE: buy underpriced side
    if signal_type == "DIVERGENCE":
        p0 = history_row.probability_at_signal
        related = history_row.related_market_probability
        if p0 is not None and related is not None:
            return "YES" if p0 < related else "NO"

    # 4. ARBITRAGE/DUPLICATE: buy cheaper side (closer to 0)
    if signal_type in {"ARBITRAGE_CANDIDATE", "DUPLICATE_MARKET"}:
        p0 = history_row.probability_at_signal
        if p0 is not None:
            return "YES" if p0 < 0.5 else "NO"

    return None  # unknown → excluded from EV calculations
```

### 2.6 Category Normalization

```python
def _normalize_core_category(raw: str, title: str) -> str:
    merged = (raw + " " + title).lower()

    if any(k in merged for k in (
        "btc", "eth", "sol", "xrp", "crypto", "token", "coin", "defi",
        "bitcoin", "ethereum", "solana", "binance", ...
    )):
        return "crypto"

    if any(k in merged for k in (
        "stock", "gdp", "cpi", "fed", "interest rate", "econom", "finance",
        "nasdaq", "s&p", "inflation", "treasury", "bond", ...
    )):
        return "finance"

    if any(k in merged for k in (
        "sport", "nba", "nfl", "soccer", "football", "match", "olympic",
        "premier league", "world cup", "super bowl", ...
    )):
        return "sports"

    if any(k in merged for k in (
        "elect", "president", "senate", "government", "politic", "policy",
        "vote", "ballot", "war", "nato", "ukraine", "trump", "biden",
        "ceasefire", "sanction", "treaty", ...
    )):
        return "politics"

    return "other"
```

**Problem:** String membership check — "bitcoin contains 'bit'" is fine here but heuristic. "Polymarket prediction market" falsely matches "market cap" for finance. Context-sensitive misclassification rate unknown.

### 2.7 Walk-Forward Backtesting

```python
def build_walkforward_report(db, *, days=90, horizon="6h",
    train_days=30, test_days=14, step_days=14, embargo_hours=24):

    cutoff = now() - timedelta(days=days)
    rows = db.query(SignalHistory).where(timestamp >= cutoff).all()

    # Group by signal_type
    by_type = defaultdict(list)
    for row in rows:
        ret = extract_return(row, horizon)  # None if no labeled data
        if ret is not None:
            by_type[signal_type].append((timestamp, ret))

    # Rolling windows
    window_start = cutoff + train_days + embargo_hours
    while window_start <= now() - test_days:
        train_rets = [r for ts, r in series if train_start <= ts < train_end]
        test_rets  = [r for ts, r in series if test_start  <= ts < test_end]

        # Bootstrap CI for each window
        ci_lo, ci_hi = bootstrap_ci(test_rets, n=500, seed=42)
        yield {"n": len(test_rets), "avg_return": mean(test_rets),
               "hit_rate": win_count/n, "ci_low": ci_lo, ...}

def extract_return(row, horizon) -> float | None:
    p0 = row.probability_at_signal
    p1 = getattr(row, f"probability_after_{horizon}")  # NULL in prod!
    if p0 is None or p1 is None:
        return None  # ← ALL rows return None currently
    raw = p1 - p0
    if row.signal_direction == "NO":
        return -raw  # direction-aware: NO signal wins when price drops
    return raw
```

**Current status:** `evaluated_windows = 0` because `probability_after_6h = NULL` for all rows. The walkforward gate passes vacuously (`None → True`).

### 2.8 Stage 11 Risk Engine

```python
@dataclass
class Stage11RiskInput:
    daily_drawdown_pct: float        # negative = loss
    weekly_drawdown_pct: float
    consecutive_losses: int
    execution_error_rate_1h: float   # failed orders / total orders
    reconciliation_gap_usd: float    # |expected_fill - actual_fill|

def resolve_circuit_breaker_level(v: Stage11RiskInput, ...) -> str:
    # PANIC: immediate halt, close all positions
    if (v.daily_drawdown_pct <= -6.0           # default
        or v.execution_error_rate_1h >= 0.10
        or v.reconciliation_gap_usd > 50.0):
        return "PANIC"

    # HARD: pause all new orders until next day
    if (v.daily_drawdown_pct <= -3.0
        or v.weekly_drawdown_pct <= -5.0
        or v.consecutive_losses >= 7):
        return "HARD"

    # SOFT: reduce position sizes
    if v.daily_drawdown_pct <= -1.5 or v.consecutive_losses >= 4:
        return "SOFT"

    return "OK"
```

### 2.9 Bootstrap CI (used in Stage 10)

```python
def _bootstrap_ci_80(values: list[float], n_sims=500, seed=42) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(max(100, min(n_sims, 5000))):
        sample = [values[rng.randrange(0, n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.10 * (len(means) - 1))]  # 10th percentile
    hi = means[int(0.90 * (len(means) - 1))]  # 90th percentile
    return (lo, hi)
# Gate: CI_low_80 > 0 required for Stage 10 PASS
```

### 2.10 Scenario Sweeps (3×3×2 = 18 combinations)

```python
cost_multipliers = [1.0, 1.5, 2.0]
edge_discounts   = [0.0, 0.1, 0.2]
direction_modes  = ["all", "high_conf_only"]  # high_conf = confidence > 0.65

for cost_mult, edge_disc, dir_mode in product(cost_multipliers, edge_discounts, direction_modes):
    # Filter rows
    rows_to_use = resolved_rows  # fallback when no KEEP decisions
    if dir_mode == "high_conf_only":
        rows_to_use = [r for r in rows_to_use if confidence > 0.65]

    # Compute returns using symmetric edge-proxy (NOT asymmetric — stress test)
    returns = []
    for row in rows_to_use:
        edge = _row_effective_edge(row) * (1 - edge_disc)
        effective_edge = edge - costs_pct * cost_mult
        returns.append(effective_edge if won else -abs(effective_edge))

    portfolio_return = sum(returns)
    positive_scenarios += 1 if portfolio_return > 0 else 0

# Gate: positive_scenarios >= 12/18
# Note: sweeps use symmetric edge-proxy (not asymmetric), intentionally.
# Purpose: test policy robustness under cost uncertainty, not PnL simulation.
```

---

## 3. Signal Pipeline Flow (Detailed)

```
[Celery job: sync_all_platforms_job]
  → Collector.fetch_markets() for each platform
  → Normalize → Insert/Update markets table
  → Create MarketSnapshot for each probability

[Celery job: analyze_markets_job]
  → SignalEngine.run()
    ├── DuplicateDetector.scan(all_markets)
    │     → For each cross-platform pair:
    │         score = token_set_ratio(normalize(title_a), normalize(title_b))
    │         if score >= threshold: create DuplicateMarketPair
    │
    ├── DivergenceAnalyzer.scan(duplicate_pairs)
    │     → For each pair: divergence = |p_a - p_b|
    │         if divergence >= SIGNAL_DIVERGENCE_THRESHOLD:
    │             create SignalHistory(
    │                 signal_type=DIVERGENCE,
    │                 probability_at_signal=p_a,
    │                 related_market_probability=p_b,
    │                 signal_id=None,  ← pre-screening row
    │                 source_tag="local"
    │             )
    │
    ├── RulesRiskAnalyzer.scan(markets)
    │     → Keyword scan of rules_text / description
    │         keywords: "at discretion", "may be voided", "if applicable", ...
    │         output: risk_score [0,1] + mode label
    │
    └── SignalEngine.generate_signals()
          → Score each candidate (rank_score or appendix_c_score)
          → Filter by threshold
          → Deduplicate: skip if same market has signal in last 24h
          → Create Signal row

[Stage 7 Agent (LLM)]
  → input_hash = hash(signal_features)
  → Check cache (Stage7AgentDecision by input_hash)
  → If not cached:
      internal_gate checks (confidence, liquidity, budget)
      llm_call(signal_context) → {decision: KEEP|SKIP|ESCALATE, confidence, reasoning}
      external_verifier(decision) → consensus check
  → Create Stage7AgentDecision

[Stage 8 Policy]
  → CategoryClassifier(market.title, market.description) → category
  → PolicyProfile.lookup(category) → {max_size, min_confidence, allowed_types, blocks}
  → DecisionGate.evaluate(signal, policy) → PASS|SOFT_BLOCK|HARD_BLOCK
  → Create Stage8Decision + Stage8Position (simulated)

[Stage 9 Execution]
  → ExecutionSimulatorV1 or V2
  → Compute: predicted_edge_after_costs_pct, utility_score
  → Update Signal.execution_analysis JSON

[Stage 10 Replay (batch, nightly)]
  → Pull SignalHistory rows
  → Resolve timeline (MarketSnapshot > manifold_bets > metaculus > local fallback)
  → Leakage check (forbidden features, embargo violation)
  → Infer direction, compute return (asymmetric payoff)
  → Aggregate: bootstrap CI, category breakdown, scenario sweeps

[Stage 11 Order Execution]
  → Check Stage11Client.mode: SHADOW|LIMITED|FULL
  → risk_engine.check() → OK|SOFT|HARD|PANIC
  → idempotency_key = hash(signal_id, venue, direction, price_bucket)
  → If SHADOW: log decision, no order
  → If LIMITED/FULL: execution_router → venue_adapter → CLOB order
  → Order state machine: PENDING → SUBMITTED → FILLED
```

---

## 4. Stage 11: A/B Test Framework Proposal

The following A/B tests are proposed for Stage 11 production validation. Each has defined metrics, sample sizes, and success criteria.

### Test A: Flat vs Kelly Position Sizing

**Hypothesis:** Fractional Kelly sizing (sized by edge magnitude) improves Sharpe ratio vs flat $50 USD positions.

**Design:**
```
Control:   flat size = STAGE11_POSITION_SIZE_USD (e.g. $50)
Treatment: size = bankroll * 0.25 * (predicted_edge / variance)
           cap at min($200, max($10, Kelly_size))
           variance = std(last_30_day_returns_per_type) or 0.10 default

Assignment: alternate by signal_id % 2 (even = control, odd = treatment)
Min sample: 200 fills per arm
Primary metric: Sharpe ratio (annualized) over rolling 30d
Secondary: total PnL, max drawdown, hit rate
Stop criteria: >$500 adverse drawdown difference between arms
```

**Expected outcome:** Treatment improves Sharpe by 15-30%. Kelly reduces exposure on uncertain signals and increases on confident ones.

---

### Test B: V1 vs V2 Execution Model

**Hypothesis:** Empirical Bayesian execution model (V2) filters more false positives once labeled data accumulates.

**Design:**
```
Prerequisite: ≥500 rows with probability_after_6h labeled
Control:   SIGNAL_EXECUTION_MODEL=v1 (naive edge_core heuristic)
Treatment: SIGNAL_EXECUTION_MODEL=v2 (empirical + Bayesian prior)

Gate: Only emit signals where predicted_edge_after_costs > 0.02
Measurement: precision = (signals with positive 6h return) / (total signals)
             recall = (positive opportunities detected) / (total positive opportunities)
Min sample: 100 resolved signals per arm
Primary metric: precision@top_10_signals (per week)
Secondary: EV per signal, fill rate
```

**Expected outcome:** V2 shows higher precision once n_empirical > min_samples. V1 may have higher recall early (less filtering).

---

### Test C: Single Model vs Ensemble LLM Agent (Stage 7)

**Hypothesis:** 3-model voting ensemble (e.g. GPT-4o + Claude + Gemini) improves decision accuracy vs single model.

**Design:**
```
Control:   Single LLM call (current implementation)
Treatment: 3 independent calls, majority vote on KEEP/SKIP
           If 2-1 split: use voter confidence to resolve ties
           Log all 3 decisions + costs

Assignment: by market_id hash (ensures same market always in same arm)
Min sample: 500 decisions per arm
Primary metric: KEEP accuracy = P(profitable | KEEP decision)
               measured at 6h horizon
Secondary: cost per correct KEEP, decision latency, monthly cost

Cost constraint: treatment max 3x control cost
```

**Expected outcome:** Ensemble reduces false positive KEEPs by 10-20% at cost of 2-3x LLM spend. Acceptable if hit rate improvement is sufficient.

---

### Test D: Static Weights vs Learned Ranker

**Hypothesis:** LightGBM ranker trained on historical (features → 6h return) outperforms static Appendix C weights.

**Design:**
```
Prerequisite: ≥1,000 labeled signal rows
Control:   appendix_c_score with fixed weights
Treatment: LightGBM ranker trained weekly on last 180d

Features: [divergence_score, liquidity_score, confidence,
           platform_a, platform_b, category, days_to_resolution,
           time_of_day, market_age_days, rules_risk_score,
           hit_rate_type_30d, spread_pct, volume_bucket]
Label: float(probability_after_6h - probability_at_signal) * direction_sign

Training: every Sunday, k-fold CV on last 180d, select by val NDCG@10
Assignment: week alternates between control/treatment

Primary metric: NDCG@10 (normalized discounted cumulative gain at top 10 signals)
Secondary: top-10 hit rate, realized PnL of top-10 per week
```

**Expected outcome:** LightGBM shows 15-25% NDCG improvement after 3 weeks of deployment. Better at capturing non-linear interactions (e.g. low-liquidity + high-divergence works differently per category).

---

### Test E: Current Divergence vs Executable Divergence Gate

**Hypothesis:** Using net executable divergence (adjusted for bid-ask spread) reduces false positive DIVERGENCE signals.

**Design:**
```
Current gate: emit if |p_a - p_b| > SIGNAL_DIVERGENCE_THRESHOLD (e.g. 0.05)

Proposed gate:
  for YES direction (p_a < p_b, buy on A):
    executable_divergence = p_b_bid - p_a_ask  # net after 1-way spread
  for NO direction (p_a > p_b, sell on A):
    executable_divergence = p_a_bid - p_b_ask
  emit only if executable_divergence > 0.02

Constraint: requires best_bid_yes, best_ask_yes on Market
            (currently populated for Polymarket only)

Assignment: alternate by signal_id % 2
Primary metric: fill rate (orders that actually fill at signal price)
               slippage = (fill_price - signal_price)
Secondary: signals per day count, PnL per trade

Note: Polymarket-only initially (others lack CLOB data)
```

**Expected outcome:** 30-50% reduction in signals emitted; better fill quality; higher hit rate per signal.

---

## 5. Priority Research Questions

### Q1: What is the true edge after accounting for bid-ask spread?

Current divergence calculation ignores that both sides of the trade have spreads.
For a 5% divergence between platforms:
- Platform A: prob 0.30, but ask = 0.32 (buy side)
- Platform B: prob 0.35, but bid = 0.33 (sell side)
- Real executable divergence = 0.33 - 0.32 = 0.01 (not 0.05)

**Research task:** Analyze all DIVERGENCE signals in `signal_history` where we have `best_bid_yes` and `best_ask_yes` stored in `market.source_payload`. Compute:
```python
spread_adjusted_divergence = p_b_bid - p_a_ask  # for YES direction
net_edge_after_costs = spread_adjusted_divergence - gas_fee/position_size - bridge_fee/position_size
```
What fraction of historical DIVERGENCE signals have `net_edge_after_costs > 0`?

---

### Q2: What is the realized probability drift at 1h/6h/24h after signal emission?

We have `market_snapshots` with timestamps and `signal_history` with emission timestamps. We can reconstruct `probability_after_Nh` from snapshots without calling APIs.

**Research task:** For each row in `signal_history`:
1. Find the MarketSnapshot with `fetched_at` closest to `timestamp + 1h` (and +6h, +24h)
2. Compute `drift = snapshot.probability_yes - signal_history.probability_at_signal`
3. Apply direction sign: `return = drift if direction=="YES" else -drift`
4. Aggregate by signal_type, category, platform_pair

What is the mean drift and hit_rate by signal_type? This would fill the `probability_after_6h` gap and unlock V2 execution model + real walkforward.

---

### Q3: Are the category priors in V2 execution model calibrated?

Current V2 priors (when no empirical data):
- crypto: `signal_execution_v2_prior_crypto` (default likely ~0.02-0.03)
- finance: `signal_execution_v2_prior_finance`
- sports: `signal_execution_v2_prior_sports`
- politics: `signal_execution_v2_prior_politics`

These are uncalibrated guesses. With snapshot-reconstructed returns (from Q2):

**Research task:** Compute empirical mean return per category using snapshot-reconstructed drifts. Compare to configured priors. How miscalibrated are they? What should the correct priors be for each category × signal_type combination?

---

### Q4: What is the LLM agent's decision accuracy in shadow mode?

Stage 7 agent decisions are logged to `stage7_agent_decisions`. In shadow mode, decisions are made but not used for execution.

**Research task:** For each `Stage7AgentDecision` where `decision=KEEP`:
1. Find the corresponding market outcome (resolved_outcome or probability drift)
2. Was the KEEP decision correct (did the market move in predicted direction)?
3. Compute precision = correct_KEEPs / total_KEEPs
4. Compare: do KEEP decisions with higher `confidence` have higher accuracy?
5. Calibration: does `confidence=0.8` correspond to 80% accuracy in realized outcomes?

---

### Q5: What is the optimal embargo period to prevent lookahead bias?

Current: `embargo_seconds = 3600` (1 hour) between signal emission and data used.

**Research task:** Run Stage 10 replay with different embargo values: 0h, 1h, 3h, 6h, 12h.
Track how `leakage_violations_count` and `post_cost_ev_ci_low_80` change.
Is there a minimum embargo after which EV estimates stabilize? A short embargo might show inflated EV (leakage). A long embargo might discard valid signals.

---

### Q6: What percentage of DIVERGENCE signals self-correct within 1h?

If a divergence signal corrects within 1 hour, we may not be able to execute at the signal price before the opportunity disappears.

**Research task:** For DIVERGENCE rows in `signal_history`:
1. Find snapshot at `timestamp + 15min`, `+30min`, `+1h`
2. Compute: at what fraction do `|p_a(t+1h) - p_b(t+1h)| < threshold` (i.e., converged)?
3. Average time to convergence
4. Does convergence speed correlate with divergence magnitude? Platform pair?

This determines the required execution latency for the bot to be viable.

---

## 6. Specific Improvement Implementations

### I1: Snapshot-Based Labeling Job

**Priority: CRITICAL** — unlocks V2 execution model, real walkforward, LLM calibration.

```python
# New Celery task: label_signal_history_from_snapshots()
# Run nightly

def label_signal_history_from_snapshots(db: Session):
    unlabeled = db.query(SignalHistory).where(
        SignalHistory.probability_after_6h.is_(None),
        SignalHistory.market_id.is_not(None),
    ).all()

    for row in unlabeled:
        market_id = row.market_id
        t0 = row.timestamp

        for hours, field in [(1, "probability_after_1h"),
                              (6, "probability_after_6h"),
                              (24, "probability_after_24h")]:
            target_ts = t0 + timedelta(hours=hours)

            # Find closest snapshot AFTER target_ts
            snap = db.query(MarketSnapshot).where(
                MarketSnapshot.market_id == market_id,
                MarketSnapshot.fetched_at >= target_ts,
                MarketSnapshot.fetched_at <= target_ts + timedelta(hours=2),
            ).order_by(MarketSnapshot.fetched_at.asc()).first()

            if snap and snap.probability_yes is not None:
                setattr(row, field, float(snap.probability_yes))

    db.commit()
```

**Effect:** After running, V2 execution model gets real empirical data; walkforward shows real return distribution; LLM calibration becomes possible.

---

### I2: Executable Divergence Computation

**Priority: HIGH** — prevents trading against the spread

```python
# In divergence analyzer, compute net executable divergence:

def compute_executable_divergence(market_a: Market, market_b: Market) -> dict:
    p_a = market_a.probability_yes
    p_b = market_b.probability_yes
    gross_divergence = abs(p_a - p_b)

    # Polymarket has CLOB data
    if market_a has CLOB:
        ask_a = market_a.best_ask_yes or (p_a + 0.01)
        bid_a = market_a.best_bid_yes or (p_a - 0.01)
    else:
        spread_a = market_a.spread_cents / 100.0 if market_a.spread_cents else 0.02
        ask_a = p_a + spread_a / 2
        bid_a = p_a - spread_a / 2

    # For YES direction (buy A, effectively sell B)
    if p_a < p_b:  # YES direction: buy cheap A
        executable = p_b - ask_a  # what B will pay minus what A costs
    else:  # NO direction: buy cheap B
        executable = p_a - ask_b

    return {
        "gross_divergence": gross_divergence,
        "executable_divergence": max(0.0, executable),
        "has_clob_data": market_a has CLOB or market_b has CLOB,
    }
```

---

### I3: pgvector Semantic Duplicate Detection

**Priority: MEDIUM** — improves duplicate detection coverage significantly

```sql
-- Add to migration:
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE markets ADD COLUMN title_embedding vector(1536);
CREATE INDEX ON markets USING ivfflat (title_embedding vector_cosine_ops);
```

```python
# In market collector, after inserting market:
from openai import OpenAI
client = OpenAI()

def embed_market_title(title: str) -> list[float]:
    response = client.embeddings.create(
        input=title,
        model="text-embedding-3-small"  # 1536 dims, ~$0.02/1M tokens
    )
    return response.data[0].embedding

# Nightly job: embed all markets without embeddings
# Duplicate detection:
SELECT m1.id, m2.id, 1 - (m1.title_embedding <=> m2.title_embedding) AS cosine_sim
FROM markets m1, markets m2
WHERE m1.platform_id != m2.platform_id
  AND 1 - (m1.title_embedding <=> m2.title_embedding) > 0.92
  AND m1.id < m2.id;
```

**Cost estimate:** ~15,000 markets × 50 tokens avg = 750,000 tokens = $0.015 one-time. Incremental: negligible.

---

### I4: Fractional Kelly Position Sizing

**Priority: HIGH** — once labeled returns are available

```python
# In Stage 11 order placement:

def kelly_position_size(
    predicted_edge: float,          # from execution simulation
    empirical_variance: float,      # std² of returns for this signal_type
    bankroll_usd: float,            # total allocated capital
    fraction: float = 0.25,         # fractional Kelly (conservative)
    min_size_usd: float = 10.0,
    max_size_usd: float = 500.0,
) -> float:
    if empirical_variance <= 0 or predicted_edge <= 0:
        return min_size_usd

    kelly_full = predicted_edge / empirical_variance
    kelly_fraction = fraction * kelly_full
    size_usd = bankroll_usd * kelly_fraction
    return max(min_size_usd, min(max_size_usd, size_usd))

# Where to get empirical_variance:
# = std([probability_after_6h - probability_at_signal for rows
#        with same signal_type, last 30d, direction-adjusted])
```

---

### I5: LLM Context Enhancement

**Priority: MEDIUM** — improves Stage 7 decision quality

```python
# Current prompt context (simplified):
context = {
    "title": market.title,
    "current_probability": market.probability_yes,
    "divergence_score": signal.divergence_score,
    "rules_text": market.description[:500],
}

# Enhanced prompt context:
def build_rich_context(signal, market, db):
    # 1. Probability trajectory (last 7 days of snapshots)
    snapshots = db.query(MarketSnapshot).where(
        MarketSnapshot.market_id == market.id,
        MarketSnapshot.fetched_at >= now() - timedelta(days=7)
    ).order_by(fetched_at.asc()).all()
    trajectory = [(snap.fetched_at, snap.probability_yes) for snap in snapshots]

    # 2. Similar past signals and their outcomes
    similar = db.query(SignalHistory).where(
        SignalHistory.signal_type == signal.signal_type,
        SignalHistory.resolved_outcome.is_not(None),
    ).order_by(timestamp.desc()).limit(5).all()

    return {
        "market": {"title": ..., "probability": ..., "rules": ...},
        "signal": {"type": ..., "divergence": ..., "direction": ...},
        "price_trajectory_7d": trajectory,  # key addition
        "similar_past_signals": [
            {"divergence": r.divergence, "outcome": r.resolved_outcome,
             "days_ago": (now() - r.timestamp).days}
            for r in similar
        ],  # key addition
        "platform_pair": f"{platform_a}/{platform_b}",
        "days_to_resolution": days_to_resolution,
    }
```

---

## 7. Current Test Coverage

Tests pass (as of last run):

```
tests/
├── test_stage10_foundation.py      4 passed  ← replay engine, leakage, direction
├── test_stage11_risk_engine.py     5 passed  ← circuit breakers, thresholds
├── test_stage11_idempotency.py     3 passed  ← dedup, TTL
├── test_stage11_state_machine.py   4 passed  ← order lifecycle
├── test_stage10_replay.py          (integration)
├── test_stage5_*.py                13 tests
├── test_stage6_*.py                7 tests
├── test_stage9_*.py                5 tests
└── test_analyzers_*.py             various

No tests yet for:
- Snapshot-based labeling (I1) — new code needed
- Executable divergence (I2) — new code needed
- Embedding-based duplicate (I3) — new code needed
- Kelly sizing (I4) — new code needed
```

---

## 8. Configuration Reference (Key Variables)

```bash
# Signal thresholds
SIGNAL_DIVERGENCE_THRESHOLD=0.05
SIGNAL_TOP_USE_V2_SELECTION=true
SIGNAL_TOP_APPENDIX_C_ENABLED=true

# Ranking weights (V2)
SIGNAL_RANK_WEIGHT_EDGE=0.35
SIGNAL_RANK_WEIGHT_LIQUIDITY=0.25
SIGNAL_RANK_WEIGHT_EXECUTION_SAFETY=0.20
SIGNAL_RANK_WEIGHT_FRESHNESS=0.10
SIGNAL_RANK_WEIGHT_CONFIDENCE=0.10

# Execution model
SIGNAL_EXECUTION_MODEL=v1            # v1|v2 (v2 needs labeled data)
SIGNAL_EXECUTION_V2_LOOKBACK_DAYS=90
SIGNAL_EXECUTION_V2_MIN_SAMPLES=30
SIGNAL_EXECUTION_POSITION_SIZE_USD=50

# Category priors (V2 fallback when no empirical data)
SIGNAL_EXECUTION_V2_PRIOR_CRYPTO=0.025
SIGNAL_EXECUTION_V2_PRIOR_FINANCE=0.020
SIGNAL_EXECUTION_V2_PRIOR_SPORTS=0.015
SIGNAL_EXECUTION_V2_PRIOR_POLITICS=0.025
SIGNAL_EXECUTION_V2_PRIOR_DEFAULT=0.020

# Polymarket costs
SIGNAL_EXECUTION_POLYMARKET_GAS_FEE_USD=2.0
SIGNAL_EXECUTION_POLYMARKET_BRIDGE_FEE_USD=0.50
SIGNAL_EXECUTION_POLYMARKET_FEE_MODE=zero         # zero|dcm10bps

# Stage 7 LLM agent
STAGE7_AGENT_PROVIDER=openai_compatible
STAGE7_AGENT_SHADOW_ENABLED=true
STAGE7_AGENT_REAL_CALLS_ENABLED=false             # ← false in prod currently
STAGE7_AGENT_MONTHLY_BUDGET_USD=20.0
STAGE7_OPENAI_MODEL=gpt-4o-mini

# Stage 11 trading
STAGE11_VENUE=polymarket
STAGE11_VENUE_DRY_RUN=true                        # ← dry run currently
STAGE11_SOFT_DAILY_DRAWDOWN_PCT=-1.5
STAGE11_HARD_DAILY_DRAWDOWN_PCT=-3.0
STAGE11_PANIC_DAILY_DRAWDOWN_PCT=-6.0
STAGE11_MIN_SHADOW_DAYS=30
STAGE11_LIMITED_MIN_DAYS=14
STAGE11_LIMITED_MIN_TRADES=50
```

---

## 9. Research Priorities (Ranked by Expected Impact)

| Priority | Task | Prerequisite | Expected Gain |
|----------|------|--------------|---------------|
| **P1** | Labeling job: fill `probability_after_6h` from snapshots | None | Unlocks P2-P5 |
| **P2** | Switch to V2 execution model | P1 | Better signal filtering |
| **P3** | Executable divergence gate | Polymarket CLOB | -40% false positives |
| **P4** | Fractional Kelly sizing | P1 | +20-40% Sharpe |
| **P5** | LightGBM ranker | P1 + 1000 rows | +15-25% precision |
| **P6** | Semantic embeddings for duplicates | pgvector setup | +2-5x duplicate coverage |
| **P7** | LLM context enrichment (trajectory) | None | +10-15% decision accuracy |
| **P8** | Multi-venue routing (Kalshi adapter) | None | +0.5-2% better fills |
| **P9** | LLM fine-tuning on signal decisions | P1 + 1000 labeled | -90% LLM costs |
| **P10** | Real-time news event trigger | External API | First-mover edge |

---

## 10. Open Architectural Questions for Research

1. **How fast do divergences actually correct?** If median correction time is < 1 hour, the Celery polling architecture (likely 5-15 min intervals) misses the window. Should move to WebSocket streaming.

2. **Is DIVERGENCE the primary source of edge, or is it RULES_RISK?** Rules risk signals have longer time horizons (days, not hours). They may have higher hit rates but lower frequency. Need comparative analysis.

3. **Platform calibration differences**: Polymarket (CLOB, real money) vs Manifold (play money) likely have different calibration properties. A Manifold probability of 0.30 may systematically overestimate vs Polymarket's 0.25 for the same event. Should the divergence threshold differ by platform pair?

4. **Neg-risk market mechanics**: Polymarket neg-risk markets have different settlement (multiple outcomes, not binary). The asymmetric payoff model `(1-p)` vs `-p` is wrong for neg-risk markets — they have multi-outcome payoffs. All neg-risk signals should be handled separately or excluded from the standard payoff model.

5. **Portfolio correlation**: Two crypto signals on the same day are likely correlated (both move with BTC). Current system doesn't track this. Kelly sizing without correlation adjustment can lead to over-concentration. Need correlation matrix between signal outcomes by category.

6. **Resolution time distribution**: What fraction of tracked markets actually resolve? Many Metaculus questions never resolve. RULES_RISK signals on questions that never resolve have undefined outcomes — they should be excluded from EV calculations or handled as "voided."

---

*Document generated: 2026-03-16. Current system status: Stage 10 PASS, Stage 11 SHADOW (dry run). Primary bottleneck: zero labeled probability_after_Nh data.*
