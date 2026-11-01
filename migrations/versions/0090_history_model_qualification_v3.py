"""Independent model qualification original and acceptance receipts.

Revision ID: 0090_history_model_qualification_v3
Revises: 0089_history_outcome_v2
"""

import sqlalchemy as sa
from alembic import op

revision = "0090_history_model_qualification_v3"
down_revision = "0089_history_outcome_v2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_history_model_qualifications_v3",
        sa.Column("qualification_id", sa.String(96), primary_key=True),
        sa.Column("owner_claim", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("model_id", sa.String(96), nullable=False),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(96), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("model_known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_information_asof", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bundle_ref", sa.Text(), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_claim", "request_id", "scope_id", name="uq_history_qualification_v3_request"
        ),
    )
    op.create_table(
        "market_history_model_qualification_events_v3",
        sa.Column("event_id", sa.String(96), primary_key=True),
        sa.Column("qualification_id", sa.String(96), nullable=False),
        sa.Column("qualification_hash", sa.String(64), nullable=False),
        sa.Column("owner_claim", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("confirmation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "qualification_id", "version", name="uq_history_qualification_v3_version"
        ),
    )
    op.create_index(
        "ix_market_history_model_qualification_events_v3_qualification_id",
        "market_history_model_qualification_events_v3",
        ["qualification_id"],
    )
    op.create_table(
        "market_history_qualified_forecast_bindings_v3",
        sa.Column("forecast_id", sa.String(96), primary_key=True),
        sa.Column("owner_claim", sa.String(128), nullable=False),
        sa.Column("qualification_id", sa.String(96), nullable=False),
        sa.Column("qualification_hash", sa.String(64), nullable=False),
        sa.Column("qualification_version", sa.Integer(), nullable=False),
        sa.Column("acceptance_id", sa.String(96), nullable=False),
        sa.Column("acceptance_hash", sa.String(64), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("forecast_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "market_history_qualified_forecast_acceptances_v3",
        sa.Column("forecast_id", sa.String(96), primary_key=True),
        sa.Column("owner_claim", sa.String(128), nullable=False),
        sa.Column("forecast_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table("market_history_qualified_forecast_acceptances_v3")
    op.drop_table("market_history_qualified_forecast_bindings_v3")
    op.drop_index(
        "ix_market_history_model_qualification_events_v3_qualification_id",
        table_name="market_history_model_qualification_events_v3",
    )
    op.drop_table("market_history_model_qualification_events_v3")
    op.drop_table("market_history_model_qualifications_v3")
