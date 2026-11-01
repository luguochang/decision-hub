"""Independent V2 hourly producer slots; preserve nonempty audit history."""

from alembic import op
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord,
)

revision = "0088_history_hourly_v2"
down_revision = "0087_history_dataset_forecast_v2"
branch_labels = None
depends_on = None


def upgrade():
    HistoryHourlyV2SlotRecord.__table__.create(op.get_bind(), checkfirst=False)


def downgrade():
    connection = op.get_bind()
    if connection.execute(text("SELECT 1 FROM market_history_v2_hourly_slots LIMIT 1")).first():
        raise ValueError("history_v2_nonempty_hourly_slots_preserved")
    HistoryHourlyV2SlotRecord.__table__.drop(connection, checkfirst=False)
