"""Performance indexes for hot query paths

Revision ID: 0018_performance_indexes
Revises: 0017_dryrun_simulator
Create Date: 2026-03-18 12:00:00.000000
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0018_performance_indexes"
down_revision: Union[str, None] = "0017_dryrun_simulator"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # signals: filter by type + sort by date (signal engine, dry-run scan)
    op.create_index(
        "ix_signal_type_created_at",
        "signals",
        ["signal_type", "created_at"],
        postgresql_ops={"created_at": "DESC"},
        if_not_exists=True,
    )
    # signals: join with stage7 decisions (dry-run scan inner join)
    op.create_index(
        "ix_signal_market_id_type",
        "signals",
        ["market_id", "signal_type"],
        if_not_exists=True,
    )
    # stage7_agent_decisions: join by signal_id + filter by decision (hot in dry-run)
    op.create_index(
        "ix_stage7_signal_decision",
        "stage7_agent_decisions",
        ["signal_id", "decision"],
        if_not_exists=True,
    )
    # stage7_agent_decisions: latest per signal (ORDER BY created_at DESC)
    op.create_index(
        "ix_stage7_created_at",
        "stage7_agent_decisions",
        ["created_at"],
        postgresql_ops={"created_at": "DESC"},
        if_not_exists=True,
    )
    # market_snapshots: latest snapshot per market (labeling, signal engine)
    op.create_index(
        "ix_market_snapshot_market_fetched",
        "market_snapshots",
        ["market_id", "fetched_at"],
        postgresql_ops={"fetched_at": "DESC"},
        if_not_exists=True,
    )
    # dryrun_positions: open positions by portfolio (hot in every refresh)
    op.create_index(
        "ix_dryrun_positions_portfolio_status_deadline",
        "dryrun_positions",
        ["portfolio_id", "status", "resolution_deadline"],
        if_not_exists=True,
    )
    # markets: filter by resolution_time for near-term queries
    op.create_index(
        "ix_market_resolution_time_platform",
        "markets",
        ["resolution_time", "platform_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_signal_type_created_at", table_name="signals", if_exists=True)
    op.drop_index("ix_signal_market_id_type", table_name="signals", if_exists=True)
    op.drop_index("ix_stage7_signal_decision", table_name="stage7_agent_decisions", if_exists=True)
    op.drop_index("ix_stage7_created_at", table_name="stage7_agent_decisions", if_exists=True)
    op.drop_index("ix_market_snapshot_market_fetched", table_name="market_snapshots", if_exists=True)
    op.drop_index("ix_dryrun_positions_portfolio_status_deadline", table_name="dryrun_positions", if_exists=True)
    op.drop_index("ix_market_resolution_time_platform", table_name="markets", if_exists=True)
