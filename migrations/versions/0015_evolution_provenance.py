"""Persist reproducible evaluation and experience provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0015_evolution_provenance"
down_revision = "0014_evolution_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("experiments") as batch:
        batch.add_column(
            sa.Column(
                "randomness_policy",
                sa.String(32),
                nullable=False,
                server_default="deterministic",
            )
        )
        batch.add_column(
            sa.Column("deadline_seconds", sa.Integer(), nullable=False, server_default="300")
        )
        batch.add_column(sa.Column("max_cost_usd", sa.Float(), nullable=True))

    with op.batch_alter_table("experiment_results") as batch:
        batch.add_column(
            sa.Column("raw_artifact_refs_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column(
                "scorer_version",
                sa.String(64),
                nullable=False,
                server_default="legacy.v0",
            )
        )
    op.execute(
        sa.text(
            "UPDATE experiment_results "
            "SET raw_artifact_refs_json = '[\"legacy:' || result_id || '\"]' "
            "WHERE raw_artifact_refs_json = '[]'"
        )
    )

    with op.batch_alter_table("failure_patterns") as batch:
        batch.add_column(
            sa.Column("source_refs_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("root_cause_hypothesis", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("remediation_refs_json", sa.Text(), nullable=False, server_default="[]")
        )
    op.execute(
        sa.text(
            "UPDATE failure_patterns "
            "SET source_refs_json = '[\"legacy:failure_pattern:' || pattern_id || '\"]' "
            "WHERE source_refs_json = '[]'"
        )
    )

    with op.batch_alter_table("experiences") as batch:
        batch.add_column(
            sa.Column("evaluation_refs_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE experiences "
            "SET evaluation_refs_json = '[\"legacy:experience:' || experience_id || '\"]' "
            "WHERE evaluation_refs_json = '[]'"
        )
    )
    op.execute(
        sa.text("UPDATE experiences SET available_at = created_at WHERE available_at IS NULL")
    )
    with op.batch_alter_table("experiences") as batch:
        batch.alter_column("available_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("experiences") as batch:
        batch.drop_column("available_at")
        batch.drop_column("evaluation_refs_json")
    with op.batch_alter_table("failure_patterns") as batch:
        batch.drop_column("remediation_refs_json")
        batch.drop_column("root_cause_hypothesis")
        batch.drop_column("source_refs_json")
    with op.batch_alter_table("experiment_results") as batch:
        batch.drop_column("scorer_version")
        batch.drop_column("raw_artifact_refs_json")
    with op.batch_alter_table("experiments") as batch:
        batch.drop_column("max_cost_usd")
        batch.drop_column("deadline_seconds")
        batch.drop_column("randomness_policy")
