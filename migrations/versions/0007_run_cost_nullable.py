"""Allow unknown run cost for databases created before cost metadata support."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Float, inspect

revision = "0007_run_cost_nullable"
down_revision = "0006_call_pricing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "runs" not in inspector.get_table_names():
        return
    columns = {item["name"]: item for item in inspector.get_columns("runs")}
    cost = columns.get("cost_usd")
    if cost is not None and cost.get("nullable") is False:
        with op.batch_alter_table("runs", recreate="always") as batch:
            batch.alter_column(
                "cost_usd",
                existing_type=Float(),
                existing_nullable=False,
                nullable=True,
            )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "runs" not in inspector.get_table_names():
        return
    columns = {item["name"]: item for item in inspector.get_columns("runs")}
    cost = columns.get("cost_usd")
    if cost is not None and cost.get("nullable") is True:
        with op.batch_alter_table("runs", recreate="always") as batch:
            batch.alter_column(
                "cost_usd",
                existing_type=Float(),
                existing_nullable=True,
                nullable=False,
            )
