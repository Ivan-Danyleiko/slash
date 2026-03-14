"""phase1 signal quality fields and metrics tables"""

from alembic import op
import sqlalchemy as sa

revision = "0004_signal_quality_phase1"
down_revision = "0003_telegram_product_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("signal_mode", sa.String(length=64), nullable=True))
    op.add_column("signals", sa.Column("score_breakdown_json", sa.JSON(), nullable=True))
    op.add_column("signals", sa.Column("drop_reason", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("execution_analysis", sa.JSON(), nullable=True))
    op.add_column("signals", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "signal_generation_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("signal_type", sa.Enum(
            "ARBITRAGE_CANDIDATE",
            "DUPLICATE_MARKET",
            "DIVERGENCE",
            "LIQUIDITY_RISK",
            "RULES_RISK",
            "WEIRD_MARKET",
            "WATCHLIST",
            name="signaltype",
            native_enum=False,
        ), nullable=False),
        sa.Column("signal_mode", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("date", "signal_type", "signal_mode", name="uq_signal_generation_stats_key"),
    )

    op.create_table(
        "signal_quality_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("markets_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("markets_with_prob", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("markets_with_rules", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshots_fresh_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pairs_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pairs_filtered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_candidates_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("arbitrage_candidates_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signals_by_type", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("signals_by_mode", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("avg_score_by_type", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("zero_move_arbitrage_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("missing_rules_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("actionable_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("simulated_edge_mean", sa.Float(), nullable=False, server_default="0"),
        sa.Column("simulated_edge_p10", sa.Float(), nullable=False, server_default="0"),
        sa.Column("top5_utility_daily", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_quality_date", "signal_quality_metrics", ["date"])


def downgrade() -> None:
    op.drop_index("ix_quality_date", table_name="signal_quality_metrics")
    op.drop_table("signal_quality_metrics")
    op.drop_table("signal_generation_stats")

    op.drop_column("signals", "updated_at")
    op.drop_column("signals", "execution_analysis")
    op.drop_column("signals", "drop_reason")
    op.drop_column("signals", "score_breakdown_json")
    op.drop_column("signals", "signal_mode")
