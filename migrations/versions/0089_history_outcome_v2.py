"""Independent five-table outcome originals; preserve all nonempty audit history."""

from alembic import op
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeObservationV2Record,
    HistoryOutcomeOpportunityV2Record,
    HistoryOutcomeProtocolV2Record,
    HistoryOutcomePublicationV2Record,
    HistoryOutcomeScoreV2Record,
)

revision = "0089_history_outcome_v2"
down_revision = "0088_history_hourly_v2"
branch_labels = None
depends_on = None
TABLES = tuple(
    record.__table__
    for record in (
        HistoryOutcomeProtocolV2Record,
        HistoryOutcomeOpportunityV2Record,
        HistoryOutcomePublicationV2Record,
        HistoryOutcomeObservationV2Record,
        HistoryOutcomeScoreV2Record,
    )
)


def upgrade() -> None:
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    connection = op.get_bind()
    # Inspect all five before dropping any table, so a later nonempty table
    # cannot cause a partly destructive downgrade of earlier empty tables.
    for table in TABLES:
        if connection.execute(text(f"SELECT 1 FROM {table.name} LIMIT 1")).first():
            raise ValueError("history_v2_nonempty_outcome_originals_preserved")
    for table in reversed(TABLES):
        table.drop(connection, checkfirst=False)
