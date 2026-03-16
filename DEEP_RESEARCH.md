# Deep Research Brief: Prediction Market Trade Bot

> **Purpose of this document:** Map the entire system as it exists today — algorithms, agents, models, data sources, libraries — and identify where substantial improvements can be made on the path to a production-grade autonomous trade bot.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Data Sources & Collection](#2-data-sources--collection)
3. [Signal Generation Pipeline](#3-signal-generation-pipeline)
4. [Algorithms In Use](#4-algorithms-in-use)
5. [Agent Architecture](#5-agent-architecture)
6. [Execution & Risk Engine](#6-execution--risk-engine)
7. [Backtesting & Validation (Stage 10)](#7-backtesting--validation-stage-10)
8. [Production Trading (Stage 11)](#8-production-trading-stage-11)
9. [Libraries & Dependencies](#9-libraries--dependencies)
10. [Database Schema](#10-database-schema)
11. [Infrastructure](#11-infrastructure)
12. [Current Performance Metrics](#12-current-performance-metrics)
13. [Known Weaknesses & Improvement Opportunities](#13-known-weaknesses--improvement-opportunities)
14. [Research Directions for Substantial Gains](#14-research-directions-for-substantial-gains)

---

## 1. System Overview

The system is a **multi-stage, event-driven prediction market scanner** that evolves toward a fully autonomous trade bot. It monitors 4 prediction platforms in real time, detects mispricings and information asymmetries, scores them, filters through an LLM decision agent, and executes orders with a risk-managed execution engine.

### Stage Map

```
[Stage 1-4]  Data collection + Telegram product (signal alerts, watchlist, digest)
     ↓
[Stage 5]    Signal quality research (backfill, ranking, ethics, A/B testing)
     ↓
[Stage 6]    Agent decision research (walk-forward backtesting, governance)
     ↓
[Stage 7]    LLM agent development (stack evaluation, shadow mode, cost budgeting)
     ↓
[Stage 8]    Category classification & policy (crypto/finance/sports/politics gates)
     ↓
[Stage 9]    Source quality validation & execution simulation
     ↓
[Stage 10]   Event Replay Engine (historical backtesting, leakage detection) ← PASS ✅
     ↓
[Stage 11]   Production trading (SHADOW → LIMITED → FULL modes)
     ↓
[Stage 12]   Multi-venue expansion (cross-venue arbitrage, routing optimizer)
```

### Core Thesis

Prediction markets are **systematically mispriced** at signal emission time because:
- Cross-platform information propagation is slow (hours, not seconds)
- Rules text ambiguity is not priced in
- Liquidity providers don't fully account for platform-specific volatility
- Duplicated markets on different platforms create exploitable divergences

The bot's edge comes from detecting these mispricings before they correct, then trading the direction.

---

## 2. Data Sources & Collection

### Platforms

| Platform | API Type | Data Retrieved | Update Frequency |
|----------|----------|---------------|-----------------|
| **Polymarket** | Gamma REST API + CLOB API | Markets, probabilities, bid/ask, volume, neg-risk flag | Celery job |
| **Manifold Markets** | REST API | Markets, probabilities, bet history | Celery job |
| **Metaculus** | REST API | Questions, community predictions, resolution criteria | Celery job |
| **Kalshi** | Elections REST API | Markets, probabilities | Celery job |

### Collected Fields per Market

```python
Market:
  - external_id, title, description, platform_id
  - probability_yes               # current mid-price
  - best_bid_yes, best_ask_yes    # CLOB orderbook (Polymarket only)
  - spread_cents                  # bid-ask spread
  - volume_24h, liquidity_value
  - resolution_time, close_time
  - is_neg_risk                   # Polymarket neg-risk market flag
  - source_payload                # raw JSON (contains bet history, prediction history)
  - category, subcategory
```

### MarketSnapshot

Every collected probability is snapshotted with timestamp → enables timeline reconstruction for replay without lookahead.

---

## 3. Signal Generation Pipeline

```
Collectors → Markets DB
                ↓
         SignalEngine.run()
         ├── detect_duplicates()     → DuplicateMarketPair rows
         ├── analyze_rules()         → RulesAnalysis rows
         ├── detect_divergence()     → divergence candidates + SignalHistory rows
         ├── analyze_liquidity()     → LiquidityAnalysis rows
         └── generate_signals()      → Signal rows (24h cooldown dedup)
                ↓
         Stage 7 Agent (LLM decision)
                ↓
         Stage 8 Policy (category gate)
                ↓
         Stage 9 Execution simulation
                ↓
         Stage 10 Replay audit
                ↓
         Stage 11 Order placement
```

### Signal Types (enum: `SignalType`)

| Type | Description | Edge Source |
|------|-------------|-------------|
| `DIVERGENCE` | Cross-platform probability gap | Price convergence |
| `ARBITRAGE_CANDIDATE` | Risk-free or near-risk-free spread | Spread capture |
| `DUPLICATE_MARKET` | Same event on multiple platforms | Synthetic arbitrage |
| `RULES_RISK` | Ambiguous resolution criteria | Volatility premium |
| `LIQUIDITY_RISK` | Thin market warning | Execution risk flag |
| `WEIRD_MARKET` | Malformed probability (>0.95 or <0.05) | Correction to fair value |
| `WATCHLIST` | User watchlist addition | N/A (product feature) |

---

## 4. Algorithms In Use

### 4.1 Duplicate Detection

**File:** `app/services/analyzers/duplicate.py`

Algorithm:
1. Tokenize market titles (stop-word removal, lemmatization)
2. **Fuzzy title matching** via `rapidfuzz` (Levenshtein, token-set ratio)
3. Profiles: `strict` (≥90 token similarity), `balanced` (≥75), `aggressive` (≥60)
4. Multi-stage evaluation: keyword overlap → date alignment → subject matching
5. Output: similarity score [0,1] + profile label per pair

Weakness: Purely lexical — misses semantic duplicates ("Will Trump win?" vs "Will the Republican candidate win the 2024 election?")

### 4.2 Divergence Detection

**File:** `app/services/analyzers/divergence.py`

Algorithm:
1. For each cross-platform duplicate pair: `divergence = |p_platform_A - p_platform_B|`
2. Configurable threshold (default: `SIGNAL_DIVERGENCE_THRESHOLD`)
3. Direction: YES if `p_A < p_B` (buy on A, sell on B) or vice versa

Weakness: Raw probability diff ignores calibration differences between platforms, bid-ask spread, and liquidity depth.

### 4.3 Signal Ranking

**File:** `app/services/signals/ranking.py`

Two scoring functions:

**Legacy `rank_score`:**
```
score = 0.60 * divergence
      + 0.30 * liquidity
      - 0.20 * rules_risk
      + 0.25 * confidence
      + type_bonus (ARBITRAGE: +0.10, DIVERGENCE: +0.08, WEIRD: +0.04)
      + mode_bonus (momentum: +0.08, explicit_rules_risk: +0.02)
```

**V2 `appendix_c_score`** (configurable weights):
```
score = w_edge * edge
      + w_liquidity * liquidity
      + w_execution_safety * execution_safety
      + w_freshness * freshness
      + w_confidence * confidence
      - risk_penalties
```
Default weights: `edge=0.35, liquidity=0.25, execution_safety=0.20, freshness=0.10, confidence=0.10`

**V2 Selection Gate** (`is_top_eligible`):
- Minimum score threshold
- Minimum utility score for ARBITRAGE signals
- Minimum confidence + liquidity for RULES_RISK
- Max share of `missing_rules_risk` signals in top-N (prevents quality dilution)

### 4.4 Execution Simulation

**File:** `app/services/signals/execution.py`

**V1 (naive, no orderbook):**
```python
edge_core = abs(prob - 0.5) * 2.0
expected_edge = 0.55*edge_core + 0.25*conf + 0.20*move_boost
slippage = min(0.05, 100/max(volume,1) * 0.01)
utility = slippage_adjusted_edge * (0.4 + 0.6*liquidity) * time_penalty
```

**V2 (empirical, labeled returns):**
- Pull signal history for same signal type from last N days
- Compute empirical: `hit_rate`, `avg_win`, `avg_loss` → `expected_edge_empirical`
- Blend with Bayesian prior: `edge = w*empirical + (1-w)*prior`
  - Prior by category: crypto / finance / sports / politics / other
  - Weight `w = min(1.0, n_samples / min_samples)`
- Platform-specific costs:
  - Polymarket: `fee + spread + slippage + gas_fee + bridge_fee`
  - Kalshi: `taker_fee(p*(1-p)) + maker_fee + spread + slippage`
  - Manifold: `spread + slippage` (no fee)
- Final: `predicted_edge_after_costs = expected_edge - costs_pct`

### 4.5 Rules Risk Scoring

**File:** `app/services/analyzers/rules_risk.py`

Keyword-based NLP heuristic:
- Scans `rules_text` / `description` fields
- Categories: explicit_risk (referee discretion, admin decision), missing_rules (no text), ambiguous_outcome
- Output: risk score [0,1] + mode label

### 4.6 Walk-Forward Validation

**File:** `app/services/research/walkforward.py`

Rolling-window out-of-sample validation:
```
[--- train_days ---][embargo_hours][--- test_days ---]
                    step_days →
```
- Extract returns: `p_after_horizon - p_at_signal` (direction-aware: NO signals invert sign)
- Per-window metrics: `avg_return`, `hit_rate`, `bootstrap_CI(95%)`
- Status: `LOW_CONFIDENCE` if `n < min_samples_per_window`
- Aggregation: share of negative-return test windows

### 4.7 Bootstrap Confidence Intervals

**File:** `app/services/research/stage10_replay.py`

```python
def _bootstrap_ci_80(values, n_sims=500, seed=42):
    # Resample with replacement, compute mean distribution
    # Return (10th percentile, 90th percentile) → 80% CI
```
Used for: post-cost EV CI (main PASS gate), per-category EV CI

### 4.8 Scenario Sweeps

3×3×2 = 18 parameter combinations over:
- Cost multiplier: `[1.0, 1.5, 2.0]`
- Edge discount: `[0.0, 0.1, 0.2]`
- Direction filter: `["all", "high_conf_only"]`

For each scenario: compute portfolio return using symmetric edge-proxy. Gate: ≥12/18 scenarios positive.

### 4.9 Asymmetric Prediction Market Payoff Model

**File:** `app/services/research/stage10_replay.py` → `_prediction_market_return`

Correct model for prediction markets (replaces symmetric ±edge):
```python
# YES signal at price p:
#   WIN:  +( 1 - p )   ← bought YES, resolves YES
#   LOSS: -p            ← bought YES, resolves NO

# NO signal at price p:
#   WIN:  +p             ← bought NO (= sold YES), resolves NO
#   LOSS: -(1 - p)       ← bought NO, resolves YES
```
Why this matters: symmetric model gives `EV = -1%` for `YES@0.30, 40% win rate`; asymmetric model gives `EV = +10%`. Prediction markets at low prices have high leverage — symmetric ±edge systematically underestimates edge.

---

## 5. Agent Architecture

### Stage 7: LLM Decision Agent

**Files:** `app/services/agent_stage7/`

Multi-layer decision pipeline:
```
Signal → internal_gate → LLM call → external_verifier → Stage7AgentDecision
```

**Internal Gate** (`internal_gate.py`):
- Pre-filters: confidence threshold, liquidity threshold, rules risk cap
- Cost guard: monthly budget check (`STAGE7_AGENT_MONTHLY_BUDGET_USD`)
- Shadow mode: real call but decision not used for execution

**Stack Adapters** (pluggable LLM backends):

| Adapter | Description |
|---------|-------------|
| `LangGraphAdapter` | LangGraph stateful agent with tool calls |
| `OpenAICompatibleAdapter` | OpenAI API (or any OpenAI-compatible endpoint) |
| `PlainApiAdapter` | Raw HTTP POST to any JSON API |

**Decision Output Schema:**
```python
Stage7AgentDecision:
  - signal_id, input_hash
  - decision: KEEP | SKIP | ESCALATE
  - confidence: float [0,1]
  - reasoning: str
  - llm_model, llm_cost_usd
  - shadow_mode: bool
```

**Tools available to LLM:**
- Market lookup, historical signal lookup, platform metadata
- Rules text extraction
- Category tagging

### Stage 8: Category Policy Agent

**Files:** `app/services/agent_stage8/`

```
Stage7Decision → CategoryClassifier → PolicyProfile → DecisionGate → Stage8Decision
```

**Category Classifier** (`category_classifier.py`):
- Keywords + rules text → {crypto, finance, sports, politics, other}
- Fallback: market title keyword scan

**Policy Profiles** (`category_policy_profiles.py`):
- Per-category: max position size, min confidence, allowed signal types, hard blocks
- Examples: politics → reduced size on election eve; sports → block live-market signals

**Decision Gates** (`decision_gate.py` + `internal_gate_v2.py`):
- Soft block: flag + reduce position
- Hard block: SKIP with reason code
- Rules field verifier: check resolution criteria present and parseable

**Output:**
```python
Stage8Decision:
  - category, subcategory
  - policy_profile_id
  - gate_outcome: PASS | SOFT_BLOCK | HARD_BLOCK
  - recommended_position_size_usd
  - position: Stage8Position (simulated trade record)
```

---

## 6. Execution & Risk Engine

### Stage 11: Trading Service

**Files:** `app/services/stage11/`

**Operating Modes:**
```
SHADOW  → All decisions computed, no real orders (min 30 days required)
LIMITED → Real orders, reduced size, capped daily exposure
FULL    → Full autonomous operation
```

Progression gate (`readiness.py`):
- SHADOW → LIMITED: `min_shadow_days`, `min_shadow_signals`
- LIMITED → FULL: `min_limited_days`, `min_limited_trades`, `max_fill_failure_rate`

**Order Lifecycle** (`state_machine.py`):
```
PENDING → SUBMITTED → FILLED | PARTIAL_FILL | CANCELLED | FAILED
```

**Idempotency** (`idempotency.py`):
- Hash: `(signal_id, venue, direction, price_bucket)` → deduplicate retries
- 24h TTL on idempotency keys

**Risk Engine** (`risk_engine.py`):
```
Circuit breaker levels:
  SOFT  → reduce position size (configurable threshold)
  HARD  → pause new orders until next day
  PANIC → close all positions, halt trading
```
Triggers:
- Daily drawdown: `SOFT_PCT`, `HARD_PCT`, `PANIC_PCT`
- Fill failure rate exceeds threshold
- Venue connectivity loss

**Venue Adapters** (`venues/`):

| Venue | Adapter | API |
|-------|---------|-----|
| Polymarket | `polymarket_clob_adapter.py` | CLOB REST + `py-clob-client` SDK |
| Others | _planned_ | — |

**Polymarket CLOB specifics:**
- Limit orders on CLOB orderbook
- Neg-risk market handling (different settlement mechanics)
- Gas fee and bridge fee cost modeling
- USDC collateral management

---

## 7. Backtesting & Validation (Stage 10)

### Event Replay Engine

**Files:** `app/services/research/stage10_replay.py`

Full historical replay on `SignalHistory` rows:
1. Pull rows from DB (up to `limit`, over last `days`)
2. For each row: reconstruct probability at signal time (anti-lookahead via timeline sources)
3. Check leakage: `forbidden_features`, `embargo_seconds` violation → flag `leakage_violation`
4. Infer signal direction (YES/NO) from signal type + probability context
5. Determine win/loss based on `resolved_outcome == signal_direction`
6. Compute return via asymmetric payoff model
7. Aggregate: bootstrap CI (80%), scenario sweeps (18 scenarios), per-category breakdown

**Timeline Source Priority** (`stage10_timeline_sources.py`):
```
1. MarketSnapshot (most reliable — no API dependency)
2. manifold_bets_history (from source_payload)
3. metaculus_prediction_history (from source_payload)
4. SignalHistory.probability_at_signal (fallback, sufficient=True for local rows)
5. None → data_insufficient_timeline
```

**PASS Criteria** (all must be True):
```
events_total >= 100
leakage_violations_count == 0
data_insufficient_timeline_share <= 20%
post_cost_ev_ci_low_80 > 0          ← 80% CI lower bound positive
core_category_positive_ev_candidates >= 1
scenario_sweeps_positive >= 12/18
reason_code_stability >= 90%
walkforward_negative_window_share <= 30%   (vacuous pass if no labeled data)
core_categories_each_ge_20              ← each core category has >= 20 rows
module_security_pass_count >= 1
llm_mode != hard_cutoff
```

**Current State: PASS ✅** (as of 2026-03-16)
- `post_cost_ev_ci_low_80: 0.01865`
- `scenario_sweeps_positive: 18/18`
- `leakage_violations_count: 0`
- `walkforward_available: False` (no labeled `probability_after_6h` data yet)

---

## 8. Production Trading (Stage 11)

### Current Status

Stage 11 is implemented and functional. Live execution depends on:
- `STAGE11_VENUE` = polymarket | kalshi | manifold
- `STAGE11_VENUE_DRY_RUN` = true/false
- Client readiness (`Stage11Client.mode`)

### Order Management

```python
Stage11Order:
  - signal_id, client_id
  - venue, direction, price, size_usd
  - state: PENDING → FILLED
  - venue_order_id (for reconciliation)
  - idempotency_key
  - fill_price, fill_timestamp

Stage11Fill:
  - order_id, fill_price, fill_size
  - pnl_usd (vs signal price)

Stage11ClientPosition:
  - market_id, direction
  - avg_entry_price, current_size_usd
  - unrealized_pnl, realized_pnl
```

### Audit Trail

Every risk decision, order placement, fill, and state change is logged to `Stage11TradingAuditEvent` with full JSON context.

---

## 9. Libraries & Dependencies

### Core Framework

| Library | Version | Use |
|---------|---------|-----|
| `fastapi` | ≥0.115.0 | REST API + async request handling |
| `uvicorn[standard]` | ≥0.30.0 | ASGI server |
| `pydantic` | ≥2.8.0 | Request/response schemas, settings validation |
| `sqlalchemy` | ≥2.0.30 | ORM + query builder |
| `psycopg[binary]` | ≥3.2.0 | PostgreSQL async driver |
| `alembic` | ≥1.13.2 | Database migrations |
| `redis` | ≥5.0.7 | Cache + Celery broker |
| `celery` | ≥5.4.0 | Distributed task queue + scheduler |
| `httpx` | ≥0.27.0 | Async HTTP client for all collectors |

### AI / ML

| Library | Use |
|---------|-----|
| `openai` (via compatible adapter) | LLM calls to Stage 7 agent |
| `langgraph` (optional) | Stateful agent graphs |
| `mlflow` | Experiment tracking for research stages |
| `vectorbt` | Advanced backtesting (optional research) |
| `quantstats` | Portfolio statistics (optional research) |

### NLP / Matching

| Library | Use |
|---------|-----|
| `rapidfuzz` ≥3.9.4 | Fuzzy string matching for duplicate detection |
| `python-dateutil` | Date parsing in signals/timeline |

### Trading Venues

| Library | Use |
|---------|-----|
| `py-clob-client` ≥0.24.0 | Polymarket CLOB order placement |

### Product / Delivery

| Library | Use |
|---------|-----|
| `aiogram` ≥3.10.0 | Telegram bot (signal delivery, watchlist) |
| `structlog` ≥24.4.0 | Structured JSON logging |
| `great-expectations` | Data quality validation (optional) |

---

## 10. Database Schema

### Key Tables

```
platforms              → Polymarket, Manifold, Metaculus, Kalshi
markets                → normalized market data
market_snapshots       → probability history per market
signals                → generated trading signals
signal_history         → labeled signal history (outcomes)
duplicate_market_pairs → detected duplicate pairs
duplicate_pair_candidates → pipeline stages for duplicate detection
rules_analysis         → rules text scoring
liquidity_analysis     → liquidity scoring

stage7_agent_decisions → LLM decision log
stage8_decisions       → category + policy decisions
stage8_positions       → simulated positions
stage10_replay_rows    → replay audit trail

stage11_clients        → trading client configs (mode, credentials)
stage11_orders         → order lifecycle
stage11_fills          → execution records
stage11_client_positions → portfolio state
stage11_trading_audit_events → full audit log

users                  → Telegram user profiles
subscription_plans     → FREE / PRO / PREMIUM
user_subscriptions
watchlist_items
user_events

job_runs               → batch job execution history
signal_generation_stats
signal_quality_metrics
```

---

## 11. Infrastructure

### Deployment

```yaml
services:
  api:        FastAPI (uvicorn) — REST endpoints
  worker:     Celery worker — background jobs
  beat:       Celery beat — job scheduler
  bot:        Telegram bot (aiogram)
  db:         PostgreSQL
  redis:      Redis (broker + cache)
```

### Scheduled Jobs (Celery Beat)

| Job | Frequency | Description |
|-----|-----------|-------------|
| `sync_all_platforms_job` | Every N minutes | Collect all platforms |
| `analyze_markets_job` | Every N minutes | Full signal generation pipeline |
| `stage10_batch_job` | Daily | Replay engine batch |
| `stage11_track_batch_job` | Hourly | Trade outcome tracking |
| `daily_digest_job` | Daily | Telegram digest |
| `signal_push_job` | Every N minutes | Telegram push alerts |
| `cleanup_old_signals_job` | Daily | Data hygiene |

### Configuration

168 environment variables in `.env`:
- Signal thresholds (divergence, liquidity, rules risk)
- Ranking weights (edge, liquidity, execution safety, freshness, confidence)
- Stage 7 agent (provider, model, budget, shadow mode)
- Stage 11 trading (venue, dry run, drawdown limits, readiness gates)
- Execution cost model (fees, slippage, gas)
- Category priors for empirical EV model

---

## 12. Current Performance Metrics

As of last Stage 10 run:

| Metric | Value | Gate |
|--------|-------|------|
| Signal history rows | 2,595 | — |
| Events evaluated | — | ≥100 |
| Leakage violations | 0 | =0 ✅ |
| Post-cost EV CI low 80% | +0.01865 | >0 ✅ |
| Scenario sweeps positive | 18/18 | ≥12 ✅ |
| Core category EV candidates | 1 | ≥1 ✅ |
| Reason code stability | — | ≥90% ✅ |
| Walkforward windows | 0 | N/A (no labeled data) |
| Stage 10 decision | **PASS** | — |

**Data note:** Current signal_history consists entirely of divergence candidates (pre-screening rows, `signal_id=None`). Full pipeline signals (Stage 7/8/9) not yet accumulated in production DB. Walkforward validation pending labeled `probability_after_6h` data.

---

## 13. Known Weaknesses & Improvement Opportunities

### 13.1 Signal Generation

**W1: Purely lexical duplicate detection**
- `rapidfuzz` catches title similarity but misses semantic equivalence
- "Will Bitcoin exceed $100k by EOY?" vs "BTC/USD above 100000 at year end?" → low score
- **Opportunity:** Sentence embeddings (OpenAI `text-embedding-3-small`, or local `bge-small-en`) → cosine similarity on title vectors → 2-5x more duplicate pairs detected

**W2: No market microstructure in divergence**
- Raw `|p_A - p_B|` doesn't account for bid-ask spreads on both sides
- A 3% divergence with 2% spread on each side = 1% real edge, not 3%
- **Opportunity:** Compute executable divergence: `divergence_net = |p_A_ask - p_B_bid|` (for YES direction); gate on `divergence_net > threshold`

**W3: No real-time event detection**
- Scanner polls on a fixed schedule; misses breaking news windows
- Divergences often correct within minutes of a major event
- **Opportunity:** News feed integration (RSS, Twitter/X API, or Perplexity API) → event-triggered signal scan within seconds of detected relevant news

**W4: Static ranking weights**
- Weights (edge: 0.35, liquidity: 0.25, etc.) are fixed in config
- No online learning from realized PnL
- **Opportunity:** Contextual bandit or gradient-boosted ranker trained on `signal → outcome` pairs; update weekly from Stage 11 fill data

### 13.2 Execution Model

**W5: V1 execution model used by default**
- `v1_naive_no_orderbook` uses crude `100/volume` slippage heuristic
- No actual CLOB depth data used for capacity estimation
- **Opportunity:** Pull CLOB orderbook depth from Polymarket API at signal time; compute true market impact for given position size (VWAP impact model)

**W6: No Kelly sizing**
- Position size is flat (configured USD amount)
- No account for signal confidence, edge magnitude, or correlation to existing positions
- **Opportunity:** Full Kelly fraction: `f = (edge - costs) / variance`; fractional Kelly (0.25-0.5f) for drawdown control; portfolio-level correlation penalty

**W7: Single venue**
- Stage 11 currently only has Polymarket CLOB adapter
- Cannot route across venues to optimize execution price
- **Opportunity:** Add Kalshi + Manifold adapters; smart order router that picks venue with best net execution price after costs

### 13.3 LLM Agent (Stage 7)

**W8: LLM as a black box decision maker**
- Single call, single model, no structured reasoning chain
- Expensive per signal, slow, non-deterministic
- **Opportunity:** Fine-tuned small model on historical (signal, decision, outcome) triples; or structured chain-of-thought with explicit sub-questions (Is resolution objective? Is there a clear market bias? Is liquidity sufficient?)

**W9: No context on market's full history**
- LLM receives current snapshot only
- Missing: how has probability moved over time, what events caused moves
- **Opportunity:** Inject summarized MarketSnapshot history (last 7-day trajectory) into LLM prompt; or use a vector-retrieval tool to find similar past signals

**W10: No ensemble or confidence calibration**
- Single model output = decision
- No calibration of LLM confidence scores against actual outcomes
- **Opportunity:** 3-model ensemble (e.g., GPT-4o + Claude + Gemini) with voting; calibration via Platt scaling on historical LLM confidence vs realized outcomes

### 13.4 Backtesting

**W11: No labeled probability_after_6h data yet**
- Walkforward validation passes vacuously (no data)
- Cannot measure realized returns at different horizons
- **Opportunity:** Run labeling job: for every SignalHistory row, fetch or interpolate `probability_after_1h/6h/24h` from MarketSnapshot; backfill historical data from platform APIs

**W12: Edge proxy instead of real returns**
- Stage 10 uses `divergence * 0.5` as edge proxy for legacy rows
- Not the same as actual `entry_price - exit_price`
- **Opportunity:** Cross-reference with Stage 11 fills; build ground-truth return series from actual executed trades; retire edge proxy once fill data accumulates

**W13: Single time horizon (6h)**
- Walk-forward only validates 6h horizon
- Some signal types (RULES_RISK) may have longer resolution horizons
- **Opportunity:** Multi-horizon validation: 1h for divergence signals, 6h for standard, 24h for rules-risk; select horizon adaptively per signal type

### 13.5 Data Collection

**W14: No adversarial market detection**
- Some markets are manipulated or have illiquid orderbooks that are easily moved
- No filter for "whale-dominated" markets
- **Opportunity:** Detect markets where single wallet holds >30% of one side; or where price spike is not correlated with platform-wide movements

**W15: Limited historical data depth**
- Polymarket history not backfilled beyond what was collected live
- Some markets resolved before collector was running
- **Opportunity:** Scrape Polymarket CLOB historical trades API for all resolved markets; bulk-import to fill MarketSnapshot history

### 13.6 Architecture

**W16: Celery polling vs event-driven**
- All jobs run on fixed schedule; no reactive processing
- A signal could be stale by the time it reaches Stage 11
- **Opportunity:** WebSocket subscriptions to Polymarket/Manifold order books; event bus (Kafka or Redis Streams) for real-time signal propagation

**W17: No portfolio-level optimization**
- Each signal evaluated independently
- No correlation tracking (two crypto signals = double crypto exposure)
- **Opportunity:** Portfolio manager layer: track open positions by category/platform; enforce concentration limits; Markowitz-style correlation penalty in position sizing

---

## 14. Research Directions for Substantial Gains

Ranked by estimated impact on Sharpe ratio / win rate:

### Priority 1: Fill the Feedback Loop (Prerequisite for Everything)

The system has ~2,600 signal rows but **zero labeled outcomes** (no `probability_after_6h`). Without this, all empirical models fall back to priors.

**Action:** Implement `stage10_timeline_backfill_run.py` as a scheduled nightly job; backfill all existing rows; add labeling to the live signal ingestion path.

**Expected gain:** Unlocks empirical execution model V2, real walkforward validation, LLM calibration, and ranker training.

### Priority 2: Real Orderbook Integration

Move from volume-heuristic slippage to CLOB-based impact model.

**Action:**
1. Fetch Polymarket CLOB depth at signal time (already supported by `py-clob-client`)
2. Compute market impact for given position size
3. Set `executable_edge = gross_edge - real_impact - fees`
4. Gate: only signal if `executable_edge > 0.02` (2%)

**Expected gain:** Eliminate 30-50% of false positive signals (those that look profitable at mid-price but not at executable price).

### Priority 3: Semantic Duplicate Detection

Replace `rapidfuzz` lexical matching with embedding-based semantic search.

**Action:**
1. Embed all market titles with `text-embedding-3-small` at collection time
2. Store in pgvector (PostgreSQL extension)
3. Near-duplicate: cosine similarity > 0.92
4. Replace current duplicate analyzer or run as parallel layer

**Expected gain:** 2-5x more duplicate detections; catch cross-language duplicates; better divergence signal quality.

### Priority 4: Kelly Position Sizing

Replace flat position size with signal-confidence-adjusted sizing.

**Action:**
1. Use `predicted_edge_after_costs_pct` as `edge` estimate
2. Use empirical variance from signal history as `variance`
3. Apply fractional Kelly: `size = bankroll * 0.25 * (edge / variance)`
4. Cap at `max_position_size_usd`

**Expected gain:** Better risk-adjusted returns; larger positions on high-edge signals; smaller on uncertain ones.

### Priority 5: Online Ranker

Train a gradient-boosted ranker (LightGBM/XGBoost) on `(signal features) → (realized 6h return)`.

**Features:** `divergence_score`, `liquidity_score`, `confidence`, `platform_pair`, `category`, `days_to_resolution`, `time_of_day`, `market_age_days`, `rules_risk_score`, `hit_rate_type_30d`

**Training:** Weekly retraining on last 180 days of labeled signal history.

**Expected gain:** 10-20% improvement in signal selection precision vs static weights.

### Priority 6: Multi-Venue Routing

Add Kalshi and Manifold adapters; smart order router picks best net price.

**Action:**
1. Implement `KalshiClobAdapter` and `ManifoldAdapter` (Stage 11)
2. At order time: query executable price from all available venues
3. Route to venue with highest `fill_price - all_in_costs`

**Expected gain:** 0.5-2% better fill prices on average; access to more markets; cross-venue arbitrage.

### Priority 7: Real-Time News Integration

Subscribe to news feeds; trigger signal scan immediately on relevant events.

**Action:**
1. RSS/Webhook subscription for major news sources
2. Relevance classifier: does this event affect any tracked market?
3. On match: immediate signal scan for affected markets
4. Time-gate: only submit if still within 10-minute window before arbitrage corrects

**Expected gain:** Access to the most profitable divergence windows (first-mover advantage).

### Priority 8: LLM Fine-Tuning

Fine-tune a small model (e.g., `Mistral-7B` or `Llama-3-8B`) on historical Stage 7 decisions + outcomes.

**Dataset:** For each resolved signal, construct: `(signal_context, decision, outcome)` triple. Labels: 1 = correct KEEP, 0 = wrong KEEP or missed SKIP.

**Training:** LoRA fine-tuning, 1-2 epochs.

**Inference:** 10-50x cheaper than GPT-4o calls; <100ms latency (vs 2-5s); fully deterministic.

**Expected gain:** 90%+ cost reduction on Stage 7; faster pipeline; potential accuracy improvement from specialized training.

---

## Appendix: File Index

```
app/
├── main.py                          # FastAPI app setup
├── core/config.py                   # 168 env vars via Pydantic Settings
├── models/models.py                 # 22+ SQLAlchemy ORM tables
├── models/enums.py                  # SignalType, AccessLevel, etc.
├── api/routes/
│   ├── analytics.py                 # divergence, kpi, retention endpoints
│   ├── signals.py                   # top/latest signals endpoints
│   ├── markets.py                   # market CRUD + analysis
│   └── admin.py                     # sync, run-analysis, test-signal
├── services/
│   ├── collectors/                  # manifold, metaculus, polymarket, kalshi
│   ├── analyzers/                   # duplicate, divergence, liquidity, rules_risk
│   ├── signals/
│   │   ├── engine.py                # full signal generation orchestration
│   │   ├── ranking.py               # rank_score, appendix_c_score, top selection
│   │   └── execution.py             # ExecutionSimulator V1/V2
│   ├── agent_stage7/                # LLM decision agent + stack adapters
│   ├── agent_stage8/                # category classifier + policy gates
│   ├── research/
│   │   ├── stage10_replay.py        # event replay engine (core backtesting)
│   │   ├── stage10_final_report.py  # PASS/WARN/DATA_PENDING verdict
│   │   ├── stage10_timeline_sources.py  # anti-lookahead timeline resolution
│   │   ├── walkforward.py           # rolling-window out-of-sample validation
│   │   └── stage5..stage9/          # per-stage research reports
│   └── stage11/
│       ├── execution_router.py      # order routing
│       ├── order_manager.py         # order lifecycle
│       ├── risk_engine.py           # circuit breakers (SOFT/HARD/PANIC)
│       ├── readiness.py             # SHADOW→LIMITED→FULL gates
│       └── venues/polymarket_clob_adapter.py
├── tasks/jobs.py                    # Celery job definitions
└── bot/bot_app.py                   # Telegram bot handlers

tests/                               # 40+ test modules (pytest)
scripts/                             # stage*_track_batch.py scripts
docs/                                # architecture, config, algorithm docs
```

---

*Last updated: 2026-03-16. Stage 10 status: PASS. Stage 11 status: SHADOW mode pending labeled data accumulation.*
