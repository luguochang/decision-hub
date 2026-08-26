"""Add transactional notification outbox."""

from __future__ import annotations

from alembic import op
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    inspect,
)

revision = "0002_outbox"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "outbox" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "outbox",
            Column("id", Integer(), autoincrement=True, nullable=False),
            Column("artifact_id", String(length=128), nullable=False),
            Column("channel", String(length=64), nullable=False),
            Column("dedupe_key", String(length=256), nullable=False),
            Column("attempts", Integer(), nullable=False),
            Column("sent_at", DateTime(timezone=True), nullable=True),
            PrimaryKeyConstraint("id"),
            UniqueConstraint("dedupe_key"),
        )
        op.create_index("ix_outbox_artifact_id", "outbox", ["artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_artifact_id", table_name="outbox")
    op.drop_table("outbox")
