"""Persist document privacy mode for safe model correction routing."""

import sqlalchemy as sa
from alembic import op

revision = "0041_document_privacy_mode"
down_revision = "0040_research_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_documents",
        sa.Column("privacy_mode", sa.String(32), nullable=False, server_default="local_only"),
    )


def downgrade() -> None:
    op.drop_column("research_documents", "privacy_mode")
