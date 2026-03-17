from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    JSON,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AccessLevel, SignalType, SubscriptionStatus


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        UniqueConstraint("platform_id", "external_market_id", name="uq_platform_market_external"),
        Index("ix_market_created_at", "created_at"),
        Index("ix_market_resolution_time", "resolution_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    external_market_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probability_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_no: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    spread_cents: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_bid_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_ask_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_neg_risk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional_value_dollars: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_yes_bid: Mapped[float | None] = mapped_column(Float, nullable=True)

    platform: Mapped[Platform] = relationship()


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    probability_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_no: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signal_type", "signal_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_type: Mapped[SignalType] = mapped_column(
        Enum(SignalType, native_enum=False), nullable=False
    )
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    related_market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rules_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signal_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score_breakdown_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    drop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signal_direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SignalHistory(Base):
    __tablename__ = "signal_history"
    __table_args__ = (
        Index("ix_signal_history_timestamp", "timestamp"),
        Index("ix_signal_history_signal_type_timestamp", "signal_type", "timestamp"),
        Index("ix_signal_history_platform_timestamp", "platform", "timestamp"),
        Index("ix_signal_history_signal_id", "signal_id"),
        Index("ix_signal_history_source_tag", "source_tag"),
        UniqueConstraint(
            "platform",
            "market_id",
            "related_market_id",
            "signal_type",
            "timestamp_bucket",
            name="uq_signal_history_idempotent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
    signal_type: Mapped[SignalType] = mapped_column(Enum(SignalType, native_enum=False), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timestamp_bucket: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
    )
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_tag: Mapped[str | None] = mapped_column(String(64), nullable=True, default="local")
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    related_market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"), nullable=True)
    probability_at_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    related_market_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_after_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_after_6h: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_after_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    signal_direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missing_label_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    simulated_trade: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DuplicateMarketPair(Base):
    __tablename__ = "duplicate_market_pairs"
    __table_args__ = (Index("ix_duplicate_divergence_score", "divergence_score"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_a_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    market_b_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    similarity_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    divergence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DuplicatePairCandidate(Base):
    __tablename__ = "duplicate_pair_candidates"
    __table_args__ = (
        Index("ix_duplicate_pair_candidates_stage", "stage"),
        Index("ix_duplicate_pair_candidates_similarity", "similarity_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_a_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    market_b_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    similarity_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    drop_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RulesAnalysis(Base):
    __tablename__ = "rules_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_flags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LiquidityAnalysis(Base):
    __tablename__ = "liquidity_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_telegram_user_id", "telegram_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    access_level: Mapped[AccessLevel] = mapped_column(
        Enum(AccessLevel, native_enum=False), default=AccessLevel.FREE
    )
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False), default=SubscriptionStatus.INACTIVE
    )
    signals_sent_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_digest_sent: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[AccessLevel] = mapped_column(
        Enum(AccessLevel, native_enum=False), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_signal_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("subscription_plans.id"), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Stage7AgentDecision(Base):
    __tablename__ = "stage7_agent_decisions"
    __table_args__ = (
        UniqueConstraint("input_hash", name="uq_stage7_agent_decisions_input_hash"),
        Index("ix_stage7_agent_decisions_created_at", "created_at"),
        Index("ix_stage7_agent_decisions_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    base_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_adjustment: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    evidence_bundle: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_template_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_snapshot_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stage8Decision(Base):
    __tablename__ = "stage8_decisions"
    __table_args__ = (
        Index("ix_stage8_decisions_created_at", "created_at"),
        Index("ix_stage8_decisions_category", "category"),
        Index("ix_stage8_decisions_execution_action", "execution_action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    stage7_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("stage7_agent_decisions.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_ambiguity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_source_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispute_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edge_after_costs: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    hard_block_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    evidence_bundle: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    kelly_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_proxy_usd_100: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stage8Position(Base):
    __tablename__ = "stage8_positions"
    __table_args__ = (
        Index("ix_stage8_positions_status_category", "status", "category"),
        Index("ix_stage8_positions_status_event_key", "status", "event_key"),
        Index("ix_stage8_positions_market_status", "market_id", "status"),
        Index("ix_stage8_positions_opened_at", "opened_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    stage8_decision_id: Mapped[int | None] = mapped_column(ForeignKey("stage8_decisions.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)  # YES/NO
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    notional_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposure_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Stage10ReplayRow(Base):
    __tablename__ = "stage10_replay_rows"
    __table_args__ = (
        UniqueConstraint("signal_history_id", name="uq_stage10_replay_signal_history"),
        Index("ix_stage10_replay_event_ts", "event_id", "replay_timestamp"),
        Index("ix_stage10_replay_category_ts", "category", "replay_timestamp"),
        Index("ix_stage10_replay_input_hash", "input_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_history_id: Mapped[int] = mapped_column(ForeignKey("signal_history.id"), nullable=False)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_observed_at_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feature_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_action: Mapped[str] = mapped_column(String(32), nullable=False)
    predicted_edge_after_costs_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_components: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolved_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_success_direction_aware: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leakage_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    leakage_reason_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stage11Client(Base):
    __tablename__ = "stage11_clients"
    __table_args__ = (
        UniqueConstraint("code", name="uq_stage11_clients_code"),
        Index("ix_stage11_clients_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    custody_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="CLIENT_SIGNED")
    runtime_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="SHADOW")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Stage11ClientWallet(Base):
    __tablename__ = "stage11_client_wallets"
    __table_args__ = (
        Index("ix_stage11_client_wallets_client_id", "client_id"),
        UniqueConstraint("client_id", "wallet_address", name="uq_stage11_wallet_client_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("stage11_clients.id"), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(256), nullable=False)
    wallet_type: Mapped[str] = mapped_column(String(32), nullable=False, default="POLYGON")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provider_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stage11Order(Base):
    __tablename__ = "stage11_orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_stage11_orders_idempotency_key"),
        Index("ix_stage11_orders_client_created", "client_id", "created_at"),
        Index("ix_stage11_orders_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("stage11_clients.id"), nullable=False)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="POLYMARKET")
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # YES/NO
    size_bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    notional_usd: Mapped[float] = mapped_column(Float, nullable=False)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="stage11_v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    venue_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submit_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    unknown_recovery_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Stage11Fill(Base):
    __tablename__ = "stage11_fills"
    __table_args__ = (
        Index("ix_stage11_fills_client_order", "client_id", "order_id"),
        Index("ix_stage11_fills_filled_at", "filled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("stage11_clients.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("stage11_orders.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_size_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fee_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fill_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stage11ClientPosition(Base):
    __tablename__ = "stage11_client_positions"
    __table_args__ = (
        Index("ix_stage11_positions_client_market", "client_id", "market_id"),
        Index("ix_stage11_positions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("stage11_clients.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    notional_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Stage11TradingAuditEvent(Base):
    __tablename__ = "stage11_trading_audit_events"
    __table_args__ = (
        Index("ix_stage11_audit_client_created", "client_id", "created_at"),
        Index("ix_stage11_audit_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("stage11_clients.id"), nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("stage11_orders.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "market_id", name="uq_watchlist_user_market"),
        Index("ix_watchlist_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserEvent(Base):
    __tablename__ = "user_events"
    __table_args__ = (Index("ix_user_events_user_id", "user_id"), Index("ix_user_events_type", "event_type"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"), nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DryrunPortfolio(Base):
    """Virtual paper-trading portfolio for dry-run simulator."""

    __tablename__ = "dryrun_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    initial_balance_usd: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    current_cash_usd: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    total_realized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_unrealized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DryrunPosition(Base):
    """Single paper-trading position opened by dry-run simulator."""

    __tablename__ = "dryrun_positions"
    __table_args__ = (
        Index("ix_dryrun_positions_portfolio_status", "portfolio_id", "status"),
        Index("ix_dryrun_positions_signal_id", "signal_id"),
        Index("ix_dryrun_positions_market_id", "market_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("dryrun_portfolios.id"), nullable=False)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="POLYMARKET")
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # YES / NO
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional_usd: Mapped[float] = mapped_column(Float, nullable=False)
    shares_count: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN / CLOSED / EXPIRED
    open_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)  # resolved_yes/resolved_no/stop_loss/expired/ai_remove
    entry_kelly_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    resolution_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SignalGenerationStats(Base):
    __tablename__ = "signal_generation_stats"
    __table_args__ = (
        UniqueConstraint("date", "signal_type", "signal_mode", name="uq_signal_generation_stats_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    signal_type: Mapped[SignalType] = mapped_column(Enum(SignalType, native_enum=False), nullable=False)
    signal_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SignalQualityMetrics(Base):
    __tablename__ = "signal_quality_metrics"
    __table_args__ = (Index("ix_quality_date", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    markets_ingested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    markets_with_prob: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    markets_with_rules: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshots_fresh_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pairs_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pairs_filtered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rules_candidates_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    arbitrage_candidates_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signals_by_type: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    signals_by_mode: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    avg_score_by_type: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    zero_move_arbitrage_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    missing_rules_share: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actionable_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    simulated_edge_mean: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    simulated_edge_p10: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    top5_utility_daily: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
