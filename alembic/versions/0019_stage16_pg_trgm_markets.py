"""stage16 pg_trgm support for historical rag

Revision ID: 0019_stage16_pg_trgm_markets
Revises: 0018_performance_indexes
Create Date: 2026-03-19
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0019_stage16_pg_trgm_markets"
down_revision = "0018_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_markets_title_trgm "
        "ON markets USING gin (title gin_trgm_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_markets_title_trgm")

