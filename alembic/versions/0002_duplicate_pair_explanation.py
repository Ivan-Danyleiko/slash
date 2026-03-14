"""add duplicate pair explanation and divergence index"""

from alembic import op
import sqlalchemy as sa

revision = "0002_duplicate_pair_explanation"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("duplicate_market_pairs", sa.Column("similarity_explanation", sa.Text(), nullable=True))
    op.create_index("ix_duplicate_divergence_score", "duplicate_market_pairs", ["divergence_score"])


def downgrade() -> None:
    op.drop_index("ix_duplicate_divergence_score", table_name="duplicate_market_pairs")
    op.drop_column("duplicate_market_pairs", "similarity_explanation")
