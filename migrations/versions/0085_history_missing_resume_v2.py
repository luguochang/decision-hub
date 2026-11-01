"""Add immutable explicit V2 resume/reconcile audit; preserve nonempty data."""

from alembic import op
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.history_missing_resume_v2_records import (
    HistoryMissingResumeV2Record,
)

revision = "0085_history_missing_resume_v2"
down_revision = "0084_history_dataset_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    HistoryMissingResumeV2Record.__table__.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    if op.get_bind().execute(text("SELECT 1 FROM market_history_v2_resume_audits LIMIT 1")).first():
        raise ValueError("history_v2_nonempty_resume_audits_preserved")
    HistoryMissingResumeV2Record.__table__.drop(op.get_bind(), checkfirst=False)
