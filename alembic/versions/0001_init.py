"""initial schema"""

from alembic import op
import sqlalchemy as sa

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("platforms", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(64), nullable=False, unique=True), sa.Column("base_url", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("markets", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("platform_id", sa.Integer(), sa.ForeignKey("platforms.id"), nullable=False), sa.Column("external_market_id", sa.String(128), nullable=False), sa.Column("title", sa.String(512), nullable=False), sa.Column("description", sa.Text()), sa.Column("category", sa.String(128)), sa.Column("url", sa.String(1024)), sa.Column("status", sa.String(64)), sa.Column("probability_yes", sa.Float()), sa.Column("probability_no", sa.Float()), sa.Column("volume_24h", sa.Float()), sa.Column("liquidity_value", sa.Float()), sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("resolution_time", sa.DateTime(timezone=True)), sa.Column("rules_text", sa.Text()), sa.Column("source_payload", sa.JSON()), sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.UniqueConstraint("platform_id", "external_market_id", name="uq_platform_market_external"))
    op.create_index("ix_market_created_at", "markets", ["created_at"])
    op.create_index("ix_market_resolution_time", "markets", ["resolution_time"])
    op.create_table("market_snapshots", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False), sa.Column("probability_yes", sa.Float()), sa.Column("probability_no", sa.Float()), sa.Column("volume_24h", sa.Float()), sa.Column("liquidity_value", sa.Float()), sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("duplicate_market_pairs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_a_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False), sa.Column("market_b_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False), sa.Column("similarity_score", sa.Float(), nullable=False), sa.Column("divergence_score", sa.Float()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("rules_analyses", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False), sa.Column("score", sa.Float(), nullable=False), sa.Column("level", sa.String(32), nullable=False), sa.Column("matched_flags", sa.JSON()), sa.Column("explanation", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("liquidity_analyses", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False), sa.Column("score", sa.Float(), nullable=False), sa.Column("level", sa.String(32), nullable=False), sa.Column("explanation", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("telegram_user_id", sa.String(128), nullable=False, unique=True), sa.Column("username", sa.String(128)), sa.Column("access_level", sa.String(16), nullable=False), sa.Column("subscription_status", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"])
    op.create_table("subscription_plans", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("code", sa.String(16), nullable=False, unique=True), sa.Column("name", sa.String(64), nullable=False), sa.Column("daily_signal_limit", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("user_subscriptions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("plan_id", sa.Integer(), sa.ForeignKey("subscription_plans.id"), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("ends_at", sa.DateTime(timezone=True)))
    op.create_table("signals", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("signal_type", sa.String(64), nullable=False), sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False), sa.Column("related_market_id", sa.Integer(), sa.ForeignKey("markets.id")), sa.Column("title", sa.String(512), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("confidence_score", sa.Float()), sa.Column("liquidity_score", sa.Float()), sa.Column("rules_risk_score", sa.Float()), sa.Column("divergence_score", sa.Float()), sa.Column("metadata_json", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_index("ix_signal_type", "signals", ["signal_type"])
    op.create_table("job_runs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("job_name", sa.String(128), nullable=False), sa.Column("status", sa.String(64), nullable=False), sa.Column("details", sa.JSON()), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("finished_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_index("ix_signal_type", table_name="signals")
    op.drop_table("signals")
    op.drop_table("user_subscriptions")
    op.drop_table("subscription_plans")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
    op.drop_table("liquidity_analyses")
    op.drop_table("rules_analyses")
    op.drop_table("duplicate_market_pairs")
    op.drop_table("market_snapshots")
    op.drop_index("ix_market_resolution_time", table_name="markets")
    op.drop_index("ix_market_created_at", table_name="markets")
    op.drop_table("markets")
    op.drop_table("platforms")
