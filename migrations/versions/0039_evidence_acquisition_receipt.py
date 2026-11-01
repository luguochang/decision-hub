"""Link admitted evidence to its immutable acquisition receipt."""

import sqlalchemy as sa
from alembic import op

revision = "0039_evidence_acquisition_receipt"
down_revision = "0038_source_acquisition_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_evidence",
        sa.Column("acquisition_receipt_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_research_evidence_acquisition_receipt_id",
        "research_evidence",
        ["acquisition_receipt_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_evidence_acquisition_receipt_id",
        table_name="research_evidence",
    )
    op.drop_column("research_evidence", "acquisition_receipt_id")
