"""Add durable assessment work and projections without certifying historical rows."""

import sqlalchemy as sa
from alembic import op

revision = "0066_analytical_assessment_jobs"
down_revision = "0065_revision_feedback_evaluations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analytical_assessment_jobs",
        sa.Column("outlook_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verifier_version", sa.String(128), nullable=False),
        sa.Column("basis_json", sa.Text(), nullable=True),
        sa.Column("basis_hash", sa.String(64), nullable=True),
        sa.Column("assessment_json", sa.Text(), nullable=True),
        sa.Column("assessment_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry', 'completed')",
            name="ck_analytical_assessment_job_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_analytical_assessment_job_attempt"),
        sa.CheckConstraint(
            "status != 'running' OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_analytical_assessment_job_running_lease",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR "
            "(evaluated_at IS NOT NULL AND basis_json IS NOT NULL AND basis_hash IS NOT NULL "
            "AND assessment_json IS NOT NULL AND assessment_hash IS NOT NULL)",
            name="ck_analytical_assessment_job_completed_projection",
        ),
    )
    op.create_index(
        "ix_assessment_jobs_status_due", "analytical_assessment_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_assessment_jobs_lease_expires_at", "analytical_assessment_jobs",
        ["lease_expires_at"],
    )


def downgrade():
    op.drop_index("ix_assessment_jobs_lease_expires_at", table_name="analytical_assessment_jobs")
    op.drop_index("ix_assessment_jobs_status_due", table_name="analytical_assessment_jobs")
    op.drop_table("analytical_assessment_jobs")
