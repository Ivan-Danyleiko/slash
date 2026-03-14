"""add duplicate pair candidates table for broad-strict diagnostics"""

from alembic import op
import sqlalchemy as sa

revision = "0005_duplicate_pair_candidates"
down_revision = "0004_signal_quality_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_pair_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_a_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("market_b_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("similarity_explanation", sa.Text(), nullable=True),
        sa.Column("drop_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_duplicate_pair_candidates_stage", "duplicate_pair_candidates", ["stage"])
    op.create_index("ix_duplicate_pair_candidates_similarity", "duplicate_pair_candidates", ["similarity_score"])


def downgrade() -> None:
    op.drop_index("ix_duplicate_pair_candidates_similarity", table_name="duplicate_pair_candidates")
    op.drop_index("ix_duplicate_pair_candidates_stage", table_name="duplicate_pair_candidates")
    op.drop_table("duplicate_pair_candidates")
