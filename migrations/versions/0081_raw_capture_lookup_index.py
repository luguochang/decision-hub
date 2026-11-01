"""Index exact archived capture lookup without changing originals or their identity."""

from alembic import op

revision = "0081_raw_capture_lookup_index"
down_revision = "0080_risk_research_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_raw_capture_body_identity",
        "raw_captures",
        ["body_ref", "body_hash"],
        unique=False,
    )


def downgrade() -> None:
    # Only a derived lookup index is removed. Original/audit rows are untouched.
    op.drop_index("ix_raw_capture_body_identity", table_name="raw_captures")
