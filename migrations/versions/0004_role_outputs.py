"""Add idempotent structured role-output references for checkpoint recovery."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint, inspect

revision = "0004_role_outputs"
down_revision = "0003_run_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "run_role_outputs" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "run_role_outputs",
        Column("output_id", String(length=128), primary_key=True),
        Column("run_id", String(length=128), nullable=False),
        Column("role", String(length=64), nullable=False),
        Column("payload_json", Text(), nullable=False),
        Column("payload_hash", String(length=64), nullable=False),
        Column("schema_version", String(length=64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("run_id", "role", name="uq_run_role_output"),
    )
    op.create_index("ix_run_role_outputs_run_id", "run_role_outputs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_role_outputs_run_id", table_name="run_role_outputs")
    op.drop_table("run_role_outputs")
