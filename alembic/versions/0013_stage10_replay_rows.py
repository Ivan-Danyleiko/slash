"""stage10 replay rows table

Revision ID: 0013_stage10_replay_rows
Revises: 0012_stage9_source_quality
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_stage10_replay_rows"
down_revision = "0012_stage9_source_quality"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(str(idx.get("name")) == index_name for idx in indexes)


def upgrade() -> None:
    if not _has_table("stage10_replay_rows"):
        op.create_table(
            "stage10_replay_rows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("signal_history_id", sa.Integer(), sa.ForeignKey("signal_history.id"), nullable=False),
            sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=True),
            sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
            sa.Column("event_id", sa.String(length=128), nullable=False),
            sa.Column("platform", sa.String(length=64), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("replay_timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("feature_observed_at_max", sa.DateTime(timezone=True), nullable=True),
            sa.Column("feature_source_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("features_snapshot", sa.JSON(), nullable=True),
            sa.Column("policy_decision", sa.String(length=32), nullable=False),
            sa.Column("agent_decision", sa.String(length=32), nullable=False),
            sa.Column("execution_action", sa.String(length=32), nullable=False),
            sa.Column("predicted_edge_after_costs_pct", sa.Float(), nullable=True),
            sa.Column("cost_components", sa.JSON(), nullable=True),
            sa.Column("resolved_outcome", sa.String(length=16), nullable=True),
            sa.Column("resolved_success_direction_aware", sa.Boolean(), nullable=True),
            sa.Column("trace_id", sa.String(length=128), nullable=True),
            sa.Column("input_hash", sa.String(length=128), nullable=True),
            sa.Column("model_version", sa.String(length=64), nullable=True),
            sa.Column("leakage_violation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("leakage_reason_codes", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("signal_history_id", name="uq_stage10_replay_signal_history"),
        )

    if not _has_index("stage10_replay_rows", "ix_stage10_replay_event_ts"):
        op.create_index("ix_stage10_replay_event_ts", "stage10_replay_rows", ["event_id", "replay_timestamp"], unique=False)
    if not _has_index("stage10_replay_rows", "ix_stage10_replay_category_ts"):
        op.create_index("ix_stage10_replay_category_ts", "stage10_replay_rows", ["category", "replay_timestamp"], unique=False)
    if not _has_index("stage10_replay_rows", "ix_stage10_replay_input_hash"):
        op.create_index("ix_stage10_replay_input_hash", "stage10_replay_rows", ["input_hash"], unique=False)


def downgrade() -> None:
    if _has_table("stage10_replay_rows"):
        if _has_index("stage10_replay_rows", "ix_stage10_replay_input_hash"):
            op.drop_index("ix_stage10_replay_input_hash", table_name="stage10_replay_rows")
        if _has_index("stage10_replay_rows", "ix_stage10_replay_category_ts"):
            op.drop_index("ix_stage10_replay_category_ts", table_name="stage10_replay_rows")
        if _has_index("stage10_replay_rows", "ix_stage10_replay_event_ts"):
            op.drop_index("ix_stage10_replay_event_ts", table_name="stage10_replay_rows")
        op.drop_table("stage10_replay_rows")
