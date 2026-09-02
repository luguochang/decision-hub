"""Add agentic research evidence lineage and dual snapshot metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0017_agentic_research_evidence"
down_revision = "0016_live_observation_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("snapshots") as batch:
        batch.add_column(
            sa.Column("snapshot_type", sa.String(32), nullable=False, server_default="trigger")
        )
        batch.add_column(sa.Column("run_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("parent_snapshot_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch.create_index("ix_snapshots_run_id", ["run_id"])

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("decision_snapshot_id", sa.String(128), nullable=True))

    op.create_table(
        "research_evidence",
        sa.Column("evidence_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, index=True),
        sa.Column("capability_id", sa.String(128), nullable=False, index=True),
        sa.Column("requirement_id", sa.String(128), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, index=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("structured_payload_ref", sa.String(2048), nullable=True),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("research_session_id", sa.String(256), nullable=False, index=True),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("quality", sa.String(32), nullable=False),
        sa.Column("freshness_status", sa.String(32), nullable=False),
        sa.Column("conflict_group", sa.String(128), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("research_evidence")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("decision_snapshot_id")
    with op.batch_alter_table("snapshots") as batch:
        batch.drop_index("ix_snapshots_run_id")
        batch.drop_column("created_at")
        batch.drop_column("generation")
        batch.drop_column("parent_snapshot_id")
        batch.drop_column("run_id")
        batch.drop_column("snapshot_type")
