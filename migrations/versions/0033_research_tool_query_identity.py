"""Bind durable tool idempotency to the complete query payload."""

import sqlalchemy as sa
from alembic import op

revision = "0033_research_tool_query_identity"
down_revision = "0032_historical_data_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_tool_call_reservations",
        sa.Column("query_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_tool_call_reservations", "query_hash")
