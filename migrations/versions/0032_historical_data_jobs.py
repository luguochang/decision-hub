"""Add owner-triggered historical data jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0032_historical_data_jobs"
down_revision = "0031_research_document_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_jobs",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(32), nullable=False),
        sa.Column("dataset_ids_json", sa.Text(), nullable=False),
        sa.Column("window_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partition_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("coverage_manifest_ref", sa.String(2048), nullable=True),
        sa.Column("errors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_data_jobs_status", "data_jobs", ["status"])
    op.create_index("ix_data_jobs_created_at", "data_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_table("data_jobs")
