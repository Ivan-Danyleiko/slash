"""stage18 event canonicalization columns

Revision ID: 0023_stage18_event_canonicalization
Revises: 0022_stage17_tail_v2_fields
Create Date: 2026-03-21
"""

from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0023_stage18_event_canonicalization"
down_revision: Union[str, Sequence[str], None] = "0022_stage17_tail_v2_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c.get("name") == column for c in insp.get_columns(table))

def _has_index(table: str, index: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(i.get("name") == index for i in insp.get_indexes(table))


def upgrade() -> None:
    if not _has_column("markets", "event_group_id"):
        op.add_column("markets", sa.Column("event_group_id", sa.String(64), nullable=True))
    if not _has_index("markets", "ix_market_event_group_id"):
        op.create_index("ix_market_event_group_id", "markets", ["event_group_id"])
    if not _has_column("markets", "event_key_version"):
        op.add_column("markets", sa.Column("event_key_version", sa.Integer(), nullable=True, server_default="0"))
    if not _has_column("markets", "event_key_confidence"):
        op.add_column("markets", sa.Column("event_key_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    if _has_index("markets", "ix_market_event_group_id"):
        op.drop_index("ix_market_event_group_id", table_name="markets")
    for col in ("event_key_confidence", "event_key_version", "event_group_id"):
        if _has_column("markets", col):
            op.drop_column("markets", col)
