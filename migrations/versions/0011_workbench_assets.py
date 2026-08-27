"""Add R2 research memo, feedback and capability intake assets."""

import sqlalchemy as sa
from alembic import op

revision = "0011_workbench_assets"
down_revision = "0010_source_poll_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_memos",
        sa.Column("memo_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("run_id", sa.String(128), nullable=True, index=True),
        sa.Column("snapshot_id", sa.String(128), nullable=True, index=True),
        sa.Column("domain_pack_ref", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workbench_feedback",
        sa.Column("feedback_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False, index=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "capability_manifests",
        sa.Column("capability_id", sa.String(128), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("capability_manifests")
    op.drop_table("workbench_feedback")
    op.drop_table("research_memos")
