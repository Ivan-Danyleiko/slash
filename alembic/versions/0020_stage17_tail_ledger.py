"""stage17 tail ledger tables

Revision ID: 0020_stage17_tail_ledger
Revises: 0019_stage16_pg_trgm_markets
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_stage17_tail_ledger"
down_revision: Union[str, Sequence[str], None] = "0019_stage16_pg_trgm_markets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stage17_tail_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("tail_category", sa.String(length=50), nullable=False),
        sa.Column("tail_variation", sa.String(length=50), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("mark_price", sa.Float(), nullable=True),
        sa.Column("notional_usd", sa.Float(), nullable=False),
        sa.Column("shares_count", sa.Float(), nullable=True),
        sa.Column("base_rate_prob", sa.Float(), nullable=True),
        sa.Column("market_prob", sa.Float(), nullable=True),
        sa.Column("mispricing_ratio", sa.Float(), nullable=True),
        sa.Column("reason_codes", sa.JSON(), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("peak_mark_price", sa.Float(), nullable=True),
        sa.Column("current_multiplier", sa.Float(), nullable=True),
        sa.Column("realized_pnl_usd", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("realized_multiplier", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl_usd", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stage17_tail_positions_status_opened",
        "stage17_tail_positions",
        ["status", "opened_at"],
    )
    op.create_index(
        "ix_stage17_tail_positions_category_status",
        "stage17_tail_positions",
        ["tail_category", "status"],
    )
    op.create_index(
        "ix_stage17_tail_positions_variation_status",
        "stage17_tail_positions",
        ["tail_variation", "status"],
    )
    op.create_index(
        "ix_stage17_tail_positions_market_status",
        "stage17_tail_positions",
        ["market_id", "status"],
    )
    op.create_index(
        "ix_stage17_tail_positions_signal_id",
        "stage17_tail_positions",
        ["signal_id"],
    )

    op.create_table(
        "stage17_tail_fills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_size_usd", sa.Float(), nullable=False),
        sa.Column("fee_usd", sa.Float(), nullable=False),
        sa.Column("fill_payload", sa.JSON(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["position_id"], ["stage17_tail_positions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stage17_tail_fills_position_id", "stage17_tail_fills", ["position_id"])
    op.create_index("ix_stage17_tail_fills_filled_at", "stage17_tail_fills", ["filled_at"])

    op.create_table(
        "stage17_tail_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("closed_positions", sa.Integer(), nullable=False),
        sa.Column("win_rate_tail", sa.Float(), nullable=False),
        sa.Column("payout_skew", sa.Float(), nullable=False),
        sa.Column("payout_skew_ci_low_80", sa.Float(), nullable=False),
        sa.Column("payout_skew_ci_high_80", sa.Float(), nullable=False),
        sa.Column("top10pct_wins_count", sa.Integer(), nullable=False),
        sa.Column("time_to_resolution_median_hours", sa.Float(), nullable=True),
        sa.Column("avg_win_multiplier", sa.Float(), nullable=True),
        sa.Column("max_concurrent_tail_positions", sa.Integer(), nullable=False),
        sa.Column("tail_budget_total_usd", sa.Float(), nullable=False),
        sa.Column("tail_budget_used_usd", sa.Float(), nullable=False),
        sa.Column("tail_budget_used_pct", sa.Float(), nullable=False),
        sa.Column("by_category", sa.JSON(), nullable=True),
        sa.Column("circuit_breaker_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("circuit_breaker_reason", sa.String(length=128), nullable=True),
        sa.Column("acceptance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_date", name="uq_stage17_tail_reports_date"),
    )
    op.create_index("ix_stage17_tail_reports_report_date", "stage17_tail_reports", ["report_date"])


def downgrade() -> None:
    op.drop_index("ix_stage17_tail_reports_report_date", table_name="stage17_tail_reports")
    op.drop_table("stage17_tail_reports")
    op.drop_index("ix_stage17_tail_fills_filled_at", table_name="stage17_tail_fills")
    op.drop_index("ix_stage17_tail_fills_position_id", table_name="stage17_tail_fills")
    op.drop_table("stage17_tail_fills")
    op.drop_index("ix_stage17_tail_positions_signal_id", table_name="stage17_tail_positions")
    op.drop_index("ix_stage17_tail_positions_market_status", table_name="stage17_tail_positions")
    op.drop_index("ix_stage17_tail_positions_variation_status", table_name="stage17_tail_positions")
    op.drop_index("ix_stage17_tail_positions_category_status", table_name="stage17_tail_positions")
    op.drop_index("ix_stage17_tail_positions_status_opened", table_name="stage17_tail_positions")
    op.drop_table("stage17_tail_positions")
