from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session
import pytest

from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord


def test_event_flow_upgrade_preserves_existing_outbox_and_empty_rollback(tmp_path):
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    config.set_main_option('sqlalchemy.url', url)
    config.attributes['configure_logger'] = False
    command.upgrade(config, '0097_event_scenario_forecasts')
    engine = create_engine(url)
    with Session(engine) as session, session.begin():
        session.add(OutboxRecord(artifact_id='preserved-report', subject_id='preserved-report',
            subject_kind='report', channel='local', dedupe_key='preserved-report'))
    command.upgrade(config, '0098_event_main_flow')
    assert {'event_realizations', 'event_opportunity_monitor_heads', 'event_opportunity_evaluations',
        'event_opportunity_notices', 'event_opportunity_commands', 'event_prevision_coverage',
        'event_outcomes'} <= set(inspect(engine).get_table_names())
    with Session(engine) as session, session.begin():
        old = session.scalar(select(OutboxRecord).where(OutboxRecord.dedupe_key == 'preserved-report'))
        assert old.subject_kind == 'report' and old.artifact_id == old.subject_id == 'preserved-report'
        event = OutboxRecord(subject_kind='event_opportunity', artifact_id=None, subject_id='event-notice',
            channel='local', dedupe_key='new-event', payload_json='{}', payload_hash='0' * 64)
        session.add(event)
    with pytest.raises(RuntimeError, match='notification_downgrade_forbidden'):
        command.downgrade(config, '0097_event_scenario_forecasts')
    with Session(engine) as session, session.begin():
        session.delete(session.scalar(select(OutboxRecord).where(OutboxRecord.dedupe_key == 'new-event')))
    command.downgrade(config, '0097_event_scenario_forecasts')
    with Session(engine) as session:
        assert session.scalar(select(OutboxRecord.dedupe_key)) == 'preserved-report'
    assert 'event_realizations' not in inspect(engine).get_table_names()
