"""Initial Decision Hub R0 ledger.

The initial revision must be a historical schema snapshot. It intentionally
does not import the current ORM metadata: new models belong to later, explicit
migrations and must never appear while upgrading to an older revision.

Revision ID: 0001_initial
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


metadata = sa.MetaData()

sa.Table(
    "events",
    metadata,
    sa.Column("event_id", sa.String(length=128), primary_key=True),
    sa.Column("event_type", sa.String(length=128), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("generation", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(length=32), nullable=False),
)
sa.Table(
    "observations",
    metadata,
    sa.Column("observation_id", sa.String(length=128), primary_key=True),
    sa.Column("event_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("source_id", sa.String(length=128), nullable=False),
    sa.Column("source_type", sa.String(length=32), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("language", sa.String(length=16), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("content_hash", sa.String(length=64), nullable=False, unique=True),
    sa.Column("source_url", sa.String(length=2048), nullable=True),
    sa.Column("event_hint", sa.String(length=128), nullable=True),
    sa.Column("revision_of", sa.String(length=128), nullable=True),
)
sa.Table(
    "snapshots",
    metadata,
    sa.Column("snapshot_id", sa.String(length=128), primary_key=True),
    sa.Column("event_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
    sa.Column("evidence_json", sa.Text(), nullable=False),
    sa.Column("pack_version", sa.String(length=64), nullable=False),
)
sa.Table(
    "runs",
    metadata,
    sa.Column("run_id", sa.String(length=128), primary_key=True),
    sa.Column("event_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("idempotency_key", sa.String(length=256), nullable=True, unique=True),
    sa.Column("snapshot_id", sa.String(length=128), nullable=True),
    sa.Column("status", sa.String(length=32), nullable=False),
    sa.Column("strategy_version", sa.String(length=64), nullable=False),
    sa.Column("runtime_version", sa.String(length=64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("latency_ms", sa.Integer(), nullable=True),
    sa.Column("cost_usd", sa.Float(), nullable=False),
    sa.Column("error_code", sa.String(length=128), nullable=True),
    sa.Column("artifact_id", sa.String(length=128), nullable=True),
)
sa.Table(
    "run_events",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("run_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("sequence_no", sa.Integer(), nullable=False),
    sa.Column("event_type", sa.String(length=128), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("payload_json", sa.Text(), nullable=False),
)
sa.Table(
    "artifacts",
    metadata,
    sa.Column("artifact_id", sa.String(length=128), primary_key=True),
    sa.Column("run_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("event_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("gate_status", sa.String(length=32), nullable=False),
    sa.Column("headline", sa.String(length=512), nullable=False),
    sa.Column("summary", sa.Text(), nullable=False),
    sa.Column("payload_json", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Table(
    "forecasts",
    metadata,
    sa.Column("forecast_id", sa.String(length=128), primary_key=True),
    sa.Column("artifact_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("instrument", sa.String(length=128), nullable=False),
    sa.Column("horizon", sa.String(length=16), nullable=False),
    sa.Column("direction", sa.String(length=16), nullable=False),
    sa.Column("probability", sa.Float(), nullable=False),
    sa.Column("trigger", sa.Text(), nullable=False),
    sa.Column("invalidation", sa.Text(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Table(
    "gate_decisions",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("run_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("artifact_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("rule_id", sa.String(length=128), nullable=False),
    sa.Column("status", sa.String(length=32), nullable=False),
    sa.Column("reason_code", sa.String(length=128), nullable=False),
    sa.Column("input_hash", sa.String(length=64), nullable=False),
)
sa.Table(
    "outcomes",
    metadata,
    sa.Column("outcome_id", sa.String(length=128), primary_key=True),
    sa.Column("forecast_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("direction_correct", sa.Boolean(), nullable=False),
    sa.Column("return_pct", sa.Float(), nullable=False),
    sa.Column("fees", sa.Float(), nullable=False),
    sa.Column("slippage", sa.Float(), nullable=False),
    sa.Column("quality_status", sa.String(length=32), nullable=False),
)
sa.Table(
    "evaluations",
    metadata,
    sa.Column("evaluation_id", sa.String(length=128), primary_key=True),
    sa.Column("forecast_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("brier_score", sa.Float(), nullable=False),
    sa.Column("net_return_pct", sa.Float(), nullable=False),
    sa.Column("direction_correct", sa.Boolean(), nullable=False),
    sa.Column("label_status", sa.String(length=32), nullable=False),
    sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Table(
    "outbox",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("artifact_id", sa.String(length=128), nullable=False, index=True),
    sa.Column("channel", sa.String(length=64), nullable=False),
    sa.Column("dedupe_key", sa.String(length=256), nullable=False, unique=True),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
