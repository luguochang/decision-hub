"""Add bounded recovery jobs; originals retained, no seed or replay."""

import sqlalchemy as sa
from alembic import op

revision = "0068_discovery_admission_recovery"
down_revision = "0067_research_harness_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discovery_admission_recovery_jobs",
        sa.Column("job_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("root_decision_id", sa.String(128), unique=True, nullable=False),
        sa.Column("contract_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("terminal_decision_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'closed')", name="ck_discovery_recovery_status"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_discovery_recovery_attempts"),
    )
    op.create_index(
        "ix_discovery_recovery_due",
        "discovery_admission_recovery_jobs",
        ["status", "next_check_at", "job_id"],
    )


def downgrade():
    table = sa.table("discovery_admission_recovery_jobs")
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(table)):
        raise RuntimeError("discovery_recovery_history_downgrade_forbidden")
    op.drop_index("ix_discovery_recovery_due", table_name="discovery_admission_recovery_jobs")
    op.drop_table("discovery_admission_recovery_jobs")
