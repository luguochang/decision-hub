"""Add durable LangGraph step-attempt projections."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, DateTime, Integer, String, inspect

revision = "0005_run_steps"
down_revision = "0004_role_outputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "run_steps" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "run_steps",
        Column("step_id", String(length=128), primary_key=True),
        Column("run_id", String(length=128), nullable=False),
        Column("step_name", String(length=64), nullable=False),
        Column("status", String(length=32), nullable=False),
        Column("attempt", Integer(), nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("finished_at", DateTime(timezone=True), nullable=True),
        Column("latency_ms", Integer(), nullable=True),
        Column("error_code", String(length=128), nullable=True),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_steps_run_id", table_name="run_steps")
    op.drop_table("run_steps")
