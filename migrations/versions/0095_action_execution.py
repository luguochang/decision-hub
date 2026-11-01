"""Prospective diagnostic execution originals; static, independently frozen DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0095_action_execution"
down_revision = "0094_action_quotes"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE action_execution_episode_heads ( portfolio_id VARCHAR(128) NOT NULL, episode_id
VARCHAR(128) NOT NULL, version INTEGER NOT NULL, payload_hash VARCHAR(64) NOT NULL, scope_id
VARCHAR(200) NOT NULL, active BOOLEAN NOT NULL, remaining_nonzero BOOLEAN NOT NULL,
next_due_at DATETIME, quote_cursor_available_at DATETIME, quote_cursor_received_at DATETIME,
quote_cursor_capture_id VARCHAR(128), PRIMARY KEY (portfolio_id), CONSTRAINT
ck_execution_episode_head_version CHECK (version >= 1), CONSTRAINT
ck_execution_episode_open_quantity CHECK (active = 0 OR remaining_nonzero = 1), CONSTRAINT
ck_execution_episode_open_due CHECK (active = 0 OR next_due_at IS NOT NULL), CONSTRAINT
ck_execution_episode_quote_cursor_complete CHECK ((quote_cursor_available_at IS NULL AND
quote_cursor_received_at IS NULL AND quote_cursor_capture_id IS NULL) OR
(quote_cursor_available_at IS NOT NULL AND quote_cursor_received_at IS NOT NULL AND
quote_cursor_capture_id IS NOT NULL)), CHECK (active IN (0, 1)), CHECK (remaining_nonzero IN
(0, 1)) )""",
    """CREATE TABLE action_execution_episodes ( episode_id VARCHAR(128) NOT NULL, version INTEGER
NOT NULL, portfolio_id VARCHAR(128) NOT NULL, payload_json TEXT NOT NULL, payload_hash
VARCHAR(64) NOT NULL, PRIMARY KEY (episode_id, version), CONSTRAINT
ck_execution_episode_version CHECK (version >= 1) )""",
    """CREATE TABLE action_execution_fills ( fill_id VARCHAR(128) NOT NULL, intent_id VARCHAR(128)
NOT NULL, portfolio_id VARCHAR(128) NOT NULL, episode_id VARCHAR(128) NOT NULL, executed_at
DATETIME NOT NULL, payload_json TEXT NOT NULL, payload_hash VARCHAR(64) NOT NULL, PRIMARY
KEY (fill_id), UNIQUE (intent_id) )""",
    """CREATE TABLE action_execution_intents ( intent_id VARCHAR(128) NOT NULL, portfolio_id
VARCHAR(128) NOT NULL, protocol_id VARCHAR(128) NOT NULL, scope_id VARCHAR(200) NOT NULL,
event_key VARCHAR(200) NOT NULL, plan_id VARCHAR(128) NOT NULL, revision_id VARCHAR(128) NOT
NULL, phase VARCHAR(16) NOT NULL, eligible_at DATETIME NOT NULL, quote_deadline DATETIME NOT
NULL, payload_json TEXT NOT NULL, payload_hash VARCHAR(64) NOT NULL, PRIMARY KEY
(intent_id), CONSTRAINT uq_execution_portfolio_event UNIQUE (portfolio_id, event_key),
CONSTRAINT ck_execution_intent_phase CHECK (phase IN ('entry','reduce','exit')), CONSTRAINT
ck_execution_intent_window CHECK (quote_deadline > eligible_at) )""",
    """CREATE TABLE action_execution_observation_heads ( portfolio_id VARCHAR(128) NOT NULL,
source_kind VARCHAR(16) NOT NULL, source_id VARCHAR(128) NOT NULL, version INTEGER NOT NULL,
observation_id VARCHAR(128) NOT NULL, payload_hash VARCHAR(64) NOT NULL, next_due_at
DATETIME, PRIMARY KEY (portfolio_id, source_kind, source_id), CONSTRAINT
ck_execution_observation_head_version CHECK (version >= 1) )""",
    """CREATE TABLE action_execution_observations ( observation_id VARCHAR(128) NOT NULL,
portfolio_id VARCHAR(128) NOT NULL, source_kind VARCHAR(16) NOT NULL, source_id VARCHAR(128)
NOT NULL, opportunity_id VARCHAR(128) NOT NULL, version INTEGER NOT NULL, status VARCHAR(32)
NOT NULL, observed_at DATETIME NOT NULL, payload_json TEXT NOT NULL, payload_hash
VARCHAR(64) NOT NULL, PRIMARY KEY (observation_id), CONSTRAINT
uq_execution_observation_version UNIQUE (portfolio_id, source_kind, source_id, version),
CONSTRAINT ck_execution_observation_version CHECK (version >= 1), CONSTRAINT
ck_execution_observation_source CHECK (source_kind IN ('evaluation','opportunity')),
CONSTRAINT ck_execution_observation_status CHECK (status IN
('intent_created','not_actionable','waiting_display','blocked','unknown','no_plan')) )""",
    """CREATE TABLE action_execution_portfolio_cursors ( portfolio_id VARCHAR(128) NOT NULL,
polled_at DATETIME NOT NULL, last_observation_id VARCHAR(128), PRIMARY KEY (portfolio_id) )""",
    """CREATE TABLE action_execution_protocols ( protocol_id VARCHAR(128) NOT NULL, trial_id
VARCHAR(128) NOT NULL, registered_at DATETIME NOT NULL, start_at DATETIME NOT NULL, end_at
DATETIME NOT NULL, payload_json TEXT NOT NULL, payload_hash VARCHAR(64) NOT NULL, PRIMARY
KEY (protocol_id), CONSTRAINT ck_execution_protocol_window CHECK (registered_at <= start_at
AND start_at < end_at), UNIQUE (payload_hash) )""",
    """CREATE TABLE action_execution_receipts ( receipt_id VARCHAR(128) NOT NULL, intent_id
VARCHAR(128) NOT NULL, portfolio_id VARCHAR(128) NOT NULL, status VARCHAR(16) NOT NULL,
decided_at DATETIME NOT NULL, payload_json TEXT NOT NULL, payload_hash VARCHAR(64) NOT NULL,
PRIMARY KEY (receipt_id), CONSTRAINT ck_execution_receipt_status CHECK (status IN
('filled','no_fill','rejected','unknown')), UNIQUE (intent_id) )""",
    """CREATE TABLE action_paper_accounts ( account_id VARCHAR(80) NOT NULL, identity_json TEXT NOT
NULL, version INTEGER NOT NULL, checkpoint_hash VARCHAR(64) NOT NULL, PRIMARY KEY
(account_id), CHECK (version >= 1) )""",
    """CREATE TABLE action_paper_checkpoints ( checkpoint_hash VARCHAR(64) NOT NULL, account_id
VARCHAR(80) NOT NULL, version INTEGER NOT NULL, execution_key VARCHAR(128) NOT NULL,
request_hash VARCHAR(64) NOT NULL, previous_hash VARCHAR(64), payload_json TEXT NOT NULL,
PRIMARY KEY (checkpoint_hash), UNIQUE (account_id, version), UNIQUE (account_id,
execution_key), CHECK (version >= 1) )""",
    """CREATE TABLE action_paper_openings ( opening_id VARCHAR(128) NOT NULL, protocol_id
VARCHAR(128) NOT NULL, scope_id VARCHAR(200) NOT NULL, payload_json TEXT NOT NULL,
payload_hash VARCHAR(64) NOT NULL, PRIMARY KEY (opening_id), CONSTRAINT
uq_paper_opening_protocol_scope UNIQUE (protocol_id, scope_id) )""",
    """CREATE INDEX ix_action_execution_protocols_trial_id ON action_execution_protocols
(trial_id)""",
    """CREATE INDEX ix_execution_episode_due ON action_execution_episode_heads (active,
next_due_at, scope_id)""",
    """CREATE INDEX ix_execution_episode_portfolio ON action_execution_episodes (portfolio_id,
episode_id, version)""",
    """CREATE INDEX ix_execution_episode_remaining ON action_execution_episode_heads (scope_id,
remaining_nonzero, next_due_at)""",
    """CREATE INDEX ix_execution_fill_episode_time ON action_execution_fills (episode_id,
executed_at)""",
    """CREATE INDEX ix_execution_intent_due ON action_execution_intents (eligible_at,
quote_deadline, scope_id)""",
    """CREATE INDEX ix_execution_intent_revision ON action_execution_intents
(plan_id, revision_id)""",
    """CREATE INDEX ix_execution_observation_denominator ON action_execution_observations
(portfolio_id, opportunity_id, observed_at)""",
    """CREATE INDEX ix_execution_observation_due ON action_execution_observation_heads
(next_due_at, portfolio_id)""",
    """CREATE INDEX ix_execution_portfolio_cursor_fair ON action_execution_portfolio_cursors
(polled_at, portfolio_id)""",
    """CREATE INDEX ix_execution_receipt_portfolio_time ON action_execution_receipts (portfolio_id,
decided_at)""",
    """CREATE TRIGGER action_execution_episodes_immutable_delete BEFORE DELETE ON
action_execution_episodes BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_episodes_immutable_insert BEFORE INSERT ON
action_execution_episodes WHEN EXISTS (SELECT 1 FROM action_execution_episodes WHERE
(episode_id=NEW.episode_id AND version=NEW.version)) BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_episodes_immutable_update BEFORE UPDATE ON
action_execution_episodes BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_fills_immutable_delete BEFORE DELETE ON
action_execution_fills BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_fills_immutable_insert BEFORE INSERT ON
action_execution_fills WHEN EXISTS (SELECT 1 FROM action_execution_fills WHERE
(fill_id=NEW.fill_id) OR (intent_id=NEW.intent_id)) BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_fills_immutable_update BEFORE UPDATE ON
action_execution_fills BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_intents_immutable_delete BEFORE DELETE ON
action_execution_intents BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_intents_immutable_insert BEFORE INSERT ON
action_execution_intents WHEN EXISTS (SELECT 1 FROM action_execution_intents WHERE
(intent_id=NEW.intent_id) OR (portfolio_id=NEW.portfolio_id AND event_key=NEW.event_key))
BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_intents_immutable_update BEFORE UPDATE ON
action_execution_intents BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_observations_immutable_delete BEFORE DELETE ON
action_execution_observations BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_observations_immutable_insert BEFORE INSERT ON
action_execution_observations WHEN EXISTS (SELECT 1 FROM action_execution_observations WHERE
(observation_id=NEW.observation_id) OR (portfolio_id=NEW.portfolio_id AND
source_kind=NEW.source_kind AND source_id=NEW.source_id AND version=NEW.version)) BEGIN
SELECT RAISE(ABORT, 'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_observations_immutable_update BEFORE UPDATE ON
action_execution_observations BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_protocols_immutable_delete BEFORE DELETE ON
action_execution_protocols BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_protocols_immutable_insert BEFORE INSERT ON
action_execution_protocols WHEN EXISTS (SELECT 1 FROM action_execution_protocols WHERE
(protocol_id=NEW.protocol_id) OR (payload_hash=NEW.payload_hash)) BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_protocols_immutable_update BEFORE UPDATE ON
action_execution_protocols BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_receipts_immutable_delete BEFORE DELETE ON
action_execution_receipts BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_execution_receipts_immutable_insert BEFORE INSERT ON
action_execution_receipts WHEN EXISTS (SELECT 1 FROM action_execution_receipts WHERE
(receipt_id=NEW.receipt_id) OR (intent_id=NEW.intent_id)) BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_execution_receipts_immutable_update BEFORE UPDATE ON
action_execution_receipts BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable');
END""",
    """CREATE TRIGGER action_paper_openings_immutable_delete BEFORE DELETE ON action_paper_openings
BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_paper_openings_immutable_insert BEFORE INSERT ON action_paper_openings
WHEN EXISTS (SELECT 1 FROM action_paper_openings WHERE (opening_id=NEW.opening_id) OR
(protocol_id=NEW.protocol_id AND scope_id=NEW.scope_id)) BEGIN SELECT RAISE(ABORT,
'action_execution_original_immutable'); END""",
    """CREATE TRIGGER action_paper_openings_immutable_update BEFORE UPDATE ON action_paper_openings
BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END""",
    """CREATE TRIGGER paper_checkpoint_no_delete BEFORE DELETE ON action_paper_checkpoints BEGIN
SELECT RAISE(ABORT, 'paper_checkpoint_immutable'); END""",
    """CREATE TRIGGER paper_checkpoint_no_replace BEFORE INSERT ON action_paper_checkpoints WHEN
EXISTS (SELECT 1 FROM action_paper_checkpoints WHERE checkpoint_hash=NEW.checkpoint_hash OR
(account_id=NEW.account_id AND (version=NEW.version OR execution_key=NEW.execution_key)))
BEGIN SELECT RAISE(ABORT, 'paper_checkpoint_immutable'); END""",
    """CREATE TRIGGER paper_checkpoint_no_update BEFORE UPDATE ON action_paper_checkpoints BEGIN
SELECT RAISE(ABORT, 'paper_checkpoint_immutable'); END""",
)
TABLES = (
    "action_execution_episode_heads",
    "action_execution_episodes",
    "action_execution_fills",
    "action_execution_intents",
    "action_execution_observation_heads",
    "action_execution_observations",
    "action_execution_portfolio_cursors",
    "action_execution_protocols",
    "action_execution_receipts",
    "action_paper_accounts",
    "action_paper_checkpoints",
    "action_paper_openings",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    connection = op.get_bind()
    for table in TABLES:
        if connection.execute(sa.text('SELECT 1 FROM "' + table + '" LIMIT 1')).first():
            raise RuntimeError("action_execution_evidence_prevents_downgrade")
    for table in reversed(TABLES):
        op.execute('DROP TABLE "' + table + '"')
