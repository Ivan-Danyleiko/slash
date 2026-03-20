from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    # Comma-separated list of extra admin Telegram user IDs (e.g. "123456,789012")
    telegram_admin_ids: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")
    admin_api_key: str = Field(default="", alias="ADMIN_API_KEY")
    admin_write_min_interval_ms: int = Field(default=250, alias="ADMIN_WRITE_MIN_INTERVAL_MS")
    admin_read_min_interval_ms: int = Field(default=100, alias="ADMIN_READ_MIN_INTERVAL_MS")
    admin_heavy_get_cache_ttl_sec: int = Field(default=30, alias="ADMIN_HEAVY_GET_CACHE_TTL_SEC")
    dryrun_manual_min_run_interval_sec: int = Field(default=20, alias="DRYRUN_MANUAL_MIN_RUN_INTERVAL_SEC")
    dryrun_report_cache_ttl_sec: int = Field(default=5, alias="DRYRUN_REPORT_CACHE_TTL_SEC")
    user_identity_hmac_secret: str = Field(default="", alias="USER_IDENTITY_HMAC_SECRET")
    user_identity_max_skew_sec: int = Field(default=300, alias="USER_IDENTITY_MAX_SKEW_SEC")

    manifold_api_base_url: str = Field(
        default="https://api.manifold.markets/v0", alias="MANIFOLD_API_BASE_URL"
    )
    metaculus_api_base_url: str = Field(
        default="https://www.metaculus.com/api2", alias="METACULUS_API_BASE_URL"
    )
    metaculus_user_agent: str = Field(
        default="PredictionMarketScanner/0.1 (+https://local-mvp)",
        alias="METACULUS_USER_AGENT",
    )
    metaculus_api_token: str = Field(default="", alias="METACULUS_API_TOKEN")
    polymarket_api_base_url: str = Field(
        default="https://gamma-api.polymarket.com", alias="POLYMARKET_API_BASE_URL"
    )
    polymarket_clob_api_base_url: str = Field(
        default="https://clob.polymarket.com", alias="POLYMARKET_CLOB_API_BASE_URL"
    )
    polymarket_clob_enabled: bool = Field(default=False, alias="POLYMARKET_CLOB_ENABLED")
    polymarket_clob_api_key: str = Field(default="", alias="POLYMARKET_CLOB_API_KEY")
    kalshi_api_base_url: str = Field(
        default="https://api.elections.kalshi.com/trade-api/v2", alias="KALSHI_API_BASE_URL"
    )
    kalshi_historical_api_base_url: str = Field(
        default="https://api.elections.kalshi.com/trade-api/v2/historical", alias="KALSHI_HISTORICAL_API_BASE_URL"
    )
    kalshi_api_key: str = Field(default="", alias="KALSHI_API_KEY")
    kalshi_enabled: bool = Field(default=False, alias="KALSHI_ENABLED")

    signal_duplicate_threshold: float = Field(default=85.0, alias="SIGNAL_DUPLICATE_THRESHOLD")
    signal_duplicate_broad_threshold: float = Field(default=75.0, alias="SIGNAL_DUPLICATE_BROAD_THRESHOLD")
    signal_duplicate_broad_min_overlap: int = Field(default=1, alias="SIGNAL_DUPLICATE_BROAD_MIN_OVERLAP")
    signal_duplicate_broad_min_jaccard: float = Field(default=0.0, alias="SIGNAL_DUPLICATE_BROAD_MIN_JACCARD")
    signal_duplicate_broad_min_weighted_overlap: float = Field(
        default=0.0, alias="SIGNAL_DUPLICATE_BROAD_MIN_WEIGHTED_OVERLAP"
    )
    signal_duplicate_broad_relaxed_fuzzy_min: float = Field(
        default=88.0, alias="SIGNAL_DUPLICATE_BROAD_RELAXED_FUZZY_MIN"
    )
    signal_duplicate_min_overlap: int = Field(default=2, alias="SIGNAL_DUPLICATE_MIN_OVERLAP")
    signal_duplicate_min_jaccard: float = Field(default=0.42, alias="SIGNAL_DUPLICATE_MIN_JACCARD")
    signal_duplicate_min_weighted_overlap: float = Field(
        default=7.5, alias="SIGNAL_DUPLICATE_MIN_WEIGHTED_OVERLAP"
    )
    signal_duplicate_anchor_idf: float = Field(default=4.5, alias="SIGNAL_DUPLICATE_ANCHOR_IDF")
    signal_duplicate_min_pair_liquidity: float = Field(
        default=0.15, alias="SIGNAL_DUPLICATE_MIN_PAIR_LIQUIDITY"
    )
    signal_divergence_threshold: float = Field(default=0.15, alias="SIGNAL_DIVERGENCE_THRESHOLD")
    signal_divergence_use_executable: bool = Field(default=False, alias="SIGNAL_DIVERGENCE_USE_EXECUTABLE")
    signal_divergence_net_edge_min: float = Field(default=0.02, alias="SIGNAL_DIVERGENCE_NET_EDGE_MIN")
    signal_divergence_position_size_usd: float = Field(default=50.0, alias="SIGNAL_DIVERGENCE_POSITION_SIZE_USD")
    signal_divergence_gas_fee_usd: float = Field(default=2.0, alias="SIGNAL_DIVERGENCE_GAS_FEE_USD")
    signal_divergence_bridge_fee_usd: float = Field(default=0.5, alias="SIGNAL_DIVERGENCE_BRIDGE_FEE_USD")
    signal_divergence_min_pair_liquidity: float = Field(
        default=0.1, alias="SIGNAL_DIVERGENCE_MIN_PAIR_LIQUIDITY"
    )
    signal_divergence_research_min_similarity: float = Field(
        default=70.0, alias="SIGNAL_DIVERGENCE_RESEARCH_MIN_SIMILARITY"
    )
    signal_divergence_research_min_diff: float = Field(
        default=0.03, alias="SIGNAL_DIVERGENCE_RESEARCH_MIN_DIFF"
    )
    signal_divergence_research_max_diff: float = Field(
        default=0.90, alias="SIGNAL_DIVERGENCE_RESEARCH_MAX_DIFF"
    )
    signal_divergence_research_max_samples_per_run: int = Field(
        default=20, alias="SIGNAL_DIVERGENCE_RESEARCH_MAX_SAMPLES_PER_RUN"
    )
    signal_divergence_research_sample_cooldown_minutes: int = Field(
        default=180, alias="SIGNAL_DIVERGENCE_RESEARCH_SAMPLE_COOLDOWN_MINUTES"
    )
    signal_divergence_research_min_volume_24h: float = Field(
        default=50.0, alias="SIGNAL_DIVERGENCE_RESEARCH_MIN_VOLUME_24H"
    )
    signal_divergence_research_min_pair_liquidity: float = Field(
        default=0.1, alias="SIGNAL_DIVERGENCE_RESEARCH_MIN_PAIR_LIQUIDITY"
    )
    signal_divergence_research_min_prob: float = Field(
        default=0.005, alias="SIGNAL_DIVERGENCE_RESEARCH_MIN_PROB"
    )
    signal_divergence_research_max_prob: float = Field(
        default=0.995, alias="SIGNAL_DIVERGENCE_RESEARCH_MAX_PROB"
    )
    signal_divergence_research_fallback_min_shared_tokens: int = Field(
        default=1, alias="SIGNAL_DIVERGENCE_RESEARCH_FALLBACK_MIN_SHARED_TOKENS"
    )
    signal_divergence_research_fallback_min_jaccard: float = Field(
        default=0.1, alias="SIGNAL_DIVERGENCE_RESEARCH_FALLBACK_MIN_JACCARD"
    )
    signal_arbitrage_min_liquidity: float = Field(default=0.55, alias="SIGNAL_ARBITRAGE_MIN_LIQUIDITY")
    signal_arbitrage_min_volume_24h: float = Field(default=250.0, alias="SIGNAL_ARBITRAGE_MIN_VOLUME_24H")
    signal_arbitrage_min_move: float = Field(default=0.08, alias="SIGNAL_ARBITRAGE_MIN_MOVE")
    signal_arbitrage_midpoint_band: float = Field(default=0.12, alias="SIGNAL_ARBITRAGE_MIDPOINT_BAND")
    signal_arbitrage_max_candidates: int = Field(default=30, alias="SIGNAL_ARBITRAGE_MAX_CANDIDATES")
    signal_arbitrage_min_liquidity_manifold: float = Field(
        default=0.25, alias="SIGNAL_ARBITRAGE_MIN_LIQUIDITY_MANIFOLD"
    )
    signal_arbitrage_min_volume_24h_manifold: float = Field(
        default=30.0, alias="SIGNAL_ARBITRAGE_MIN_VOLUME_24H_MANIFOLD"
    )
    signal_arbitrage_min_manifold_candidates: int = Field(
        default=10, alias="SIGNAL_ARBITRAGE_MIN_MANIFOLD_CANDIDATES"
    )
    signal_sources_enabled: str = Field(
        default="POLYMARKET,MANIFOLD", alias="SIGNAL_SOURCES_ENABLED"
    )
    signal_manifold_max_per_cycle: int = Field(
        default=20, alias="SIGNAL_MANIFOLD_MAX_PER_CYCLE"
    )
    signal_arbitrage_exclude_keywords: str = Field(
        default="coinflip,coin flip,lottery,dice,die roll,random,daily coin,daily coin flip,free lottery",
        alias="SIGNAL_ARBITRAGE_EXCLUDE_KEYWORDS",
    )
    signal_rules_risk_threshold: float = Field(default=0.2, alias="SIGNAL_RULES_RISK_THRESHOLD")
    signal_rules_missing_min_liquidity: float = Field(default=0.72, alias="SIGNAL_RULES_MISSING_MIN_LIQUIDITY")
    signal_rules_missing_min_volume_24h: float = Field(default=300.0, alias="SIGNAL_RULES_MISSING_MIN_VOLUME_24H")
    signal_mode_momentum_min_move: float = Field(default=0.10, alias="SIGNAL_MODE_MOMENTUM_MIN_MOVE")
    signal_mode_uncertainty_max_score: float = Field(default=0.65, alias="SIGNAL_MODE_UNCERTAINTY_MAX_SCORE")
    signal_tail_enabled: bool = Field(default=False, alias="SIGNAL_TAIL_ENABLED")
    signal_tail_min_prob: float = Field(default=0.03, alias="SIGNAL_TAIL_MIN_PROB")
    signal_tail_max_prob: float = Field(default=0.25, alias="SIGNAL_TAIL_MAX_PROB")
    signal_tail_min_mispricing_ratio: float = Field(default=2.0, alias="SIGNAL_TAIL_MIN_MISPRICING_RATIO")
    signal_tail_min_our_prob: float = Field(default=0.05, alias="SIGNAL_TAIL_MIN_OUR_PROB")
    signal_tail_max_market_prob_for_entry: float = Field(default=0.25, alias="SIGNAL_TAIL_MAX_MARKET_PROB_FOR_ENTRY")
    signal_tail_min_koef: float = Field(default=5.0, alias="SIGNAL_TAIL_MIN_KOEF")
    signal_tail_sweet_spot_koef_min: float = Field(default=10.0, alias="SIGNAL_TAIL_SWEET_SPOT_KOEF_MIN")
    signal_tail_sweet_spot_koef_max: float = Field(default=50.0, alias="SIGNAL_TAIL_SWEET_SPOT_KOEF_MAX")
    signal_tail_max_koef: float = Field(default=200.0, alias="SIGNAL_TAIL_MAX_KOEF")
    signal_tail_max_days_to_resolution: int = Field(default=365, alias="SIGNAL_TAIL_MAX_DAYS_TO_RESOLUTION")
    signal_tail_min_volume_usd: float = Field(default=500.0, alias="SIGNAL_TAIL_MIN_VOLUME_USD")
    signal_tail_max_candidates: int = Field(default=20, alias="SIGNAL_TAIL_MAX_CANDIDATES")
    signal_tail_max_positions_open: int = Field(default=100, alias="SIGNAL_TAIL_MAX_POSITIONS_OPEN")
    signal_tail_base_rate_external_enabled: bool = Field(
        default=False,
        alias="SIGNAL_TAIL_BASE_RATE_EXTERNAL_ENABLED",
    )
    signal_tail_budget_pct: float = Field(default=0.10, alias="SIGNAL_TAIL_BUDGET_PCT")
    signal_tail_notional_pct: float = Field(default=0.005, alias="SIGNAL_TAIL_NOTIONAL_PCT")
    signal_tail_reference_balance_usd: float = Field(
        default=1000.0, alias="SIGNAL_TAIL_REFERENCE_BALANCE_USD"
    )
    signal_tail_max_single_bet_usd: float = Field(default=25.0, alias="SIGNAL_TAIL_MAX_SINGLE_BET_USD")
    signal_tail_take_profit_ratio: float = Field(default=0.50, alias="SIGNAL_TAIL_TAKE_PROFIT_RATIO")
    signal_tail_stop_loss_floor_mult: float = Field(default=0.30, alias="SIGNAL_TAIL_STOP_LOSS_FLOOR_MULT")
    signal_tail_days_before_resolution_exit: int = Field(default=3, alias="SIGNAL_TAIL_DAYS_BEFORE_RESOLUTION_EXIT")
    signal_tail_min_mark_to_hold_mult: float = Field(default=1.5, alias="SIGNAL_TAIL_MIN_MARK_TO_HOLD_MULT")
    signal_tail_category_limit_crypto: float = Field(default=0.20, alias="SIGNAL_TAIL_CATEGORY_LIMIT_CRYPTO")
    signal_tail_category_limit_disasters: float = Field(default=0.03, alias="SIGNAL_TAIL_CATEGORY_LIMIT_DISASTERS")
    signal_tail_category_limit_geopolitics: float = Field(default=0.20, alias="SIGNAL_TAIL_CATEGORY_LIMIT_GEOPOLITICS")
    signal_tail_category_limit_sports: float = Field(default=0.20, alias="SIGNAL_TAIL_CATEGORY_LIMIT_SPORTS")
    signal_tail_category_limit_regulatory: float = Field(default=0.20, alias="SIGNAL_TAIL_CATEGORY_LIMIT_REGULATORY")
    signal_tail_category_limit_zero_event: float = Field(default=0.20, alias="SIGNAL_TAIL_CATEGORY_LIMIT_ZERO_EVENT")
    signal_tail_circuit_breaker_consecutive_losses: int = Field(
        default=3, alias="SIGNAL_TAIL_CIRCUIT_BREAKER_CONSECUTIVE_LOSSES"
    )
    signal_tail_circuit_breaker_cooldown_hours: int = Field(
        default=24, alias="SIGNAL_TAIL_CIRCUIT_BREAKER_COOLDOWN_HOURS"
    )
    signal_tail_llm_temperature: float = Field(default=0.0, alias="SIGNAL_TAIL_LLM_TEMPERATURE")
    signal_tail_llm_prompt_version: str = Field(default="tail_v1", alias="SIGNAL_TAIL_LLM_PROMPT_VERSION")
    stage17_tail_min_closed_positions: int = Field(default=40, alias="STAGE17_TAIL_MIN_CLOSED_POSITIONS")
    stage17_tail_min_top10pct_wins_count: int = Field(default=3, alias="STAGE17_TAIL_MIN_TOP10PCT_WINS_COUNT")
    stage17_tail_min_hit_rate: float = Field(default=0.10, alias="STAGE17_TAIL_MIN_HIT_RATE")
    stage17_tail_min_payout_skew: float = Field(default=0.50, alias="STAGE17_TAIL_MIN_PAYOUT_SKEW")
    stage17_tail_min_payout_skew_ci_low_80: float = Field(
        default=0.35, alias="STAGE17_TAIL_MIN_PAYOUT_SKEW_CI_LOW_80"
    )
    stage17_tail_max_time_to_resolution_days: float = Field(
        default=45.0, alias="STAGE17_TAIL_MAX_TIME_TO_RESOLUTION_DAYS"
    )
    stage17_tail_min_avg_win_multiplier: float = Field(default=8.0, alias="STAGE17_TAIL_MIN_AVG_WIN_MULTIPLIER")
    stage17_tail_bootstrap_resamples: int = Field(default=1000, alias="STAGE17_TAIL_BOOTSTRAP_RESAMPLES")
    signal_rules_missing_daily_cap: int = Field(default=8, alias="SIGNAL_RULES_MISSING_DAILY_CAP")
    snapshot_fresh_hours: int = Field(default=6, alias="SNAPSHOT_FRESH_HOURS")
    top_window_size: int = Field(default=20, alias="TOP_WINDOW_SIZE")
    signal_top_min_score_total: float = Field(default=0.45, alias="SIGNAL_TOP_MIN_SCORE_TOTAL")
    signal_top_min_utility_score: float = Field(default=0.08, alias="SIGNAL_TOP_MIN_UTILITY_SCORE")
    signal_top_max_missing_rules_share: float = Field(default=0.35, alias="SIGNAL_TOP_MAX_MISSING_RULES_SHARE")
    signal_top_min_confidence_missing_rules: float = Field(
        default=0.35, alias="SIGNAL_TOP_MIN_CONFIDENCE_MISSING_RULES"
    )
    signal_top_rules_risk_min_confidence: float = Field(
        default=0.45, alias="SIGNAL_TOP_RULES_RISK_MIN_CONFIDENCE"
    )
    signal_top_rules_risk_min_liquidity: float = Field(
        default=0.55, alias="SIGNAL_TOP_RULES_RISK_MIN_LIQUIDITY"
    )
    signal_top_allow_fallback_when_empty: bool = Field(default=True, alias="SIGNAL_TOP_ALLOW_FALLBACK_WHEN_EMPTY")
    signal_top_use_v2_selection: bool = Field(default=True, alias="SIGNAL_TOP_USE_V2_SELECTION")
    signal_top_v2_rank_by_score_total: bool = Field(default=True, alias="SIGNAL_TOP_V2_RANK_BY_SCORE_TOTAL")
    signal_top_appendix_c_enabled: bool = Field(default=True, alias="SIGNAL_TOP_APPENDIX_C_ENABLED")
    signal_rank_weight_edge: float = Field(default=0.35, alias="SIGNAL_RANK_WEIGHT_EDGE")
    signal_rank_weight_liquidity: float = Field(default=0.25, alias="SIGNAL_RANK_WEIGHT_LIQUIDITY")
    signal_rank_weight_execution_safety: float = Field(default=0.20, alias="SIGNAL_RANK_WEIGHT_EXECUTION_SAFETY")
    signal_rank_weight_freshness: float = Field(default=0.10, alias="SIGNAL_RANK_WEIGHT_FRESHNESS")
    signal_rank_weight_confidence: float = Field(default=0.10, alias="SIGNAL_RANK_WEIGHT_CONFIDENCE")
    signal_execution_model: str = Field(default="v2", alias="SIGNAL_EXECUTION_MODEL")
    signal_execution_v2_horizon: str = Field(default="6h", alias="SIGNAL_EXECUTION_V2_HORIZON")
    signal_execution_v2_lookback_days: int = Field(default=60, alias="SIGNAL_EXECUTION_V2_LOOKBACK_DAYS")
    signal_execution_v2_min_samples: int = Field(default=30, alias="SIGNAL_EXECUTION_V2_MIN_SAMPLES")
    signal_execution_v2_prior_default: float = Field(default=0.02, alias="SIGNAL_EXECUTION_V2_PRIOR_DEFAULT")
    signal_execution_v2_prior_crypto: float = Field(default=0.025, alias="SIGNAL_EXECUTION_V2_PRIOR_CRYPTO")
    signal_execution_v2_prior_finance: float = Field(default=0.02, alias="SIGNAL_EXECUTION_V2_PRIOR_FINANCE")
    signal_execution_v2_prior_sports: float = Field(default=0.015, alias="SIGNAL_EXECUTION_V2_PRIOR_SPORTS")
    signal_execution_v2_prior_politics: float = Field(default=0.02, alias="SIGNAL_EXECUTION_V2_PRIOR_POLITICS")
    signal_execution_v2_prior_other: float = Field(default=0.015, alias="SIGNAL_EXECUTION_V2_PRIOR_OTHER")
    signal_execution_position_size_usd: float = Field(default=100.0, alias="SIGNAL_EXECUTION_POSITION_SIZE_USD")
    signal_execution_polymarket_mode: str = Field(
        default="gamma_api",
        alias="SIGNAL_EXECUTION_POLYMARKET_MODE",
    )
    signal_execution_polymarket_gas_fee_usd: float = Field(
        default=0.50,
        alias="SIGNAL_EXECUTION_POLYMARKET_GAS_FEE_USD",
    )
    signal_execution_polymarket_bridge_fee_usd: float = Field(
        default=0.0,
        alias="SIGNAL_EXECUTION_POLYMARKET_BRIDGE_FEE_USD",
    )
    signal_execution_polymarket_fee_mode: str = Field(
        default="zero",
        alias="SIGNAL_EXECUTION_POLYMARKET_FEE_MODE",
    )
    signal_execution_polymarket_negrisk_impact_multiplier: float = Field(
        default=0.7,
        alias="SIGNAL_EXECUTION_POLYMARKET_NEGRISK_IMPACT_MULTIPLIER",
    )
    signal_execution_kalshi_taker_coeff: float = Field(
        default=0.07,
        alias="SIGNAL_EXECUTION_KALSHI_TAKER_COEFF",
    )
    signal_execution_kalshi_maker_fee_pct: float = Field(
        default=0.0,
        alias="SIGNAL_EXECUTION_KALSHI_MAKER_FEE_PCT",
    )
    agent_policy_keep_ev_threshold_pct: float = Field(default=0.02, alias="AGENT_POLICY_KEEP_EV_THRESHOLD_PCT")
    agent_policy_modify_ev_threshold_pct: float = Field(default=0.005, alias="AGENT_POLICY_MODIFY_EV_THRESHOLD_PCT")
    agent_policy_min_confidence: float = Field(default=0.40, alias="AGENT_POLICY_MIN_CONFIDENCE")
    agent_policy_min_liquidity: float = Field(default=0.50, alias="AGENT_POLICY_MIN_LIQUIDITY")
    agent_policy_version: str = Field(default="policy_v1", alias="AGENT_POLICY_VERSION")
    stage7_agent_provider: str = Field(default="langgraph", alias="STAGE7_AGENT_PROVIDER")
    stage7_agent_shadow_enabled: bool = Field(default=True, alias="STAGE7_AGENT_SHADOW_ENABLED")
    stage7_agent_real_calls_enabled: bool = Field(default=False, alias="STAGE7_AGENT_REAL_CALLS_ENABLED")
    stage7_agent_max_latency_ms: int = Field(default=1200, alias="STAGE7_AGENT_MAX_LATENCY_MS")
    stage7_agent_monthly_budget_usd: float = Field(default=150.0, alias="STAGE7_AGENT_MONTHLY_BUDGET_USD")
    stage7_agent_cost_per_call_usd: float = Field(default=0.002, alias="STAGE7_AGENT_COST_PER_CALL_USD")
    stage7_agent_tool_snapshot_version: str = Field(
        default="v1",
        alias="STAGE7_AGENT_TOOL_SNAPSHOT_VERSION",
    )
    stage7_agent_internal_gate_profile: str = Field(
        default="balanced",
        alias="STAGE7_AGENT_INTERNAL_GATE_PROFILE",
    )
    stage7_agent_provider_profile: str = Field(
        default="openai",
        alias="STAGE7_AGENT_PROVIDER_PROFILE",
    )
    stage7_ensemble_enabled: bool = Field(default=False, alias="STAGE7_ENSEMBLE_ENABLED")
    stage7_ensemble_models: str = Field(
        default="gpt-4o-mini,claude-haiku-4-5",
        alias="STAGE7_ENSEMBLE_MODELS",
    )
    stage7_ensemble_voting: str = Field(default="majority", alias="STAGE7_ENSEMBLE_VOTING")
    stage7_portfolio_context_enabled: bool = Field(
        default=True,
        alias="STAGE7_PORTFOLIO_CONTEXT_ENABLED",
    )
    stage7_historical_rag_enabled: bool = Field(
        default=True,
        alias="STAGE7_HISTORICAL_RAG_ENABLED",
    )
    stage7_historical_rag_min_similar: int = Field(
        default=2,
        alias="STAGE7_HISTORICAL_RAG_MIN_SIMILAR",
    )
    stage7_historical_rag_limit: int = Field(
        default=3,
        alias="STAGE7_HISTORICAL_RAG_LIMIT",
    )
    stage8_policy_profile: str = Field(default="bootstrap_v1", alias="STAGE8_POLICY_PROFILE")
    stage8_policy_version: str = Field(default="stage8_bootstrap_v1", alias="STAGE8_POLICY_VERSION")
    stage8_category_confidence_floor: float = Field(
        default=0.60,
        alias="STAGE8_CATEGORY_CONFIDENCE_FLOOR",
    )
    stage10_replay_embargo_seconds: int = Field(default=0, alias="STAGE10_REPLAY_EMBARGO_SECONDS")
    stage10_llm_budget_usd_monthly: float = Field(default=100.0, alias="STAGE10_LLM_BUDGET_USD_MONTHLY")
    stage10_backfill_metaculus_delay_seconds: float = Field(
        default=2.0,
        alias="STAGE10_BACKFILL_METACULUS_DELAY_SECONDS",
    )
    stage10_backfill_manifold_delay_seconds: float = Field(
        default=0.3,
        alias="STAGE10_BACKFILL_MANIFOLD_DELAY_SECONDS",
    )
    stage11_soft_daily_drawdown_pct: float = Field(default=-1.5, alias="STAGE11_SOFT_DAILY_DRAWDOWN_PCT")
    stage11_soft_consecutive_losses: int = Field(default=4, alias="STAGE11_SOFT_CONSECUTIVE_LOSSES")
    stage11_hard_daily_drawdown_pct: float = Field(default=-3.0, alias="STAGE11_HARD_DAILY_DRAWDOWN_PCT")
    stage11_hard_weekly_drawdown_pct: float = Field(default=-5.0, alias="STAGE11_HARD_WEEKLY_DRAWDOWN_PCT")
    stage11_hard_consecutive_losses: int = Field(default=7, alias="STAGE11_HARD_CONSECUTIVE_LOSSES")
    stage11_panic_daily_drawdown_pct: float = Field(default=-6.0, alias="STAGE11_PANIC_DAILY_DRAWDOWN_PCT")
    stage11_panic_execution_error_rate_1h: float = Field(default=0.10, alias="STAGE11_PANIC_EXECUTION_ERROR_RATE_1H")
    stage11_panic_reconciliation_gap_usd: float = Field(default=50.0, alias="STAGE11_PANIC_RECONCILIATION_GAP_USD")
    stage11_max_unknown_recovery_sec: int = Field(default=120, alias="STAGE11_MAX_UNKNOWN_RECOVERY_SEC")
    stage11_min_shadow_days: int = Field(default=14, alias="STAGE11_MIN_SHADOW_DAYS")
    stage11_limited_min_days: int = Field(default=30, alias="STAGE11_LIMITED_MIN_DAYS")
    stage11_limited_min_trades: int = Field(default=100, alias="STAGE11_LIMITED_MIN_TRADES")
    stage11_realized_return_tolerance_pct: float = Field(
        default=0.02,
        alias="STAGE11_REALIZED_RETURN_TOLERANCE_PCT",
    )
    stage11_allowed_custody_modes: str = Field(
        default="CLIENT_SIGNED,MANAGED_HOT_WALLET",
        alias="STAGE11_ALLOWED_CUSTODY_MODES",
    )
    stage11_venue: str = Field(default="POLYMARKET_CLOB", alias="STAGE11_VENUE")
    stage11_venue_dry_run: bool = Field(default=True, alias="STAGE11_VENUE_DRY_RUN")
    stage11_clob_use_sdk: bool = Field(default=False, alias="STAGE11_CLOB_USE_SDK")
    stage11_clob_private_key: str = Field(default="", alias="STAGE11_CLOB_PRIVATE_KEY")
    stage11_clob_funder_address: str = Field(default="", alias="STAGE11_CLOB_FUNDER_ADDRESS")
    stage11_clob_chain_id: int = Field(default=137, alias="STAGE11_CLOB_CHAIN_ID")
    dryrun_cross_platform_prob_weight: float = Field(
        default=0.60,
        alias="DRYRUN_CROSS_PLATFORM_PROB_WEIGHT",
    )
    dryrun_cross_platform_min_diff: float = Field(
        default=0.05,
        alias="DRYRUN_CROSS_PLATFORM_MIN_DIFF",
    )
    stage7_openai_api_base_url: str = Field(
        default="https://api.openai.com/v1",
        alias="STAGE7_OPENAI_API_BASE_URL",
    )
    stage7_openai_api_key: str = Field(default="", alias="STAGE7_OPENAI_API_KEY")
    stage7_openai_model: str = Field(default="gpt-5-mini", alias="STAGE7_OPENAI_MODEL")
    stage7_openai_timeout_seconds: float = Field(default=12.0, alias="STAGE7_OPENAI_TIMEOUT_SECONDS")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    stage7_gemini_model: str = Field(default="gemini-2.5-flash", alias="STAGE7_GEMINI_MODEL")
    stage7_groq_model: str = Field(default="llama-3.3-70b-versatile", alias="STAGE7_GROQ_MODEL")
    stage7_openrouter_model: str = Field(
        default="google/gemini-2.5-flash-preview",
        alias="STAGE7_OPENROUTER_MODEL",
    )
    stage7_openrouter_http_referer: str = Field(default="", alias="STAGE7_OPENROUTER_HTTP_REFERER")
    stage7_openrouter_x_title: str = Field(default="prediction-market-scanner", alias="STAGE7_OPENROUTER_X_TITLE")
    signal_history_retention_days: int = Field(default=90, alias="SIGNAL_HISTORY_RETENTION_DAYS")
    signal_labeling_tolerance_minutes: int = Field(default=10, alias="SIGNAL_LABELING_TOLERANCE_MINUTES")
    signal_labeling_horizon_lag_hours: float = Field(default=2.0, alias="SIGNAL_LABELING_HORIZON_LAG_HOURS")
    signal_duplicate_resolution_window_days: int = Field(
        default=365, alias="SIGNAL_DUPLICATE_RESOLUTION_WINDOW_DAYS"
    )
    research_tracking_enabled: bool = Field(default=True, alias="RESEARCH_TRACKING_ENABLED")
    research_experiment_registry_path: str = Field(
        default="artifacts/research/experiments.jsonl",
        alias="RESEARCH_EXPERIMENT_REGISTRY_PATH",
    )
    research_mlflow_enabled: bool = Field(default=False, alias="RESEARCH_MLFLOW_ENABLED")
    research_mlflow_tracking_uri: str = Field(default="", alias="RESEARCH_MLFLOW_TRACKING_URI")
    research_mlflow_experiment_name: str = Field(
        default="stage5_signal_quality",
        alias="RESEARCH_MLFLOW_EXPERIMENT_NAME",
    )
    research_great_expectations_enabled: bool = Field(
        default=False, alias="RESEARCH_GREAT_EXPECTATIONS_ENABLED"
    )
    research_ab_enabled: bool = Field(default=False, alias="RESEARCH_AB_ENABLED")
    research_ab_control_share: float = Field(default=0.5, alias="RESEARCH_AB_CONTROL_SHARE")
    research_ab_salt: str = Field(default="stage5-ab", alias="RESEARCH_AB_SALT")
    research_ab_experiment_name: str = Field(default="stage5_signal_framework", alias="RESEARCH_AB_EXPERIMENT_NAME")
    research_ab_control_label: str = Field(default="v2_control", alias="RESEARCH_AB_CONTROL_LABEL")
    research_ab_treatment_label: str = Field(default="v3_treatment", alias="RESEARCH_AB_TREATMENT_LABEL")
    research_ethics_disclaimer_text: str = Field(
        default=(
            "This is algorithmic analysis, not financial advice. "
            "Prediction markets involve risk. Past performance != future results."
        ),
        alias="RESEARCH_ETHICS_DISCLAIMER_TEXT",
    )
    rules_risk_keywords: str = Field(
        default="consensus,credible media,if unavailable,team decision,sole discretion,may be resolved by",
        alias="RULES_RISK_KEYWORDS",
    )

    free_plan_daily_signals: int = Field(default=5, alias="FREE_PLAN_DAILY_SIGNALS")
    pro_plan_daily_signals: int = Field(default=30, alias="PRO_PLAN_DAILY_SIGNALS")
    premium_plan_daily_signals: int = Field(default=200, alias="PREMIUM_PLAN_DAILY_SIGNALS")

    stage18_event_canon_enabled: bool = Field(default=True, alias="STAGE18_EVENT_CANON_ENABLED")
    stage18_event_group_min_confidence: float = Field(default=0.60, alias="STAGE18_EVENT_GROUP_MIN_CONFIDENCE")
    stage18_topic_weights_enabled: bool = Field(default=True, alias="STAGE18_TOPIC_WEIGHTS_ENABLED")
    stage18_topic_weights_min_n: int = Field(default=100, alias="STAGE18_TOPIC_WEIGHTS_MIN_N")
    stage18_structural_arb_enabled: bool = Field(default=True, alias="STAGE18_STRUCTURAL_ARB_ENABLED")
    stage18_structural_arb_min_underround: float = Field(default=0.015, alias="STAGE18_STRUCTURAL_ARB_MIN_UNDERROUND")
    stage18_structural_arb_max_group_size: int = Field(default=8, alias="STAGE18_STRUCTURAL_ARB_MAX_GROUP_SIZE")
    stage18_require_kalshi: bool = Field(default=False, alias="STAGE18_REQUIRE_KALSHI")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
