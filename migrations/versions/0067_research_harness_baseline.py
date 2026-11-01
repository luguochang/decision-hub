"""Add independent Harness originals; no seed, pointer, approval or backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0067_research_harness_baseline"
down_revision = "0066_analytical_assessment_jobs"
branch_labels = None
depends_on = None

TABLES = (
    ("research_harness_candidates", "candidate_id"),
    ("research_harness_evaluations", "evaluation_id"),
    ("research_harness_deployment_decisions", "request_id"),
    ("research_harness_pointers", "domain_pack_ref"),
)


def upgrade():
    for name, key in TABLES:
        columns = [
            sa.Column(key, sa.String(128), primary_key=True, nullable=False),
            sa.Column("execution_scope", sa.String(32), nullable=False),
        ]
        if name == "research_harness_pointers":
            columns.append(sa.Column("generation", sa.Integer(), nullable=False))
        columns.extend([
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.CheckConstraint("execution_scope = 'research_harness'", name=f"ck_{name}_scope"),
        ])
        if name == "research_harness_pointers":
            columns.append(sa.CheckConstraint("generation >= 1", name="ck_research_harness_generation"))
        op.create_table(name, *columns)


def downgrade():
    # Refuse to erase immutable history. Inspect all four before any DROP.
    connection = op.get_bind()
    for name, _ in TABLES:
        table = sa.table(name)
        if connection.scalar(sa.select(sa.func.count()).select_from(table)):
            raise RuntimeError("harness_history_downgrade_forbidden")
    for name, _ in reversed(TABLES):
        op.drop_table(name)
