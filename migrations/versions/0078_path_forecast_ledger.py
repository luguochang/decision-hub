"""Add finite path prediction/label/evaluation ledger without altering old data."""

import sqlalchemy as sa
from alembic import op

revision = "0078_path_forecast_ledger"
down_revision = "0077_run_spot_admissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_shadow_trials",
        sa.Column("trial_id", sa.String(80), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("registration_hash", sa.String(64), nullable=False),
        sa.Column("protocol_json", sa.Text(), nullable=False),
        sa.Column("protocol_hash", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conclusion", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "state IN ('registered','running','paused','draining','completed','failed')",
            name="ck_risk_shadow_trial_state",
        ),
    )
    op.create_table(
        "risk_path_jobs",
        sa.Column("job_id", sa.String(80), primary_key=True),
        sa.Column("trial_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("trial_id", "kind", "scope_id", "cutoff_at", name="uq_risk_path_job"),
        sa.CheckConstraint(
            "kind IN ('fit','forecast','label','finalize')", name="ck_risk_path_kind"
        ),
        sa.CheckConstraint(
            "state IN ('pending','running','succeeded','missed','failed','expired')",
            name="ck_risk_path_job_state",
        ),
        sa.CheckConstraint("lease_generation >= 0 AND attempts >= 0", name="ck_risk_path_lease"),
    )
    op.create_index(
        "ix_risk_path_job_due",
        "risk_path_jobs",
        [
            "state",
            "next_attempt_at",
            "lease_expires_at",
        ],
    )
    op.create_table(
        "risk_predictions",
        sa.Column("prediction_id", sa.String(80), primary_key=True),
        sa.Column("trial_id", sa.String(80), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_seconds", sa.Integer(), nullable=False),
        sa.Column("target_version", sa.String(64), nullable=False),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("feature_manifest_json", sa.Text(), nullable=False),
        sa.Column("feature_manifest_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "trial_id",
            "scope_id",
            "method",
            "cutoff_at",
            "horizon_seconds",
            "target_version",
            name="uq_risk_path_prediction",
        ),
    )
    op.create_index("ix_risk_prediction_scope_time", "risk_predictions", ["scope_id", "cutoff_at"])
    op.create_table(
        "risk_path_observations",
        sa.Column("observation_id", sa.String(80), primary_key=True),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculator_version", sa.String(64), nullable=False),
        sa.Column("path_hash", sa.String(64), nullable=False),
        sa.Column("label_known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "scope_id",
            "cutoff_at",
            "calculator_version",
            "payload_hash",
            name="uq_risk_path_observation",
        ),
    )
    op.create_index(
        "ix_risk_path_observation_known",
        "risk_path_observations",
        [
            "scope_id",
            "cutoff_at",
            "label_known_at",
        ],
    )
    op.create_table(
        "risk_evaluation_reports",
        sa.Column("report_id", sa.String(80), primary_key=True),
        sa.Column("trial_id", sa.String(80), nullable=False),
        sa.Column("report_version", sa.String(64), nullable=False),
        sa.Column("protocol_hash", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("trial_id", "report_version", name="uq_risk_path_evaluation"),
    )


def downgrade() -> None:
    raise RuntimeError("path_forecast_audit_downgrade_forbidden_restore_verified_backup")
