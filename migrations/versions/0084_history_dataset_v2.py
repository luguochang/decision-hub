"""Fixed DatasetPlan V2 and auditable bounded missing-page epochs."""

from alembic import op
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryDatasetV2OriginalRecord,
    HistoryMissingV2AttemptRecord,
    HistoryMissingV2EpochRecord,
    HistoryMissingV2JobRecord,
)

revision = "0084_history_dataset_v2"
down_revision = "0083_history_observed_forecasts"
branch_labels = None
depends_on = None
TABLES = tuple(
    record.__table__
    for record in (
        HistoryDatasetV2OriginalRecord,
        HistoryMissingV2EpochRecord,
        HistoryMissingV2JobRecord,
        HistoryMissingV2AttemptRecord,
    )
)


def upgrade() -> None:
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        if connection.execute(text(f"SELECT 1 FROM {table.name} LIMIT 1")).first():
            raise ValueError("history_v2_nonempty_originals_and_attempts_preserved")
    for table in reversed(TABLES):
        table.drop(connection, checkfirst=False)
