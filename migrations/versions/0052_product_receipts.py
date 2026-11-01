"""Explicit product reading and tracking commands; no inferred historical reads."""

import sqlalchemy as sa
from alembic import op

revision = "0052_product_receipts"
down_revision = "0051_analytical_outlooks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_read_receipts",
        sa.Column("revision_id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "product_commands",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_product_commands_target_id", "product_commands", ["target_id"])


def downgrade():
    op.drop_index("ix_product_commands_target_id", table_name="product_commands")
    op.drop_table("product_commands")
    op.drop_table("product_read_receipts")
