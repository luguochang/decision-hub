"""Durable, independently leased model usage collection work."""
import sqlalchemy as sa
from alembic import op

revision = "0055_usage_collection"
down_revision = "0054_research_usage_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_collection_jobs",
        sa.Column("job_id", sa.String(80), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(256), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("last_view_hash", sa.String(64)),
        sa.Column("last_snapshot_id", sa.String(80)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("run_id", "next_check_at"):
        op.create_index(f"ix_usage_collection_jobs_{column}", "usage_collection_jobs", [column])


def downgrade():
    op.drop_table("usage_collection_jobs")
