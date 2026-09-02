"""Add durable evolution jobs and service heartbeats."""

import sqlalchemy as sa
from alembic import op

revision = "0016_live_observation_runtime"
down_revision = "0015_evolution_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_jobs",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("trigger_key", sa.String(512), nullable=False, unique=True),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("domain_pack_ref", sa.String(128), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("input_refs_json", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.String(128), nullable=True),
        sa.Column("experiment_refs_json", sa.Text(), nullable=False),
        sa.Column("result_refs_json", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True, index=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_evolution_jobs_due",
        "evolution_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_table(
        "service_heartbeats",
        sa.Column("service_id", sa.String(128), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("interval_seconds", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("service_heartbeats")
    op.drop_index("ix_evolution_jobs_due", table_name="evolution_jobs")
    op.drop_table("evolution_jobs")
