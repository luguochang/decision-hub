"""Immutable manual frozen-run pairs; no backfill or deployment change."""
import sqlalchemy as sa
from alembic import op
revision = '0069_harness_case_assignments'
down_revision = '0068_discovery_admission_recovery'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('research_harness_case_assignments',
        sa.Column('assignment_id', sa.String(128), primary_key=True, nullable=False),
        sa.Column('request_id', sa.String(256), unique=True, nullable=False),
        sa.Column('baseline_run_id', sa.String(128), unique=True, nullable=False),
        sa.Column('candidate_run_id', sa.String(128), unique=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False))
    op.create_index('ix_harness_assignments_created', 'research_harness_case_assignments', ['created_at','assignment_id'])

def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM research_harness_case_assignments')):
        raise RuntimeError('harness_assignment_history_downgrade_forbidden')
    op.drop_index('ix_harness_assignments_created', table_name='research_harness_case_assignments')
    op.drop_table('research_harness_case_assignments')
