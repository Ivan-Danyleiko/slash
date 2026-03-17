"""dryrun paper trading simulator tables

Revision ID: 0017_dryrun_simulator
Revises: 0016_market_fetched_at_index
Create Date: 2026-03-17 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_dryrun_simulator"
down_revision: Union[str, Sequence[str], None] = "0016_market_fetched_at_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dryrun_portfolios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("initial_balance_usd", sa.Float(), nullable=False),
        sa.Column("current_cash_usd", sa.Float(), nullable=False),
        sa.Column("total_realized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("total_unrealized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "dryrun_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("mark_price", sa.Float(), nullable=True),
        sa.Column("notional_usd", sa.Float(), nullable=False),
        sa.Column("shares_count", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("open_reason", sa.String(length=128), nullable=True),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column("entry_kelly_fraction", sa.Float(), nullable=True),
        sa.Column("entry_ev_pct", sa.Float(), nullable=True),
        sa.Column("realized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("resolution_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["dryrun_portfolios.id"]),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dryrun_positions_portfolio_status", "dryrun_positions", ["portfolio_id", "status"])
    op.create_index("ix_dryrun_positions_signal_id", "dryrun_positions", ["signal_id"])
    op.create_index("ix_dryrun_positions_market_id", "dryrun_positions", ["market_id"])


def downgrade() -> None:
    op.drop_index("ix_dryrun_positions_market_id", table_name="dryrun_positions")
    op.drop_index("ix_dryrun_positions_signal_id", table_name="dryrun_positions")
    op.drop_index("ix_dryrun_positions_portfolio_status", table_name="dryrun_positions")
    op.drop_table("dryrun_positions")
    op.drop_table("dryrun_portfolios")
