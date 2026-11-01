"""Durable publication bundle jobs, originals and confirmation state.

Revision ID: 0091_research_publication_bundles
Revises: 0090_history_model_qualification_v3
"""

import sqlalchemy as sa
from alembic import op

revision = "0091_research_publication_bundles"
down_revision = "0090_history_model_qualification_v3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_publication_bundle_jobs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("artifact_id", sa.String(128), nullable=False, unique=True),
        sa.Column("publication_id", sa.String(128), nullable=False, unique=True),
        sa.Column("publication_hash", sa.String(64), nullable=False),
        sa.Column("publication_schema", sa.String(128), nullable=False),
        sa.Column("publication_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("selection_native_json", sa.Text(), nullable=False),
        sa.Column("selection_native_hash", sa.String(64), nullable=False),
        sa.Column("reader_version", sa.String(96), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "research_publication_bundles",
        sa.Column("bundle_hash", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, unique=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("bundle_ref", sa.String(192), nullable=False),
        sa.Column("bundle_bytes", sa.Integer(), nullable=False),
        sa.Column("material_ref", sa.Text(), nullable=False),
        sa.Column("material_hash", sa.String(64), nullable=False),
        sa.Column("material_bytes", sa.Integer(), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("report_bytes", sa.Integer(), nullable=False),
        sa.Column("reader_hash", sa.String(64), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "research_publication_bundle_confirmations",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "research_publication_bundle_state_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(160), nullable=True),
        sa.Column("bundle_hash", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "version", name="uq_publication_bundle_state_version"),
    )
    op.create_index(
        "ix_research_publication_bundle_state_events_run_id",
        "research_publication_bundle_state_events", ["run_id"],
    )


def downgrade():
    op.drop_index(
        "ix_research_publication_bundle_state_events_run_id",
        table_name="research_publication_bundle_state_events",
    )
    op.drop_table("research_publication_bundle_state_events")
    op.drop_table("research_publication_bundle_confirmations")
    op.drop_table("research_publication_bundles")
    op.drop_table("research_publication_bundle_jobs")
