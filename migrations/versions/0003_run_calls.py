"""Add normalized model-call observability records."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, inspect

revision = "0003_run_calls"
down_revision = "0002_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "run_calls" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "run_calls",
        Column("call_id", String(length=128), primary_key=True),
        Column("run_id", String(length=128), nullable=False),
        Column("role", String(length=64), nullable=False),
        Column("status", String(length=32), nullable=False),
        Column("attempt", Integer(), nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("finished_at", DateTime(timezone=True), nullable=True),
        Column("latency_ms", Integer(), nullable=True),
        Column("runtime_id", String(length=64), nullable=True),
        Column("runtime_version", String(length=64), nullable=True),
        Column("provider_id", String(length=128), nullable=True),
        Column("model", String(length=128), nullable=True),
        Column("api_mode", String(length=32), nullable=True),
        Column("schema_version", String(length=64), nullable=True),
        Column("prompt_tokens", Integer(), nullable=True),
        Column("completion_tokens", Integer(), nullable=True),
        Column("total_tokens", Integer(), nullable=True),
        Column("cost_usd", Float(), nullable=True),
        Column("cost_status", String(length=32), nullable=False),
        Column("error_code", String(length=128), nullable=True),
        Column("retryable", Boolean(), nullable=False),
    )
    op.create_index("ix_run_calls_run_id", "run_calls", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_calls_run_id", table_name="run_calls")
    op.drop_table("run_calls")
