"""Independent V2 prospective forecast originals."""

from alembic import op
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record,
)

revision = "0087_history_dataset_forecast_v2"
down_revision = "0086_history_dataset_model_v2"
branch_labels = None
depends_on = None


def upgrade():
    HistoryDatasetForecastV2Record.__table__.create(op.get_bind(), checkfirst=False)


def downgrade():
    connection = op.get_bind()
    if connection.execute(text("SELECT 1 FROM market_history_v2_forecasts LIMIT 1")).first():
        raise ValueError("history_v2_nonempty_forecasts_preserved")
    HistoryDatasetForecastV2Record.__table__.drop(connection, checkfirst=False)
