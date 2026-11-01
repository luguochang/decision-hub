"""V2 notification subjects and fenced delivery claims; preserve report rows."""

import sqlalchemy as sa
from alembic import op

revision = "0072_market_risk_outbox"
down_revision = "0071_study_effect_tracking_request_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outbox") as batch:
        batch.alter_column("artifact_id", existing_type=sa.String(128), nullable=True)
        batch.add_column(
            sa.Column("subject_kind", sa.String(32), nullable=False, server_default="report")
        )
        batch.add_column(sa.Column("subject_id", sa.String(128)))
        batch.add_column(sa.Column("payload_json", sa.Text()))
        batch.add_column(sa.Column("payload_hash", sa.String(64)))
        batch.add_column(
            sa.Column("dispatch_status", sa.String(32), nullable=False, server_default="queued")
        )
        batch.add_column(sa.Column("claim_owner", sa.String(128)))
        batch.add_column(
            sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("provider_message_id", sa.String(256)))
        batch.add_column(sa.Column("provider_accepted_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("attempt_history_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.create_check_constraint(
            "ck_outbox_subject_identity",
            "(subject_kind = 'report' AND artifact_id IS NOT NULL AND "
            "(subject_id IS NULL OR subject_id = artifact_id)) OR "
            "(subject_kind = 'risk_alert' AND artifact_id IS NULL AND "
            "subject_id IS NOT NULL AND payload_json IS NOT NULL AND payload_hash IS NOT NULL)",
        )
        batch.create_check_constraint("ck_outbox_claim_generation", "claim_generation >= 0")
        batch.create_index(
            "ix_outbox_due_claim", ["sent_at", "failed_at", "next_attempt_at", "claim_expires_at"]
        )
    op.execute(
        sa.text(
            "UPDATE outbox SET subject_id = artifact_id, dispatch_status = "
            "CASE WHEN sent_at IS NOT NULL THEN 'legacy_sent' "
            "WHEN failed_at IS NOT NULL THEN 'failed' ELSE 'queued' END"
        )
    )


def downgrade() -> None:
    incompatible = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM outbox "
                "WHERE subject_kind <> 'report' OR claim_owner IS NOT NULL "
                "OR payload_json IS NOT NULL OR payload_hash IS NOT NULL "
                "OR provider_accepted_at IS NOT NULL OR provider_message_id IS NOT NULL "
                "OR attempt_history_json <> '[]'"
            )
        )
        .scalar_one()
    )
    if incompatible:
        raise RuntimeError("notification_subject_history_downgrade_forbidden")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_index("ix_outbox_due_claim")
        batch.drop_constraint("ck_outbox_subject_identity", type_="check")
        batch.drop_constraint("ck_outbox_claim_generation", type_="check")
        for name in (
            "subject_kind",
            "subject_id",
            "payload_json",
            "payload_hash",
            "dispatch_status",
            "claim_owner",
            "claim_generation",
            "claim_expires_at",
            "provider_message_id",
            "provider_accepted_at",
            "attempt_history_json",
        ):
            batch.drop_column(name)
        batch.alter_column("artifact_id", existing_type=sa.String(128), nullable=False)
