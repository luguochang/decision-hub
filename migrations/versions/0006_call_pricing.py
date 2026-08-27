"""Add pricing lineage to normalized provider calls."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, String, inspect

revision = "0006_call_pricing"
down_revision = "0005_run_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "run_calls" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("run_calls")}
    if "pricing_version" not in columns:
        op.add_column(
            "run_calls",
            Column("pricing_version", String(length=64), nullable=True),
        )


def downgrade() -> None:
    columns = {
        item["name"] for item in inspect(op.get_bind()).get_columns("run_calls")
    }
    if "pricing_version" in columns:
        op.drop_column("run_calls", "pricing_version")
