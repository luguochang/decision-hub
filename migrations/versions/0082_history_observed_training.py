"""Isolated held history-at-fit registration/dataset ledgers; no legacy mutation."""

import sqlalchemy as sa
from alembic import op

revision = "0082_history_observed_training"
down_revision = "0081_raw_capture_lookup_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_history_training_records",
        sa.Column("record_id", sa.String(80), primary_key=True),
        sa.Column("registration_id", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("original_ref", sa.String(2048), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "registration_id", "kind", "scope_id", name="uq_history_observed_record"
        ),
        sa.UniqueConstraint("request_id", "kind", "scope_id", name="uq_history_observed_request"),
        sa.CheckConstraint(
            "kind IN ('protocol','dataset','model')", name="ck_history_observed_kind"
        ),
    )
    op.create_table(
        "market_history_acquisition_jobs",
        sa.Column("job_id", sa.String(80), primary_key=True),
        sa.Column("registration_id", sa.String(80), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("protocol_hash", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "registration_id", "scope_id", "cutoff_at", name="uq_history_observed_job"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_history_observed_attempts"),
        sa.CheckConstraint(
            "state IN ('held','pending','running','completed','partial','failed','expired')",
            name="ck_history_observed_state",
        ),
        sa.CheckConstraint("lease_generation >= 0", name="ck_history_observed_lease_generation"),
    )


def downgrade() -> None:
    for table in ("market_history_training_records", "market_history_acquisition_jobs"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise ValueError("history_observed_nonempty_originals_preserved_on_rollback")
    op.drop_table("market_history_acquisition_jobs")
    op.drop_table("market_history_training_records")
