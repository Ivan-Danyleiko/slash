"""add stage8 decisions and positions tables"""

from alembic import op
import sqlalchemy as sa


revision = "0010_stage8_decisions"
down_revision = "0009_signal_hist_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect in {"postgresql", "sqlite"}:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS stage7_agent_decisions (
                id SERIAL PRIMARY KEY,
                signal_id INTEGER NOT NULL REFERENCES signals(id),
                input_hash VARCHAR(128) NOT NULL,
                base_decision VARCHAR(32) NOT NULL,
                decision VARCHAR(32) NOT NULL,
                confidence_adjustment FLOAT,
                reason_codes JSON,
                evidence_bundle JSON,
                model_id VARCHAR(128),
                model_version VARCHAR(64),
                prompt_template_version VARCHAR(64),
                provider VARCHAR(64),
                provider_fingerprint VARCHAR(128),
                tool_snapshot_version VARCHAR(64),
                llm_cost_usd FLOAT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
            );
            """
        )
        if dialect == "postgresql":
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_stage7_agent_decisions_input_hash ON stage7_agent_decisions (input_hash);"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_stage7_agent_decisions_created_at ON stage7_agent_decisions (created_at);"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_stage7_agent_decisions_provider ON stage7_agent_decisions (provider);"
            )
    else:
        inspector = sa.inspect(bind)
        if not inspector.has_table("stage7_agent_decisions"):
            op.create_table(
                "stage7_agent_decisions",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=False),
                sa.Column("input_hash", sa.String(length=128), nullable=False),
                sa.Column("base_decision", sa.String(length=32), nullable=False),
                sa.Column("decision", sa.String(length=32), nullable=False),
                sa.Column("confidence_adjustment", sa.Float(), nullable=True),
                sa.Column("reason_codes", sa.JSON(), nullable=True),
                sa.Column("evidence_bundle", sa.JSON(), nullable=True),
                sa.Column("model_id", sa.String(length=128), nullable=True),
                sa.Column("model_version", sa.String(length=64), nullable=True),
                sa.Column("prompt_template_version", sa.String(length=64), nullable=True),
                sa.Column("provider", sa.String(length=64), nullable=True),
                sa.Column("provider_fingerprint", sa.String(length=128), nullable=True),
                sa.Column("tool_snapshot_version", sa.String(length=64), nullable=True),
                sa.Column("llm_cost_usd", sa.Float(), nullable=True),
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            )
            op.create_index(
                "ix_stage7_agent_decisions_created_at",
                "stage7_agent_decisions",
                ["created_at"],
                unique=False,
            )
            op.create_index(
                "ix_stage7_agent_decisions_provider",
                "stage7_agent_decisions",
                ["provider"],
                unique=False,
            )
            op.create_unique_constraint(
                "uq_stage7_agent_decisions_input_hash",
                "stage7_agent_decisions",
                ["input_hash"],
            )

    op.create_table(
        "stage8_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=False),
        sa.Column(
            "stage7_decision_id",
            sa.Integer(),
            sa.ForeignKey("stage7_agent_decisions.id"),
            nullable=True,
        ),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("category_confidence", sa.Float(), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("rules_ambiguity_score", sa.Float(), nullable=True),
        sa.Column("resolution_source_confidence", sa.Float(), nullable=True),
        sa.Column("dispute_risk_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("edge_after_costs", sa.Float(), nullable=True),
        sa.Column("base_decision", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("execution_action", sa.String(length=32), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=True),
        sa.Column("hard_block_reason", sa.String(length=256), nullable=True),
        sa.Column("evidence_bundle", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stage8_decisions_created_at", "stage8_decisions", ["created_at"], unique=False)
    op.create_index("ix_stage8_decisions_category", "stage8_decisions", ["category"], unique=False)
    op.create_index("ix_stage8_decisions_execution_action", "stage8_decisions", ["execution_action"], unique=False)

    op.create_table(
        "stage8_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=False),
        sa.Column(
            "stage8_decision_id",
            sa.Integer(),
            sa.ForeignKey("stage8_decisions.id"),
            nullable=True,
        ),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("notional_usd", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("exposure_weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_stage8_positions_status_category",
        "stage8_positions",
        ["status", "category"],
        unique=False,
    )
    op.create_index(
        "ix_stage8_positions_status_event_key",
        "stage8_positions",
        ["status", "event_key"],
        unique=False,
    )
    op.create_index(
        "ix_stage8_positions_market_status",
        "stage8_positions",
        ["market_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_stage8_positions_opened_at",
        "stage8_positions",
        ["opened_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.drop_index("ix_stage8_positions_opened_at", table_name="stage8_positions")
    op.drop_index("ix_stage8_positions_market_status", table_name="stage8_positions")
    op.drop_index("ix_stage8_positions_status_event_key", table_name="stage8_positions")
    op.drop_index("ix_stage8_positions_status_category", table_name="stage8_positions")
    op.drop_table("stage8_positions")
    op.drop_index("ix_stage8_decisions_execution_action", table_name="stage8_decisions")
    op.drop_index("ix_stage8_decisions_category", table_name="stage8_decisions")
    op.drop_index("ix_stage8_decisions_created_at", table_name="stage8_decisions")
    op.drop_table("stage8_decisions")

    if inspector.has_table("stage7_agent_decisions"):
        op.drop_constraint("uq_stage7_agent_decisions_input_hash", "stage7_agent_decisions", type_="unique")
        op.drop_index("ix_stage7_agent_decisions_provider", table_name="stage7_agent_decisions")
        op.drop_index("ix_stage7_agent_decisions_created_at", table_name="stage7_agent_decisions")
        op.drop_table("stage7_agent_decisions")
