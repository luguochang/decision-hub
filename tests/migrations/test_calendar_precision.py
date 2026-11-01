from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def test_precision_migration_preserves_old_vintage_and_does_not_invent_schedule(
    tmp_path: Path,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'calendar-migration.sqlite3'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0043_source_acquisition_receipt_timeline")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO macro_events
            (macro_event_id,event_id,external_event_id,family,scheduled_at,source_id,
             source_authority,revision_flag,status,created_at,updated_at)
            VALUES ('macro','event','uid','fomc','2028-01-01 00:00:00','fed-calendar',
             'official',0,'scheduled',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO macro_event_vintages
            (vintage_id,macro_event_id,event_id,observed_at,revision_flag,status,payload_hash,created_at)
            VALUES ('original','macro','event',CURRENT_TIMESTAMP,0,'scheduled',
                    'original-hash',CURRENT_TIMESTAMP)
        """)
        )
    command.upgrade(config, "0044_calendar_time_precision")
    with engine.connect() as connection:
        event = connection.execute(text("SELECT * FROM macro_events")).one()
        vintage = connection.execute(text("SELECT * FROM macro_event_vintages")).one()
        assert event.time_precision == vintage.time_precision == "unknown"
        assert event.scheduled_date is None and event.source_timezone is None
        assert vintage.scheduled_at is None and vintage.scheduled_date is None
        assert vintage.payload_hash == "original-hash"
        assert event.scheduled_at == "2028-01-01 00:00:00"
    command.downgrade(config, "0043_source_acquisition_receipt_timeline")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT payload_hash FROM macro_event_vintages")).scalar_one()
            == "original-hash"
        )
