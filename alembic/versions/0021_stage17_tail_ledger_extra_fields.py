"""stage17 tail ledger extra fields (idempotent backfill)

Revision ID: 0021_stage17_tail_ledger_extra_fields
Revises: 0020_stage17_tail_ledger
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_stage17_tail_ledger_extra_fields"
down_revision: Union[str, Sequence[str], None] = "0020_stage17_tail_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = inspector.get_columns(table)
    return any(str(c.get("name")) == column for c in cols)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "")).lower()
    # stage17_tail_positions extras
    if not _has_column("stage17_tail_positions", "shares_count"):
        op.add_column("stage17_tail_positions", sa.Column("shares_count", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_positions", "peak_mark_price"):
        op.add_column("stage17_tail_positions", sa.Column("peak_mark_price", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_positions", "current_multiplier"):
        op.add_column("stage17_tail_positions", sa.Column("current_multiplier", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_positions", "realized_multiplier"):
        op.add_column("stage17_tail_positions", sa.Column("realized_multiplier", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_positions", "resolution_deadline"):
        op.add_column(
            "stage17_tail_positions",
            sa.Column("resolution_deadline", sa.DateTime(timezone=True), nullable=True),
        )
    # Ensure NOT NULL pnl fields have server defaults for raw SQL inserts/backfills.
    if dialect == "postgresql":
        op.execute("ALTER TABLE stage17_tail_positions ALTER COLUMN realized_pnl_usd SET DEFAULT 0.0")
        op.execute("ALTER TABLE stage17_tail_positions ALTER COLUMN unrealized_pnl_usd SET DEFAULT 0.0")

    # stage17_tail_reports extras
    if not _has_column("stage17_tail_reports", "avg_win_multiplier"):
        op.add_column("stage17_tail_reports", sa.Column("avg_win_multiplier", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_reports", "circuit_breaker_active"):
        op.add_column(
            "stage17_tail_reports",
            sa.Column("circuit_breaker_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if not _has_column("stage17_tail_reports", "circuit_breaker_reason"):
        op.add_column("stage17_tail_reports", sa.Column("circuit_breaker_reason", sa.String(length=128), nullable=True))


def downgrade() -> None:
    # Best-effort reversible in reverse order; only drop when present.
    if _has_column("stage17_tail_reports", "circuit_breaker_reason"):
        op.drop_column("stage17_tail_reports", "circuit_breaker_reason")
    if _has_column("stage17_tail_reports", "circuit_breaker_active"):
        op.drop_column("stage17_tail_reports", "circuit_breaker_active")
    if _has_column("stage17_tail_reports", "avg_win_multiplier"):
        op.drop_column("stage17_tail_reports", "avg_win_multiplier")

    if _has_column("stage17_tail_positions", "resolution_deadline"):
        op.drop_column("stage17_tail_positions", "resolution_deadline")
    if _has_column("stage17_tail_positions", "realized_multiplier"):
        op.drop_column("stage17_tail_positions", "realized_multiplier")
    if _has_column("stage17_tail_positions", "current_multiplier"):
        op.drop_column("stage17_tail_positions", "current_multiplier")
    if _has_column("stage17_tail_positions", "peak_mark_price"):
        op.drop_column("stage17_tail_positions", "peak_mark_price")
    if _has_column("stage17_tail_positions", "shares_count"):
        op.drop_column("stage17_tail_positions", "shares_count")
