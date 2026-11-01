"""Schedule legacy closed manual-paper episodes for funding reconciliation."""

import sqlalchemy as sa
from alembic import op

revision = "0103_perp_funding_reconcile"
down_revision = "0102_perpetual_research_schedules"
branch_labels = None
depends_on = None


_MANUAL_CLOSED = """
active = 0
AND remaining_nonzero = 0
AND EXISTS (
    SELECT 1
    FROM action_execution_episodes AS episode
    WHERE episode.episode_id = action_execution_episode_heads.episode_id
      AND episode.version = action_execution_episode_heads.version
      AND episode.payload_json LIKE '%"schema_version":"perpetual-manual-episode.v1"%'
)
"""


def upgrade():
    # Only the mutable queue pointer changes. Immutable episode, outcome and
    # paper-ledger originals remain untouched and are reauthenticated by the tick.
    op.get_bind().execute(
        sa.text(
            "UPDATE action_execution_episode_heads "
            "SET next_due_at = CURRENT_TIMESTAMP "
            f"WHERE next_due_at IS NULL AND {_MANUAL_CLOSED}"
        )
    )


def downgrade():
    op.get_bind().execute(
        sa.text(
            "UPDATE action_execution_episode_heads "
            "SET next_due_at = NULL "
            f"WHERE next_due_at IS NOT NULL AND {_MANUAL_CLOSED}"
        )
    )
