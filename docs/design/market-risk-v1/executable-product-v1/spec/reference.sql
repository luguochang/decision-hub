-- In-memory design simulator only. NOT an Alembic production migration.
CREATE TABLE heads (
  plan_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision >= 1)
);
CREATE TABLE revisions (
  plan_id TEXT NOT NULL REFERENCES heads(plan_id),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  content_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  PRIMARY KEY (plan_id, revision)
);
CREATE TRIGGER immutable_revision_update BEFORE UPDATE ON revisions
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_REVISION'); END;
CREATE TRIGGER immutable_revision_delete BEFORE DELETE ON revisions
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_REVISION'); END;
CREATE TABLE commands (
  request_key TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL,
  receipt_json TEXT NOT NULL CHECK (json_valid(receipt_json))
);
CREATE TABLE outbox (
  event_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  FOREIGN KEY (plan_id, revision) REFERENCES revisions(plan_id, revision)
);
