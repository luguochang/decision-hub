"""Link immutable original-document assets from research evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0031_research_document_assets"
down_revision = "0030_research_value_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_evidence") as batch:
        batch.add_column(sa.Column("content_ref", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("source_content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("parser_ref", sa.String(256), nullable=True))
        batch.create_index("ix_research_evidence_content_ref", ["content_ref"])


def downgrade() -> None:
    with op.batch_alter_table("research_evidence") as batch:
        batch.drop_index("ix_research_evidence_content_ref")
        batch.drop_column("parser_ref")
        batch.drop_column("source_content_hash")
        batch.drop_column("content_ref")
