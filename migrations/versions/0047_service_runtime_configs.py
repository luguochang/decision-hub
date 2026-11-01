"""Keep each process startup's redacted configuration and time as evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0047_service_runtime_configs"
down_revision = "0046_calendar_corrections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_runtime_configs",
        sa.Column("config_id", sa.String(64), primary_key=True),
        sa.Column("service_id", sa.String(128), nullable=False),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_service_runtime_configs_service_id", "service_runtime_configs", ["service_id"]
    )


def downgrade():
    op.drop_table("service_runtime_configs")
