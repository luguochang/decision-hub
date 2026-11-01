"""Attach point-in-time provenance to forecast rows."""

import sqlalchemy as sa
from alembic import op

revision = "0035_forecast_point_in_time_audit"
down_revision = "0034_macro_event_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "forecasts", sa.Column("forecasted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("forecasts", sa.Column("as_of", sa.String(64), nullable=True))
    op.add_column("forecasts", sa.Column("strategy_version", sa.String(128), nullable=True))
    op.add_column("forecasts", sa.Column("input_snapshot_id", sa.String(128), nullable=True))
    op.add_column("forecasts", sa.Column("input_fingerprint", sa.String(64), nullable=True))
    op.add_column("forecasts", sa.Column("feature_snapshot_json", sa.Text(), nullable=True))
    op.add_column(
        "forecasts", sa.Column("evidence_cutoff_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_forecasts_forecasted_at", "forecasts", ["forecasted_at"])
    op.create_index("ix_forecasts_strategy_version", "forecasts", ["strategy_version"])
    op.create_index("ix_forecasts_input_snapshot_id", "forecasts", ["input_snapshot_id"])
    op.create_index("ix_forecasts_input_fingerprint", "forecasts", ["input_fingerprint"])


def downgrade() -> None:
    op.drop_column("forecasts", "evidence_cutoff_at")
    op.drop_column("forecasts", "feature_snapshot_json")
    op.drop_column("forecasts", "input_fingerprint")
    op.drop_column("forecasts", "input_snapshot_id")
    op.drop_column("forecasts", "strategy_version")
    op.drop_column("forecasts", "as_of")
    op.drop_column("forecasts", "forecasted_at")
