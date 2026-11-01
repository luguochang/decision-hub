"""Explicit Action opportunity version; historical rows retain V1 semantics."""

import sqlalchemy as sa
from alembic import op

revision = "0101_decision_opportunity_version"
down_revision = "0100_perpetual_paper"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("decision_opportunities", sa.Column(
        "schema_version", sa.String(64), nullable=False,
        server_default="decision-opportunity.v1",
    ))
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_decision_opportunity_version_immutable "
            "BEFORE UPDATE OF schema_version ON decision_opportunities "
            "WHEN NEW.schema_version != OLD.schema_version "
            "BEGIN SELECT RAISE(ABORT, 'decision_opportunity_version_immutable'); END"
        )


def downgrade():
    # Removing this discriminator while V2 rows exist would relabel originals.
    count = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM decision_opportunities WHERE schema_version != 'decision-opportunity.v1'"
    )).scalar_one()
    if count:
        raise RuntimeError("decision_opportunity_v2_restore_required_before_downgrade")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_decision_opportunity_version_immutable")
    op.drop_column("decision_opportunities", "schema_version")
