"""Durable effect-tracking acceptance; preserve all existing final receipts."""

from alembic import op
import sqlalchemy as sa

revision = "0071_study_effect_tracking_request_jobs"
down_revision = "0070_strategy_calibration_productions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_effect_tracking_request_jobs",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("study_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("source_run_id", sa.String(128), nullable=False),
        sa.Column("artifact_id", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.CheckConstraint("state IN ('accepted', 'running', 'retrying', 'completed', 'failed')",
                           name="ck_effect_tracking_request_state"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_effect_tracking_request_attempts"),
    )
    op.create_index("ix_effect_tracking_requests_due", "study_effect_tracking_request_jobs",
                    ["state", "next_attempt_at", "request_id"])
    op.create_index("ix_effect_tracking_requests_owner_study", "study_effect_tracking_request_jobs",
                    ["owner_id", "study_id", "accepted_at"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM study_effect_tracking_request_jobs")).scalar_one():
        raise RuntimeError("effect_tracking_request_history_downgrade_forbidden")
    op.drop_table("study_effect_tracking_request_jobs")
