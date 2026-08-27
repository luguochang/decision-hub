"""Add idempotent promotion audit and evaluation-family evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0013_promotion_invariants"
down_revision = "0012_evolution_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_results") as batch:
        batch.add_column(
            sa.Column("event_family_counts_json", sa.Text(), nullable=False, server_default="{}")
        )
    with op.batch_alter_table("promotion_decisions") as batch:
        batch.add_column(sa.Column("request_id", sa.String(256), nullable=True))
        batch.add_column(sa.Column("domain_pack_ref", sa.String(128), nullable=True))
        batch.add_column(sa.Column("resulting_candidate_id", sa.String(128), nullable=True))
        batch.create_unique_constraint("uq_promotion_decisions_request_id", ["request_id"])
        batch.create_index("ix_promotion_decisions_domain_pack_ref", ["domain_pack_ref"])


def downgrade() -> None:
    with op.batch_alter_table("promotion_decisions") as batch:
        batch.drop_index("ix_promotion_decisions_domain_pack_ref")
        batch.drop_constraint("uq_promotion_decisions_request_id", type_="unique")
        batch.drop_column("resulting_candidate_id")
        batch.drop_column("domain_pack_ref")
        batch.drop_column("request_id")
    with op.batch_alter_table("experiment_results") as batch:
        batch.drop_column("event_family_counts_json")
