"""Independent immutable prospective forecast originals; preserve training ledgers."""

import sqlalchemy as sa
from alembic import op

revision = "0083_history_observed_forecasts"
down_revision = "0082_history_observed_training"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_history_forecasts",
        sa.Column("forecast_id", sa.String(80), primary_key=True),
        sa.Column("operator_owner_claim", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("registration_id", sa.String(80), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("model_known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("threshold", sa.String(8), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("original_ref", sa.String(2048), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "operator_owner_claim",
            "request_id",
            name="uq_history_forecast_request",
        ),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM market_history_forecasts LIMIT 1")).first():
        raise ValueError("history_forecast_nonempty_originals_preserved_on_rollback")
    op.drop_table("market_history_forecasts")
