"""add resolution labeling columns to signal_history"""

from alembic import op
import sqlalchemy as sa


revision = "0008_signal_hist_res_labels"
down_revision = "0007_signal_hist_fk_soften"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signal_history", sa.Column("resolved_probability", sa.Float(), nullable=True))
    op.add_column("signal_history", sa.Column("resolved_success", sa.Boolean(), nullable=True))
    op.add_column("signal_history", sa.Column("resolution_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("signal_history", "resolution_checked_at")
    op.drop_column("signal_history", "resolved_success")
    op.drop_column("signal_history", "resolved_probability")
