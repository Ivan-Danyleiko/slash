"""make signal_history.signal_id nullable with on delete set null"""

from alembic import op
import sqlalchemy as sa

revision = "0007_signal_hist_fk_soften"
down_revision = "0006_signal_history_stage5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("signal_history_signal_id_fkey", "signal_history", type_="foreignkey")
    op.alter_column("signal_history", "signal_id", existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key(
        "signal_history_signal_id_fkey",
        "signal_history",
        "signals",
        ["signal_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("signal_history_signal_id_fkey", "signal_history", type_="foreignkey")
    op.alter_column("signal_history", "signal_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "signal_history_signal_id_fkey",
        "signal_history",
        "signals",
        ["signal_id"],
        ["id"],
    )
