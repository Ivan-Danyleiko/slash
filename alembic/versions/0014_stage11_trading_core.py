"""stage11 trading core tables

Revision ID: 0014_stage11_trading_core
Revises: 0013_stage10_replay_rows
Create Date: 2026-03-16 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0014_stage11_trading_core"
down_revision: Union[str, Sequence[str], None] = "0013_stage10_replay_rows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stage11_clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("custody_mode", sa.String(length=32), nullable=False),
        sa.Column("runtime_mode", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("risk_profile", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_stage11_clients_code"),
    )
    op.create_index("ix_stage11_clients_active", "stage11_clients", ["is_active"], unique=False)

    op.create_table(
        "stage11_client_wallets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("wallet_address", sa.String(length=256), nullable=False),
        sa.Column("wallet_type", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("provider_hint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["stage11_clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "wallet_address", name="uq_stage11_wallet_client_address"),
    )
    op.create_index("ix_stage11_client_wallets_client_id", "stage11_client_wallets", ["client_id"], unique=False)

    op.create_table(
        "stage11_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("size_bucket", sa.String(length=16), nullable=False),
        sa.Column("notional_usd", sa.Float(), nullable=False),
        sa.Column("requested_price", sa.Float(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("venue_order_id", sa.String(length=128), nullable=True),
        sa.Column("submit_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=256), nullable=True),
        sa.Column("unknown_recovery_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["stage11_clients.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_stage11_orders_idempotency_key"),
    )
    op.create_index("ix_stage11_orders_client_created", "stage11_orders", ["client_id", "created_at"], unique=False)
    op.create_index("ix_stage11_orders_status_created", "stage11_orders", ["status", "created_at"], unique=False)

    op.create_table(
        "stage11_fills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_size_usd", sa.Float(), nullable=False),
        sa.Column("fee_usd", sa.Float(), nullable=False),
        sa.Column("realized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("fill_payload", sa.JSON(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["stage11_clients.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["stage11_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stage11_fills_client_order", "stage11_fills", ["client_id", "order_id"], unique=False)
    op.create_index("ix_stage11_fills_filled_at", "stage11_fills", ["filled_at"], unique=False)

    op.create_table(
        "stage11_client_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notional_usd", sa.Float(), nullable=False),
        sa.Column("avg_entry_price", sa.Float(), nullable=True),
        sa.Column("mark_price", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("realized_pnl_usd", sa.Float(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["stage11_clients.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stage11_positions_client_market", "stage11_client_positions", ["client_id", "market_id"], unique=False)
    op.create_index("ix_stage11_positions_status", "stage11_client_positions", ["status"], unique=False)

    op.create_table(
        "stage11_trading_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("payload_checksum", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["stage11_clients.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["stage11_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stage11_audit_client_created",
        "stage11_trading_audit_events",
        ["client_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_stage11_audit_event_type", "stage11_trading_audit_events", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stage11_audit_event_type", table_name="stage11_trading_audit_events")
    op.drop_index("ix_stage11_audit_client_created", table_name="stage11_trading_audit_events")
    op.drop_table("stage11_trading_audit_events")

    op.drop_index("ix_stage11_positions_status", table_name="stage11_client_positions")
    op.drop_index("ix_stage11_positions_client_market", table_name="stage11_client_positions")
    op.drop_table("stage11_client_positions")

    op.drop_index("ix_stage11_fills_filled_at", table_name="stage11_fills")
    op.drop_index("ix_stage11_fills_client_order", table_name="stage11_fills")
    op.drop_table("stage11_fills")

    op.drop_index("ix_stage11_orders_status_created", table_name="stage11_orders")
    op.drop_index("ix_stage11_orders_client_created", table_name="stage11_orders")
    op.drop_table("stage11_orders")

    op.drop_index("ix_stage11_client_wallets_client_id", table_name="stage11_client_wallets")
    op.drop_table("stage11_client_wallets")

    op.drop_index("ix_stage11_clients_active", table_name="stage11_clients")
    op.drop_table("stage11_clients")
