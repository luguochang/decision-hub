"""Independent cross-registration history-at-fit model originals."""

from alembic import op
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record,
)

revision = "0086_history_dataset_model_v2"
down_revision = "0085_history_missing_resume_v2"
branch_labels = None
depends_on = None


def upgrade():
    HistoryDatasetModelV2Record.__table__.create(op.get_bind(), checkfirst=False)


def downgrade():
    connection = op.get_bind()
    if connection.execute(text("SELECT 1 FROM market_history_v2_models LIMIT 1")).first():
        raise ValueError("history_v2_nonempty_models_preserved")
    HistoryDatasetModelV2Record.__table__.drop(connection, checkfirst=False)
