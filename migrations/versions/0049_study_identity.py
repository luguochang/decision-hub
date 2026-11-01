"""Add study, occurrence, immutable report versions and discovery decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0049_study_identity"
down_revision = "0048_source_poll_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("study_id", sa.String(128), nullable=True))
    op.create_index("ix_runs_study_id", "runs", ["study_id"])
    op.create_table(
        "occurrences",
        sa.Column("occurrence_id", sa.String(128), primary_key=True),
        sa.Column("identity_key", sa.String(64), nullable=False, unique=True),
        sa.Column("identity_basis", sa.String(64), nullable=False),
        sa.Column("family", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("macro_event_id", sa.String(128), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "event_occurrences",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("occurrence_id", sa.String(128), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("basis", sa.String(64), nullable=False),
    )
    op.create_index("ix_event_occurrences_occurrence_id", "event_occurrences", ["occurrence_id"])
    op.create_table(
        "event_watch_links",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("watch_id", sa.String(128), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_watch_links_watch_id", "event_watch_links", ["watch_id"])
    op.create_table(
        "studies",
        sa.Column("study_id", sa.String(128), primary_key=True),
        sa.Column("scope_key", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("instrument", sa.String(128), nullable=False),
        sa.Column("horizon_scope", sa.String(128), nullable=False),
        sa.Column("question_key", sa.String(256), nullable=False),
        sa.Column("identity_basis", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "study_occurrences",
        sa.Column("study_id", sa.String(128), primary_key=True),
        sa.Column("occurrence_id", sa.String(128), primary_key=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "report_revisions",
        sa.Column("revision_id", sa.String(128), primary_key=True),
        sa.Column("study_id", sa.String(128), nullable=False),
        sa.Column("source_run_id", sa.String(128), nullable=False, unique=True),
        sa.Column("artifact_id", sa.String(128), nullable=False, unique=True),
        sa.Column("previous_revision_id", sa.String(128), nullable=True),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
        sa.Column("publication_status", sa.String(32), nullable=False),
        sa.Column("change_json", sa.Text(), nullable=False),
        sa.Column("identity_basis", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("study_id", "created_at"):
        op.create_index(f"ix_report_revisions_{column}", "report_revisions", [column])
    op.create_table(
        "admission_decisions",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("study_id", sa.String(128), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("attempt_id", "event_id", "study_id", "source_id", "decided_at"):
        op.create_index(f"ix_admission_decisions_{column}", "admission_decisions", [column])


def downgrade():
    for table in (
        "admission_decisions",
        "report_revisions",
        "study_occurrences",
        "studies",
        "event_occurrences",
        "event_watch_links",
        "occurrences",
    ):
        op.drop_table(table)
    op.drop_index("ix_runs_study_id", table_name="runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("study_id")
