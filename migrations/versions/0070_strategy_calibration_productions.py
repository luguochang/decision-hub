"""Dedicated authenticated probability production; no legacy backfill."""

from alembic import op
import sqlalchemy as sa

revision = "0070_strategy_calibration_productions"
down_revision = "0069_harness_case_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_calibration_jobs",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_production_id", sa.String(128)),
        sa.Column("error_code", sa.String(128)),
    )
    op.create_index("ix_calibration_jobs_due", "strategy_calibration_jobs", ["status", "next_attempt_at", "job_id"])
    op.create_index("ix_strategy_calibration_jobs_status", "strategy_calibration_jobs", ["status"])
    op.create_table(
        "strategy_calibration_productions",
        sa.Column("production_id", sa.String(128), primary_key=True),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("producer_hash", sa.String(64), nullable=False),
        sa.Column("sample_dataset_hash", sa.String(64)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("report_json", sa.Text()),
        sa.Column("report_hash", sa.String(64)),
        sa.UniqueConstraint("job_id", "inputs_hash", "producer_hash", name="uq_calibration_production_inputs"),
    )
    op.create_index("ix_strategy_calibration_productions_job_id", "strategy_calibration_productions", ["job_id"])
    op.create_index("ix_strategy_calibration_productions_scope_hash", "strategy_calibration_productions", ["scope_hash"])
    op.create_index("ix_strategy_calibration_productions_state", "strategy_calibration_productions", ["state"])
    op.create_table(
        "strategy_probability_activations",
        sa.Column("activation_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("instrument", sa.String(128), nullable=False),
        sa.Column("pack_definition_hash", sa.String(64), nullable=False),
        sa.Column("compiled_policy_recipe_hash", sa.String(64), nullable=False),
        sa.Column("server_known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_probability_activation_scope", "strategy_probability_activations", ["market", "instrument", "pack_definition_hash", "compiled_policy_recipe_hash", "valid_from", "valid_until"])


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("strategy_probability_activations", "strategy_calibration_productions",
                  "strategy_calibration_jobs"):
        if connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one():
            raise RuntimeError("calibration_history_downgrade_forbidden")
    op.drop_table("strategy_probability_activations")
    op.drop_table("strategy_calibration_productions")
    op.drop_table("strategy_calibration_jobs")
