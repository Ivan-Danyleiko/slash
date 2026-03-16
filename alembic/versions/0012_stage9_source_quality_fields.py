"""stage9 source quality and direction fields

Revision ID: 0012_stage9_source_quality
Revises: 0011_stage8_kelly_pnl
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_stage9_source_quality"
down_revision = "0011_stage8_kelly_pnl"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = inspector.get_columns(table_name)
    return any(str(col.get("name")) == column_name for col in cols)


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(str(idx.get("name")) == index_name for idx in indexes)


def upgrade() -> None:
    # markets
    if not _has_column("markets", "spread_cents"):
        op.add_column("markets", sa.Column("spread_cents", sa.Float(), nullable=True))
    if not _has_column("markets", "best_bid_yes"):
        op.add_column("markets", sa.Column("best_bid_yes", sa.Float(), nullable=True))
    if not _has_column("markets", "best_ask_yes"):
        op.add_column("markets", sa.Column("best_ask_yes", sa.Float(), nullable=True))
    if not _has_column("markets", "execution_source"):
        op.add_column("markets", sa.Column("execution_source", sa.String(length=32), nullable=True))
    if not _has_column("markets", "is_neg_risk"):
        op.add_column("markets", sa.Column("is_neg_risk", sa.Boolean(), nullable=True))
    if not _has_column("markets", "open_interest"):
        op.add_column("markets", sa.Column("open_interest", sa.Float(), nullable=True))
    if not _has_column("markets", "notional_value_dollars"):
        op.add_column("markets", sa.Column("notional_value_dollars", sa.Float(), nullable=True))
    if not _has_column("markets", "previous_yes_bid"):
        op.add_column("markets", sa.Column("previous_yes_bid", sa.Float(), nullable=True))
    if not _has_index("markets", "ix_market_execution_source"):
        op.create_index("ix_market_execution_source", "markets", ["execution_source"], unique=False)
    if not _has_index("markets", "ix_market_neg_risk"):
        op.create_index("ix_market_neg_risk", "markets", ["is_neg_risk"], unique=False)

    # signals
    if not _has_column("signals", "signal_direction"):
        op.add_column("signals", sa.Column("signal_direction", sa.String(length=8), nullable=True))
    if not _has_index("signals", "ix_signal_direction"):
        op.create_index("ix_signal_direction", "signals", ["signal_direction"], unique=False)

    # signal_history
    if not _has_column("signal_history", "signal_direction"):
        op.add_column("signal_history", sa.Column("signal_direction", sa.String(length=8), nullable=True))
    if not _has_column("signal_history", "resolved_outcome"):
        op.add_column("signal_history", sa.Column("resolved_outcome", sa.String(length=16), nullable=True))
    if not _has_index("signal_history", "ix_signal_history_direction"):
        op.create_index("ix_signal_history_direction", "signal_history", ["signal_direction"], unique=False)


def downgrade() -> None:
    if _has_index("signal_history", "ix_signal_history_direction"):
        op.drop_index("ix_signal_history_direction", table_name="signal_history")
    if _has_column("signal_history", "resolved_outcome"):
        op.drop_column("signal_history", "resolved_outcome")
    if _has_column("signal_history", "signal_direction"):
        op.drop_column("signal_history", "signal_direction")

    if _has_index("signals", "ix_signal_direction"):
        op.drop_index("ix_signal_direction", table_name="signals")
    if _has_column("signals", "signal_direction"):
        op.drop_column("signals", "signal_direction")

    if _has_index("markets", "ix_market_neg_risk"):
        op.drop_index("ix_market_neg_risk", table_name="markets")
    if _has_index("markets", "ix_market_execution_source"):
        op.drop_index("ix_market_execution_source", table_name="markets")
    if _has_column("markets", "previous_yes_bid"):
        op.drop_column("markets", "previous_yes_bid")
    if _has_column("markets", "notional_value_dollars"):
        op.drop_column("markets", "notional_value_dollars")
    if _has_column("markets", "open_interest"):
        op.drop_column("markets", "open_interest")
    if _has_column("markets", "is_neg_risk"):
        op.drop_column("markets", "is_neg_risk")
    if _has_column("markets", "execution_source"):
        op.drop_column("markets", "execution_source")
    if _has_column("markets", "best_ask_yes"):
        op.drop_column("markets", "best_ask_yes")
    if _has_column("markets", "best_bid_yes"):
        op.drop_column("markets", "best_bid_yes")
    if _has_column("markets", "spread_cents"):
        op.drop_column("markets", "spread_cents")
