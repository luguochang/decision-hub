"""Persist immutable strategy calibration receipts and bind forecasts to them."""

import sqlalchemy as sa
from alembic import op

revision = "0036_strategy_calibration_receipts"
down_revision = "0035_forecast_point_in_time_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_calibrations",
        sa.Column("receipt_id", sa.String(128), primary_key=True),
        sa.Column("strategy_version", sa.String(128), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_sample_count", sa.Integer(), nullable=False),
        sa.Column("fold_count", sa.Integer(), nullable=False),
        sa.Column("accuracy", sa.Float(), nullable=False),
        sa.Column("accuracy_lower_bound", sa.Float(), nullable=False),
        sa.Column("brier_score", sa.Float(), nullable=False),
        sa.Column("baseline_brier_score", sa.Float(), nullable=False),
        sa.Column("log_loss", sa.Float(), nullable=False),
        sa.Column("net_return_pct", sa.Float(), nullable=False),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("max_regime_share", sa.Float(), nullable=False),
        sa.Column("calibration_slope", sa.Float(), nullable=False),
        sa.Column("calibration_intercept", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("failure_reasons_json", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "strategy_version",
            "horizon",
            "dataset_hash",
            name="uq_strategy_calibration_dataset",
        ),
        sa.UniqueConstraint("report_hash"),
    )
    op.create_index(
        "ix_strategy_calibrations_strategy_version",
        "strategy_calibrations",
        ["strategy_version"],
    )
    op.create_index("ix_strategy_calibrations_horizon", "strategy_calibrations", ["horizon"])
    op.create_index(
        "ix_strategy_calibrations_dataset_hash",
        "strategy_calibrations",
        ["dataset_hash"],
    )
    op.create_index("ix_strategy_calibrations_passed", "strategy_calibrations", ["passed"])
    op.create_index(
        "ix_strategy_calibrations_evaluated_at",
        "strategy_calibrations",
        ["evaluated_at"],
    )
    op.add_column(
        "forecasts", sa.Column("calibration_receipt_id", sa.String(128), nullable=True)
    )
    op.create_index(
        "ix_forecasts_calibration_receipt_id",
        "forecasts",
        ["calibration_receipt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_forecasts_calibration_receipt_id", table_name="forecasts")
    op.drop_column("forecasts", "calibration_receipt_id")
    op.drop_table("strategy_calibrations")
