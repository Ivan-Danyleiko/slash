"""add backfill fields and idempotent key for signal_history"""

from alembic import op
import sqlalchemy as sa


revision = "0009_signal_hist_backfill"
down_revision = "0008_signal_hist_res_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signal_history", sa.Column("timestamp_bucket", sa.DateTime(timezone=True), nullable=True))
    op.add_column("signal_history", sa.Column("source_tag", sa.String(length=64), nullable=True))
    op.add_column("signal_history", sa.Column("missing_label_reason", sa.String(length=128), nullable=True))

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("UPDATE signal_history SET timestamp_bucket = date_trunc('hour', timestamp) WHERE timestamp_bucket IS NULL")
    elif dialect == "sqlite":
        op.execute(
            """
            UPDATE signal_history
            SET timestamp_bucket = strftime('%Y-%m-%d %H:00:00', timestamp)
            WHERE timestamp_bucket IS NULL
            """
        )
    else:
        op.execute("UPDATE signal_history SET timestamp_bucket = timestamp WHERE timestamp_bucket IS NULL")

    op.execute("UPDATE signal_history SET source_tag = 'local' WHERE source_tag IS NULL")

    # Keep only the oldest row within the same idempotent group before enforcing uniqueness.
    op.execute(
        """
        DELETE FROM signal_history
        WHERE id IN (
            SELECT id FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            COALESCE(platform, ''),
                            market_id,
                            COALESCE(related_market_id, -1),
                            signal_type,
                            timestamp_bucket
                        ORDER BY id
                    ) AS rn
                FROM signal_history
            ) ranked
            WHERE rn > 1
        )
        """
    )

    with op.batch_alter_table("signal_history") as batch_op:
        batch_op.alter_column("timestamp_bucket", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.create_index("ix_signal_history_source_tag", ["source_tag"], unique=False)
        batch_op.create_unique_constraint(
            "uq_signal_history_idempotent",
            ["platform", "market_id", "related_market_id", "signal_type", "timestamp_bucket"],
        )


def downgrade() -> None:
    with op.batch_alter_table("signal_history") as batch_op:
        batch_op.drop_constraint("uq_signal_history_idempotent", type_="unique")
        batch_op.drop_index("ix_signal_history_source_tag")
        batch_op.drop_column("missing_label_reason")
        batch_op.drop_column("source_tag")
        batch_op.drop_column("timestamp_bucket")
