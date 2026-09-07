"""Add append-only run-level research value evaluations."""

import sqlalchemy as sa
from alembic import op

revision = "0030_research_value_evaluations"
down_revision = "0029_event_watches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_value_evaluations",
        sa.Column("evaluation_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("artifact_id", sa.String(128), nullable=True),
        sa.Column("evaluation_version", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "evaluation_version",
            name="uq_research_value_evaluation_version",
        ),
        sa.UniqueConstraint("payload_hash", name="uq_research_value_payload_hash"),
    )
    op.create_index(
        "ix_research_value_evaluations_run_id",
        "research_value_evaluations",
        ["run_id"],
    )
    op.create_index(
        "ix_research_value_evaluations_artifact_id",
        "research_value_evaluations",
        ["artifact_id"],
    )
    op.create_index(
        "ix_research_value_evaluations_evaluated_at",
        "research_value_evaluations",
        ["evaluated_at"],
    )


def downgrade() -> None:
    op.drop_table("research_value_evaluations")
