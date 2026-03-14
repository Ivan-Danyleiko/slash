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
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
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
