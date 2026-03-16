"""add index on market_snapshots(market_id, fetched_at) for labeling queries

Revision ID: 0015_market_snapshots_index
Revises: 0014_stage11_trading_core
Create Date: 2026-03-16 22:00:00.000000
"""

from alembic import op

revision = "0015_market_snapshots_index"
down_revision = "0014_stage11_trading_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_market_snapshots_market_id_fetched_at",
        "market_snapshots",
        ["market_id", "fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_snapshots_market_id_fetched_at", table_name="market_snapshots")
