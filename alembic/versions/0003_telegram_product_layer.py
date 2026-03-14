"""telegram product layer tables and user counters"""

from alembic import op
import sqlalchemy as sa

revision = "0003_telegram_product_layer"
down_revision = "0002_duplicate_pair_explanation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("signals_sent_today", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("last_digest_sent", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "market_id", name="uq_watchlist_user_market"),
    )
    op.create_index("ix_watchlist_user_id", "watchlist_items", ["user_id"])

    op.create_table(
        "user_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_user_events_user_id", "user_events", ["user_id"])
    op.create_index("ix_user_events_type", "user_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_user_events_type", table_name="user_events")
    op.drop_index("ix_user_events_user_id", table_name="user_events")
    op.drop_table("user_events")

    op.drop_index("ix_watchlist_user_id", table_name="watchlist_items")
    op.drop_table("watchlist_items")

    op.drop_column("users", "last_digest_sent")
    op.drop_column("users", "signals_sent_today")
