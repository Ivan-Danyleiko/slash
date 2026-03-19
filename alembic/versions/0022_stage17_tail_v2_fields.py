"""stage17 tail v2 position fields

Revision ID: 0022_stage17_tail_v2_fields
Revises: 0021_stage17_tail_ledger_extra_fields
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_stage17_tail_v2_fields"
down_revision: Union[str, Sequence[str], None] = "0021_stage17_tail_ledger_extra_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = insp.get_columns(table)
    return any(str(c.get("name")) == column for c in cols)


def upgrade() -> None:
    if not _has_column("stage17_tail_positions", "our_prob_entry"):
        op.add_column("stage17_tail_positions", sa.Column("our_prob_entry", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_positions", "koef_entry"):
        op.add_column("stage17_tail_positions", sa.Column("koef_entry", sa.Float(), nullable=True))
    if not _has_column("stage17_tail_positions", "days_to_resolution_entry"):
        op.add_column("stage17_tail_positions", sa.Column("days_to_resolution_entry", sa.Float(), nullable=True))


def downgrade() -> None:
    if _has_column("stage17_tail_positions", "days_to_resolution_entry"):
        op.drop_column("stage17_tail_positions", "days_to_resolution_entry")
    if _has_column("stage17_tail_positions", "koef_entry"):
        op.drop_column("stage17_tail_positions", "koef_entry")
    if _has_column("stage17_tail_positions", "our_prob_entry"):
        op.drop_column("stage17_tail_positions", "our_prob_entry")
