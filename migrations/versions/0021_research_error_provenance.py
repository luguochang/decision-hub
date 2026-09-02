"""Persist structured provenance for research trace failures."""

import sqlalchemy as sa
from alembic import op

revision = "0021_research_error_provenance"
down_revision = "0020_research_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_trace_events",
        sa.Column("error_provenance_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_trace_events", "error_provenance_json")
