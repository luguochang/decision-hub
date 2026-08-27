"""Persist experiment provenance and reusable experience assets."""

import sqlalchemy as sa
from alembic import op

revision = "0014_evolution_metadata"
down_revision = "0013_promotion_invariants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_versions") as batch:
        batch.add_column(sa.Column("content_ref", sa.String(512), nullable=True))
        batch.add_column(
            sa.Column("source", sa.String(64), nullable=False, server_default="owner")
        )
    with op.batch_alter_table("experiments") as batch:
        batch.add_column(
            sa.Column(
                "strategy_version", sa.String(64), nullable=False, server_default="baseline.v1"
            )
        )
        batch.add_column(
            sa.Column("runtime_id", sa.String(64), nullable=False, server_default="replay")
        )
        batch.add_column(
            sa.Column(
                "runtime_version", sa.String(64), nullable=False, server_default="replay.v1"
            )
        )
        batch.add_column(sa.Column("provider_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("model", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column(
                "schema_version", sa.String(64), nullable=False, server_default="experiment.v1"
            )
        )
        batch.add_column(sa.Column("random_seed", sa.Integer(), nullable=True))
    with op.batch_alter_table("experiment_results") as batch:
        batch.add_column(sa.Column("stage", sa.String(32), nullable=True))
        batch.add_column(
            sa.Column("failure_counts_json", sa.Text(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("evidence_coverage", sa.Float(), nullable=True))
        batch.add_column(sa.Column("directional_accuracy", sa.Float(), nullable=True))
    op.create_table(
        "experiences",
        sa.Column("experience_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("domain_pack_ref", sa.String(128), nullable=False, index=True),
        sa.Column("event_family", sa.String(128), nullable=False, index=True),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("applicable_conditions_json", sa.Text(), nullable=False),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False),
        sa.Column("outcome_refs_json", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # 0013 intentionally allowed NULLs for historical rows.  Give them stable,
    # non-secret identifiers so the new audit view can render old decisions too.
    op.execute(
        sa.text(
            "UPDATE promotion_decisions "
            "SET request_id = 'legacy:' || decision_id "
            "WHERE request_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE promotion_decisions SET domain_pack_ref = 'legacy' "
            "WHERE domain_pack_ref IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_table("experiences")
    with op.batch_alter_table("experiment_results") as batch:
        batch.drop_column("directional_accuracy")
        batch.drop_column("evidence_coverage")
        batch.drop_column("failure_counts_json")
        batch.drop_column("stage")
    with op.batch_alter_table("experiments") as batch:
        batch.drop_column("random_seed")
        batch.drop_column("schema_version")
        batch.drop_column("model")
        batch.drop_column("provider_id")
        batch.drop_column("runtime_version")
        batch.drop_column("runtime_id")
        batch.drop_column("strategy_version")
    with op.batch_alter_table("candidate_versions") as batch:
        batch.drop_column("source")
        batch.drop_column("content_ref")
