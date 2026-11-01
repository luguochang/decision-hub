"""Event actuals, lifecycle, coverage and outcomes; preserves existing records."""
import sqlalchemy as sa
from alembic import op
revision = '0098_event_main_flow'
down_revision = '0097_event_scenario_forecasts'
branch_labels = None
depends_on = None
TABLES = ('event_realizations', 'event_opportunity_monitor_heads', 'event_opportunity_evaluations', 'event_opportunity_notices', 'event_opportunity_commands', 'event_prevision_coverage', 'event_outcomes')
OLD_SUBJECT_CHECK = "(subject_kind = 'report' AND artifact_id IS NOT NULL AND (subject_id IS NULL OR subject_id = artifact_id)) OR (subject_kind IN ('risk_alert', 'action_revision') AND artifact_id IS NULL AND subject_id IS NOT NULL AND payload_json IS NOT NULL AND payload_hash IS NOT NULL)"
NEW_SUBJECT_CHECK = "(subject_kind = 'report' AND artifact_id IS NOT NULL AND (subject_id IS NULL OR subject_id = artifact_id)) OR (subject_kind IN ('risk_alert', 'action_revision', 'event_opportunity') AND artifact_id IS NULL AND subject_id IS NOT NULL AND payload_json IS NOT NULL AND payload_hash IS NOT NULL)"

def upgrade():
    op.create_table('event_realizations',
        sa.Column('realization_id', sa.String(length=128), nullable=False, primary_key=True),
        sa.Column('event_key', sa.String(length=200), nullable=False),
        sa.Column('forecast_id', sa.String(length=96), nullable=False),
        sa.Column('owner_forecast_hash', sa.String(length=64), nullable=False),
        sa.Column('reference_period', sa.String(length=7), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('known_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('input_hash', sa.String(length=64), nullable=False),
        sa.Column('revision_parent_ref', sa.String(length=128), nullable=True),
        sa.Column('schema_version', sa.String(length=64), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.UniqueConstraint('forecast_id', 'input_hash', name='uq_event_realization_input'),
    )
    op.create_index('ix_event_realization_event_time', 'event_realizations', ['event_key', 'known_at', 'recorded_at'], unique=False)
    op.create_index('ix_event_realizations_forecast_id', 'event_realizations', ['forecast_id'], unique=False)
    op.create_table('event_opportunity_monitor_heads',
        sa.Column('monitor_id', sa.String(length=96), nullable=False, primary_key=True),
        sa.Column('event_key', sa.String(length=200), nullable=False),
        sa.Column('forecast_id', sa.String(length=96), nullable=False),
        sa.Column('instrument', sa.String(length=16), nullable=False),
        sa.Column('position_context', sa.String(length=8), nullable=False),
        sa.Column('activation_id', sa.String(length=96), nullable=True),
        sa.Column('opportunity_id', sa.String(length=128), nullable=True),
        sa.Column('opportunity_hash', sa.String(length=64), nullable=True),
        sa.Column('scenario_id', sa.String(length=128), nullable=True),
        sa.Column('state', sa.String(length=24), nullable=False),
        sa.Column('state_version', sa.Integer(), nullable=False),
        sa.Column('latest_evaluation_id', sa.String(length=96), nullable=True),
        sa.Column('last_input_watermark', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_due', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_owner', sa.String(length=128), nullable=True),
        sa.Column('lease_generation', sa.Integer(), nullable=False),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position_context IN ('flat','long')", name='ck_event_monitor_position'),
        sa.CheckConstraint("state IN ('waiting','triggered','invalidated','expired','missing')", name='ck_event_monitor_state'),
        sa.UniqueConstraint('forecast_id', 'instrument', 'position_context', name='uq_event_monitor_owner'),
    )
    op.create_index('ix_event_monitor_due', 'event_opportunity_monitor_heads', ['next_due', 'lease_expires_at'], unique=False)
    op.create_index('ix_event_opportunity_monitor_heads_event_key', 'event_opportunity_monitor_heads', ['event_key'], unique=False)
    op.create_table('event_opportunity_evaluations',
        sa.Column('evaluation_id', sa.String(length=96), nullable=False, primary_key=True),
        sa.Column('monitor_id', sa.String(length=96), nullable=False),
        sa.Column('event_key', sa.String(length=200), nullable=False),
        sa.Column('tick_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('monitor_id', 'tick_at', name='uq_event_monitor_tick'),
    )
    op.create_index('ix_event_opportunity_evaluations_event_key', 'event_opportunity_evaluations', ['event_key'], unique=False)
    op.create_index('ix_event_opportunity_evaluations_monitor_id', 'event_opportunity_evaluations', ['monitor_id'], unique=False)
    op.create_table('event_opportunity_notices',
        sa.Column('notice_id', sa.String(length=96), nullable=False, primary_key=True),
        sa.Column('event_key', sa.String(length=200), nullable=False),
        sa.Column('dedupe_key', sa.String(length=256), nullable=False),
        sa.Column('outbox_id', sa.Integer(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('dedupe_key', name='uq_event_notice_dedupe'),
    )
    op.create_index('ix_event_opportunity_notices_created_at', 'event_opportunity_notices', ['created_at'], unique=False)
    op.create_index('ix_event_opportunity_notices_event_key', 'event_opportunity_notices', ['event_key'], unique=False)
    op.create_table('event_opportunity_commands',
        sa.Column('command_id', sa.String(length=96), nullable=False, primary_key=True),
        sa.Column('notice_id', sa.String(length=96), nullable=False),
        sa.Column('owner_id', sa.String(length=128), nullable=False),
        sa.Column('request_id', sa.String(length=128), nullable=False),
        sa.Column('command', sa.String(length=16), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('notice_id', 'owner_id', 'request_id', name='uq_event_notice_command'),
    )
    op.create_index('ix_event_opportunity_commands_notice_id', 'event_opportunity_commands', ['notice_id'], unique=False)
    op.create_table('event_prevision_coverage',
        sa.Column('coverage_id', sa.String(length=96), nullable=False, primary_key=True),
        sa.Column('event_key', sa.String(length=200), nullable=False),
        sa.Column('reference_period', sa.String(length=7), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('slot_kind', sa.String(length=16), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('forecast_id', sa.String(length=96), nullable=True),
        sa.Column('forecast_published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notice_id', sa.String(length=128), nullable=True),
        sa.Column('reason_code', sa.String(length=128), nullable=True),
        sa.CheckConstraint("state IN ('pending','ready','late','missing','missed')", name='ck_event_prevision_coverage_state'),
        sa.UniqueConstraint('event_key', 'slot_kind', name='uq_event_prevision_coverage_slot'),
    )
    op.create_index('ix_event_prevision_coverage_due', 'event_prevision_coverage', ['due_at', 'state'], unique=False)
    op.create_table('event_outcomes',
        sa.Column('outcome_id', sa.String(length=128), nullable=False, primary_key=True),
        sa.Column('event_key', sa.String(length=200), nullable=False),
        sa.Column('forecast_id', sa.String(length=96), nullable=False),
        sa.Column('realization_id', sa.String(length=128), nullable=False),
        sa.Column('owner_forecast_hash', sa.String(length=64), nullable=False),
        sa.Column('realization_hash', sa.String(length=64), nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('input_hash', sa.String(length=64), nullable=False),
        sa.Column('revision_parent_ref', sa.String(length=128), nullable=True),
        sa.Column('schema_version', sa.String(length=64), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.UniqueConstraint('forecast_id', 'realization_id', 'input_hash', name='uq_event_outcome_input'),
    )
    op.create_index('ix_event_outcome_event_time', 'event_outcomes', ['event_key', 'recorded_at'], unique=False)
    op.create_index('ix_event_outcomes_forecast_id', 'event_outcomes', ['forecast_id'], unique=False)
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_subject_identity", type_="check")
        batch.create_check_constraint("ck_outbox_subject_identity", NEW_SUBJECT_CHECK)
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("CREATE TRIGGER trg_event_realizations_immutable_insert BEFORE INSERT ON event_realizations WHEN EXISTS(SELECT 1 FROM event_realizations WHERE (realization_id=NEW.realization_id) OR (forecast_id=NEW.forecast_id AND input_hash=NEW.input_hash)) BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: event_realizations'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_realizations_immutable_update BEFORE UPDATE ON event_realizations BEGIN SELECT RAISE(ABORT, 'event_realizations_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_realizations_immutable_delete BEFORE DELETE ON event_realizations BEGIN SELECT RAISE(ABORT, 'event_realizations_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_evaluations_immutable_insert BEFORE INSERT ON event_opportunity_evaluations WHEN EXISTS(SELECT 1 FROM event_opportunity_evaluations WHERE (evaluation_id=NEW.evaluation_id) OR (monitor_id=NEW.monitor_id AND tick_at=NEW.tick_at)) BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: event_opportunity_evaluations'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_evaluations_immutable_update BEFORE UPDATE ON event_opportunity_evaluations BEGIN SELECT RAISE(ABORT, 'event_opportunity_evaluations_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_evaluations_immutable_delete BEFORE DELETE ON event_opportunity_evaluations BEGIN SELECT RAISE(ABORT, 'event_opportunity_evaluations_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_notices_immutable_insert BEFORE INSERT ON event_opportunity_notices WHEN EXISTS(SELECT 1 FROM event_opportunity_notices WHERE (notice_id=NEW.notice_id) OR (dedupe_key=NEW.dedupe_key)) BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: event_opportunity_notices'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_notices_immutable_update BEFORE UPDATE ON event_opportunity_notices BEGIN SELECT RAISE(ABORT, 'event_opportunity_notices_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_notices_immutable_delete BEFORE DELETE ON event_opportunity_notices BEGIN SELECT RAISE(ABORT, 'event_opportunity_notices_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_commands_immutable_insert BEFORE INSERT ON event_opportunity_commands WHEN EXISTS(SELECT 1 FROM event_opportunity_commands WHERE (command_id=NEW.command_id) OR (notice_id=NEW.notice_id AND owner_id=NEW.owner_id AND request_id=NEW.request_id)) BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: event_opportunity_commands'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_commands_immutable_update BEFORE UPDATE ON event_opportunity_commands BEGIN SELECT RAISE(ABORT, 'event_opportunity_commands_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_opportunity_commands_immutable_delete BEFORE DELETE ON event_opportunity_commands BEGIN SELECT RAISE(ABORT, 'event_opportunity_commands_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_outcomes_immutable_insert BEFORE INSERT ON event_outcomes WHEN EXISTS(SELECT 1 FROM event_outcomes WHERE (outcome_id=NEW.outcome_id) OR (forecast_id=NEW.forecast_id AND realization_id=NEW.realization_id AND input_hash=NEW.input_hash)) BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: event_outcomes'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_outcomes_immutable_update BEFORE UPDATE ON event_outcomes BEGIN SELECT RAISE(ABORT, 'event_outcomes_immutable'); END"))
        op.execute(sa.text("CREATE TRIGGER trg_event_outcomes_immutable_delete BEFORE DELETE ON event_outcomes BEGIN SELECT RAISE(ABORT, 'event_outcomes_immutable'); END"))

def downgrade():
    connection = op.get_bind()
    for table in TABLES:
        if connection.execute(sa.text("SELECT 1 FROM " + table + " LIMIT 1")).first():
            raise RuntimeError("event_main_flow_nonempty_downgrade_forbidden:" + table)
    if connection.execute(sa.text("SELECT 1 FROM outbox WHERE subject_kind = 'event_opportunity' LIMIT 1")).first():
        raise RuntimeError("event_main_flow_notification_downgrade_forbidden")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_subject_identity", type_="check")
        batch.create_check_constraint("ck_outbox_subject_identity", OLD_SUBJECT_CHECK)
    for table in reversed(TABLES):
        op.drop_table(table)
