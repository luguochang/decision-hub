"""Add immutable evaluation, candidate and owner promotion assets."""

import sqlalchemy as sa
from alembic import op

revision = "0012_evolution_assets"
down_revision = "0011_workbench_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("dataset_id", sa.String(128), primary_key=True),
        sa.Column("split", sa.String(32), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "candidate_versions",
        sa.Column("candidate_id", sa.String(128), primary_key=True),
        sa.Column("candidate_type", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("parent_version", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "experiments",
        sa.Column("experiment_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=False, index=True),
        sa.Column("baseline_ref", sa.String(128), nullable=False),
        sa.Column("candidate_refs_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "experiment_results",
        sa.Column("result_id", sa.String(128), primary_key=True),
        sa.Column("experiment_id", sa.String(128), nullable=False, index=True),
        sa.Column("candidate_id", sa.String(128), nullable=False, index=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=True),
        sa.Column("safety_violations", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "active_pointers",
        sa.Column("pointer_id", sa.String(128), primary_key=True),
        sa.Column("domain_pack_ref", sa.String(128), nullable=False, unique=True),
        sa.Column("candidate_id", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "promotion_decisions",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column("candidate_id", sa.String(128), nullable=False, index=True),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evaluation_refs_json", sa.Text(), nullable=False),
        sa.Column("previous_candidate_id", sa.String(128), nullable=True),
        sa.Column("resulting_generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "failure_patterns",
        sa.Column("pattern_id", sa.String(128), primary_key=True),
        sa.Column("failure_code", sa.String(128), nullable=False, unique=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "failure_patterns",
        "promotion_decisions",
        "active_pointers",
        "experiment_results",
        "experiments",
        "candidate_versions",
        "evaluation_datasets",
    ):
        op.drop_table(table)
