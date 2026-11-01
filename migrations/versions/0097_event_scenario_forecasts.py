"""Calendar-event forecast jobs and immutable event-owned outputs.

Static additive DDL; no current ORM imports or normal data rewrite.
"""
import sqlalchemy as sa
from alembic import op
revision='0097_event_scenario_forecasts'
down_revision='0096_research_skill_learning'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('event_scenario_jobs',
        sa.Column('job_id',sa.String(96),primary_key=True),sa.Column('event_key',sa.String(200),nullable=False),sa.Column('scheduled_at',sa.DateTime(timezone=True),nullable=False),sa.Column('reference_period',sa.String(7)),sa.Column('slot_id',sa.String(10),nullable=False),sa.Column('job_kind',sa.String(16),nullable=False),sa.Column('scope_key',sa.String(128),nullable=False),sa.Column('parent_forecast_id',sa.String(96)),sa.Column('input_hash',sa.String(64),nullable=False),sa.Column('model_hash',sa.String(64),nullable=False),sa.Column('input_manifest_json',sa.Text(),nullable=False),sa.Column('state',sa.String(24),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),sa.Column('lease_owner',sa.String(128)),sa.Column('lease_generation',sa.Integer(),nullable=False),sa.Column('lease_expires_at',sa.DateTime(timezone=True)),sa.Column('next_attempt_at',sa.DateTime(timezone=True)),sa.Column('last_error_code',sa.String(128)),sa.Column('forecast_id',sa.String(96)),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.Column('completed_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('event_key','slot_id','job_kind','scope_key','input_hash','model_hash',name='uq_event_scenario_job'),sa.CheckConstraint("state IN ('waiting_inputs','pending','running','published','failed','missed')",name='ck_event_scenario_job_state'),sa.CheckConstraint("job_kind IN ('forecast','opportunity')",name='ck_event_scenario_job_kind'),sa.CheckConstraint('attempts>=0 AND lease_generation>=0',name='ck_event_scenario_job_generation'))
    op.create_index('ix_event_scenario_jobs_event_key','event_scenario_jobs',['event_key'])
    op.create_index('ix_event_scenario_jobs_due','event_scenario_jobs',['state','next_attempt_at','lease_expires_at'])
    op.create_table('event_scenario_forecasts',
        sa.Column('forecast_id',sa.String(96),primary_key=True),sa.Column('job_id',sa.String(96),nullable=False),sa.Column('event_key',sa.String(200),nullable=False),sa.Column('scheduled_at',sa.DateTime(timezone=True),nullable=False),sa.Column('reference_period',sa.String(7),nullable=False),sa.Column('slot_id',sa.String(10),nullable=False),sa.Column('input_hash',sa.String(64),nullable=False),sa.Column('model_hash',sa.String(64),nullable=False),sa.Column('as_of',sa.DateTime(timezone=True),nullable=False),sa.Column('published_at',sa.DateTime(timezone=True),nullable=False),sa.Column('revision_parent_ref',sa.String(96)),sa.Column('schema_version',sa.String(64),nullable=False),sa.Column('payload_json',sa.Text(),nullable=False),sa.Column('payload_hash',sa.String(64),nullable=False),sa.UniqueConstraint('job_id',name='uq_event_scenario_forecast_job'))
    op.create_index('ix_event_scenario_forecast_event_time','event_scenario_forecasts',['event_key','published_at'])
    op.create_table('event_scenario_outputs',
        sa.Column('output_id',sa.String(128),primary_key=True),sa.Column('forecast_id',sa.String(96),nullable=False),sa.Column('owner_forecast_hash',sa.String(64),nullable=False),sa.Column('event_key',sa.String(200),nullable=False),sa.Column('kind',sa.String(32),nullable=False),sa.Column('scope_key',sa.String(128),nullable=False),sa.Column('inputs_hash',sa.String(64),nullable=False),sa.Column('model_hash',sa.String(64),nullable=False),sa.Column('schema_version',sa.String(64),nullable=False),sa.Column('payload_json',sa.Text(),nullable=False),sa.Column('payload_hash',sa.String(64),nullable=False),sa.Column('published_at',sa.DateTime(timezone=True),nullable=False),sa.Column('revision_parent_ref',sa.String(128)),sa.UniqueConstraint('forecast_id','kind','scope_key','inputs_hash','model_hash',name='uq_event_scenario_output'))
    op.create_index('ix_event_scenario_output_event_scope','event_scenario_outputs',['event_key','scope_key','published_at'])
    if op.get_bind().dialect.name=='sqlite':
        identity_fields=('job_id','event_key','scheduled_at','reference_period','slot_id','job_kind','scope_key','parent_forecast_id','input_hash','model_hash','input_manifest_json','created_at')
        changed=' OR '.join(f'OLD.{name} IS NOT NEW.{name}' for name in identity_fields)
        op.execute(sa.text(f"CREATE TRIGGER event_scenario_job_identity_immutable BEFORE UPDATE ON event_scenario_jobs WHEN {changed} BEGIN SELECT RAISE(ABORT,'event_scenario_job_identity_immutable'); END"))
        for table,key in (('event_scenario_forecasts','forecast_id'),('event_scenario_outputs','output_id')):
            for operation in ('UPDATE','DELETE'):
                op.execute(sa.text(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'event_scenario_original_immutable'); END"))
            identity=("forecast_id=NEW.forecast_id OR job_id=NEW.job_id" if table=='event_scenario_forecasts' else "output_id=NEW.output_id OR (forecast_id=NEW.forecast_id AND kind=NEW.kind AND scope_key=NEW.scope_key AND inputs_hash=NEW.inputs_hash AND model_hash=NEW.model_hash)")
            op.execute(sa.text(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {identity}) BEGIN SELECT RAISE(ABORT,'event_scenario_original_immutable'); END"))

def downgrade():
    raise RuntimeError('event_scenario_originals_require_explicit_archive_migration')
