"""Initial Decision Hub R0 ledger.

Revision ID: 0001_initial
"""

from __future__ import annotations

from alembic import op

from packages.kernel.decision_hub_kernel.persistence.db import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
