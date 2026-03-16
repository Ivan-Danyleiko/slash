"""add index on markets(fetched_at) for incremental rules analysis

Revision ID: 0016_market_fetched_at_index
Revises: 0015_market_snapshots_index
Create Date: 2026-03-16 23:00:00.000000
"""

from alembic import op

revision = "0016_market_fetched_at_index"
down_revision = "0015_market_snapshots_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_market_fetched_at", "markets", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_market_fetched_at", table_name="markets")
