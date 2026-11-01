"""Add independent spot trial admissions without changing historical Run identities."""

import sqlalchemy as sa
from alembic import op

revision = "0074_spot_scope_admissions"
down_revision = "0073_market_risk_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "spot_research_scope_admissions",
        sa.Column("admission_id", sa.String(80), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("research_scope_id", sa.String(128), nullable=False),
        sa.Column("path_scope_id", sa.String(192), nullable=False),
        sa.Column("instrument", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("proof_json", sa.Text(), nullable=False),
        sa.Column("proof_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purpose = 'controlled_trial'", name="ck_spot_admission_purpose"),
    )
    op.create_index(
        "ix_spot_admission_scope_expiry",
        "spot_research_scope_admissions",
        ["research_scope_id", "expires_at"],
    )


def downgrade():
    raise RuntimeError("spot_research_admission_audit_preservation_required")
