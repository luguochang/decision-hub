"""Freeze research judgments separately from publication and execution gates."""

import sqlalchemy as sa
from alembic import op

revision = "0051_analytical_outlooks"
down_revision = "0050_fact_lineage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analytical_outlooks",
        sa.Column("outlook_id", sa.String(128), nullable=False, primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, primary_key=False),
        sa.Column("artifact_id", sa.String(128), nullable=False, primary_key=False),
        sa.Column("study_id", sa.String(128), nullable=True, primary_key=False),
        sa.Column("market", sa.String(64), nullable=False, primary_key=False),
        sa.Column("instrument", sa.String(128), nullable=False, primary_key=False),
        sa.Column("method_family", sa.String(64), nullable=False, primary_key=False),
        sa.Column("method_version", sa.String(256), nullable=False, primary_key=False),
        sa.Column("horizon", sa.String(16), nullable=False, primary_key=False),
        sa.Column("action", sa.String(32), nullable=False, primary_key=False),
        sa.Column("subjective_probability", sa.Float(), nullable=True, primary_key=False),
        sa.Column("probability_status", sa.String(32), nullable=False, primary_key=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("window_start_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("result_hash", sa.String(64), nullable=False, primary_key=False),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("fact_version_refs_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("raw_payload_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("trigger", sa.Text(), nullable=False, primary_key=False),
        sa.Column("invalidation", sa.Text(), nullable=False, primary_key=False),
        sa.Column("cohort", sa.String(32), nullable=False, primary_key=False),
        sa.Column("eligibility", sa.String(32), nullable=False, primary_key=False),
        sa.Column("exclusion_reasons_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("research_quality", sa.String(32), nullable=False, primary_key=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.UniqueConstraint(
            "run_id", "method_version", "horizon", name="uq_outlook_run_method_horizon"
        ),
    )
    op.create_index("ix_analytical_outlooks_artifact_id", "analytical_outlooks", ["artifact_id"])
    op.create_index("ix_analytical_outlooks_run_id", "analytical_outlooks", ["run_id"])
    op.create_index("ix_analytical_outlooks_study_id", "analytical_outlooks", ["study_id"])
    op.create_index(
        "ix_analytical_outlooks_window_end_at", "analytical_outlooks", ["window_end_at"]
    )
    op.create_index(
        "ix_outlooks_cohort",
        "analytical_outlooks",
        ["market", "instrument", "method_version", "horizon"],
    )
    op.create_table(
        "publication_decisions",
        sa.Column("artifact_id", sa.String(128), nullable=False, primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, primary_key=False),
        sa.Column("status", sa.String(32), nullable=False, primary_key=False),
        sa.Column("result_hash", sa.String(64), nullable=True, primary_key=False),
        sa.Column("decisions_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("published_horizons_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("review_refs_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
    )
    op.create_index("ix_publication_decisions_run_id", "publication_decisions", ["run_id"])
    op.create_table(
        "execution_permissions",
        sa.Column("artifact_id", sa.String(128), nullable=False, primary_key=True),
        sa.Column("allowed", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("policy_version", sa.String(128), nullable=False, primary_key=False),
        sa.Column("reasons_json", sa.Text(), nullable=False, primary_key=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
    )
    op.create_table(
        "analytical_observations",
        sa.Column("outlook_id", sa.String(128), nullable=False, primary_key=True),
        sa.Column("status", sa.String(32), nullable=False, primary_key=False),
        sa.Column("attempt", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("lease_owner", sa.String(128), nullable=True, primary_key=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("market_return_pct", sa.Float(), nullable=True, primary_key=False),
        sa.Column("directional_return_pct", sa.Float(), nullable=True, primary_key=False),
        sa.Column("direction_correct", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("quality_status", sa.String(32), nullable=True, primary_key=False),
        sa.Column("benchmark", sa.String(64), nullable=True, primary_key=False),
        sa.Column("window_json", sa.Text(), nullable=True, primary_key=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("error_code", sa.String(128), nullable=True, primary_key=False),
    )
    op.create_index(
        "ix_analytical_observations_next_attempt_at", "analytical_observations", ["next_attempt_at"]
    )
    op.create_index("ix_analytical_observations_status", "analytical_observations", ["status"])
    op.create_table(
        "analytical_observation_attempts",
        sa.Column("outlook_id", sa.String(128), nullable=False, primary_key=True),
        sa.Column("attempt", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("status", sa.String(32), nullable=False, primary_key=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("error_code", sa.String(128), nullable=True, primary_key=False),
    )


def downgrade():
    op.drop_table("analytical_observation_attempts")
    op.drop_table("analytical_observations")
    op.drop_table("execution_permissions")
    op.drop_table("publication_decisions")
    op.drop_table("analytical_outlooks")
