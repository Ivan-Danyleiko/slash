"""add signal history table for stage5 research"""

from alembic import op
import sqlalchemy as sa

revision = "0006_signal_history_stage5"
down_revision = "0005_duplicate_pair_candidates"
branch_labels = None
depends_on = None


signal_type_enum = sa.Enum(
    "ARBITRAGE_CANDIDATE",
    "DUPLICATE_MARKET",
    "DIVERGENCE",
    "LIQUIDITY_RISK",
    "RULES_RISK",
    "WEIRD_MARKET",
    "WATCHLIST",
    name="signaltype",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "signal_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("signal_type", signal_type_enum, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("related_market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("probability_at_signal", sa.Float(), nullable=True),
        sa.Column("related_market_probability", sa.Float(), nullable=True),
        sa.Column("divergence", sa.Float(), nullable=True),
        sa.Column("liquidity", sa.Float(), nullable=True),
        sa.Column("volume_24h", sa.Float(), nullable=True),
        sa.Column("probability_after_1h", sa.Float(), nullable=True),
        sa.Column("probability_after_6h", sa.Float(), nullable=True),
        sa.Column("probability_after_24h", sa.Float(), nullable=True),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("simulated_trade", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_signal_history_timestamp", "signal_history", ["timestamp"])
    op.create_index("ix_signal_history_signal_type_timestamp", "signal_history", ["signal_type", "timestamp"])
    op.create_index("ix_signal_history_platform_timestamp", "signal_history", ["platform", "timestamp"])
    op.create_index("ix_signal_history_signal_id", "signal_history", ["signal_id"])


def downgrade() -> None:
    op.drop_index("ix_signal_history_signal_id", table_name="signal_history")
    op.drop_index("ix_signal_history_platform_timestamp", table_name="signal_history")
    op.drop_index("ix_signal_history_signal_type_timestamp", table_name="signal_history")
    op.drop_index("ix_signal_history_timestamp", table_name="signal_history")
    op.drop_table("signal_history")
