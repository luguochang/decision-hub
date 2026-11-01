"""Private artifact SQLite behavior ledger; no production migration registration.

Each write is short and completed before any model work. The optional lease
fence is composition-owned and is called in that same writer transaction.
"""
from packages.kernel.decision_hub_kernel.persistence.db import Base as BehaviorBase
from packages.kernel.decision_hub_kernel.ports.research_learning import BehaviorAttempt, BehaviorOutput, BehaviorProtocol, BehaviorReceipt
from sqlalchemy import CheckConstraint, DDL, String, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from packages.kernel.decision_hub_kernel.application.research_skill_package import encoded, sha
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

class BehaviorOriginal(BehaviorBase):
    __tablename__ = 'research_skill_behavior_originals'
    __table_args__ = (CheckConstraint("kind IN ('protocol','attempt','output','receipt')"),)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    identity: Mapped[str] = mapped_column(String(128), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(128), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
for operation in ('UPDATE', 'DELETE'):
    event.listen(BehaviorOriginal.__table__, 'after_create', DDL(f"CREATE TRIGGER behavior_no_{operation.lower()} BEFORE {operation} ON research_skill_behavior_originals BEGIN SELECT RAISE(ABORT,'behavior_original_immutable'); END").execute_if(dialect='sqlite'))
event.listen(BehaviorOriginal.__table__, 'after_create', DDL("CREATE TRIGGER behavior_no_replace BEFORE INSERT ON research_skill_behavior_originals WHEN EXISTS(SELECT 1 FROM research_skill_behavior_originals WHERE kind=NEW.kind AND identity=NEW.identity) BEGIN SELECT RAISE(ABORT,'behavior_original_immutable'); END").execute_if(dialect='sqlite'))
TYPES = {'protocol': BehaviorProtocol, 'attempt': BehaviorAttempt, 'output': BehaviorOutput, 'receipt': BehaviorReceipt}

class BehaviorStore:

    def __init__(self, database, *, clock, writer_fence):
        self.database, self.clock, self.writer_fence = (database, clock, writer_fence)

    def read_in_session(self, session, kind, identity):
        row = session.get(BehaviorOriginal, (kind, identity), populate_existing=True)
        if row is None:
            return None
        value = TYPES[kind].model_validate_json(row.payload_json)
        wire = encoded(value.model_dump(mode='json'))
        if wire.decode() != row.payload_json or sha(wire) != row.payload_hash:
            raise ValueError('behavior_store_original_changed')
        expected_id = value.id if kind == 'protocol' else value.key if kind == 'attempt' else value.attempt.ref if kind == 'output' else value.protocol.ref
        expected_protocol = value.id if kind == 'protocol' else value.protocol.ref if kind != 'output' else self.read_in_session(session, 'attempt', identity).protocol.ref
        if identity != expected_id or row.protocol_id != expected_protocol:
            raise ValueError('behavior_store_projection_changed')
        return value

    def _read(self, kind, identity):
        with self.database.session() as session:
            return self.read_in_session(session, kind, identity)

    def read(self, protocol_id):
        value = self._read('protocol', protocol_id)
        if value is None:
            raise ValueError('behavior_protocol_missing')
        return value

    def read_attempt(self, key):
        return self._read('attempt', key)

    def read_output(self, key):
        return self._read('output', key)

    def receipt(self, protocol_id):
        return self._read('receipt', protocol_id)

    def _append(self, kind, identity, protocol_id, value):
        with self.database.session() as session:
            begin_immediate(session)
            self.writer_fence(session, protocol_id)
            old = self.read_in_session(session, kind, identity)
            if old is not None:
                if kind != 'attempt' and old != value:
                    raise ValueError('behavior_original_conflict')
                return (old, False)
            wire = encoded(value.model_dump(mode='json'))
            session.add(BehaviorOriginal(kind=kind, identity=identity, protocol_id=protocol_id, payload_json=wire.decode(), payload_hash=sha(wire)))
            session.flush()
            return (self.read_in_session(session, kind, identity), True)

    def register_protocol(self, value):
        value = BehaviorProtocol.model_validate(value.model_dump(mode='json'))
        if value.registered_at != self.clock():
            raise ValueError('behavior_registration_clock_mismatch')
        return self._append('protocol', value.id, value.id, value)[0]

    def append_attempt(self, value):
        protocol = self.read(value.protocol.ref)
        if value.protocol.hash != canonical_hash(protocol.model_dump(mode='json')):
            raise ValueError('behavior_attempt_protocol_changed')
        if value.created_at < protocol.registered_at or value.created_at > self.clock():
            raise ValueError('behavior_attempt_clock_invalid')
        return self._append('attempt', value.key, protocol.id, value)

    def append_output(self, key, value):
        attempt = self.read_attempt(key)
        if attempt is None or value.attempt.hash != canonical_hash(attempt.model_dump(mode='json')):
            raise ValueError('behavior_output_attempt_changed')
        return self._append('output', key, attempt.protocol.ref, value)[0]

    def append_receipt(self, protocol_id, value):
        return self._append('receipt', protocol_id, protocol_id, value)[0]
