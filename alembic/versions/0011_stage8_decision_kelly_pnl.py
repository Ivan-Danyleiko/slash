"""add kelly and pnl proxy fields to stage8 decisions"""

from alembic import op
import sqlalchemy as sa


revision = "0011_stage8_kelly_pnl"
down_revision = "0010_stage8_decisions"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = inspector.get_columns(table_name)
    return any(str(col.get("name")) == column_name for col in cols)


def upgrade() -> None:
    if not _has_column("stage8_decisions", "kelly_fraction"):
        op.add_column("stage8_decisions", sa.Column("kelly_fraction", sa.Float(), nullable=True))
    if not _has_column("stage8_decisions", "pnl_proxy_usd_100"):
        op.add_column("stage8_decisions", sa.Column("pnl_proxy_usd_100", sa.Float(), nullable=True))


def downgrade() -> None:
    if _has_column("stage8_decisions", "pnl_proxy_usd_100"):
        op.drop_column("stage8_decisions", "pnl_proxy_usd_100")
    if _has_column("stage8_decisions", "kelly_fraction"):
        op.drop_column("stage8_decisions", "kelly_fraction")
