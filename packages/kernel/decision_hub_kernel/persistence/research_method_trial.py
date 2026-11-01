"""Artifact-only trial storage. No production migration or implicit commit."""
from packages.kernel.decision_hub_kernel.persistence.db import Base as TrialBase
from sqlalchemy import DDL, CheckConstraint, Integer, String, Text, event, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from packages.kernel.decision_hub_kernel.ports.research_learning import MethodTrialProtocol, MethodTrialReceipt
from packages.kernel.decision_hub_kernel.application.research_skill_package import encoded
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

class TrialOriginal(TrialBase):
    __tablename__ = 'research_method_trial_originals'
    __table_args__ = (CheckConstraint("kind IN ('protocol','receipt')"),)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    identity: Mapped[str] = mapped_column(String(128), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(128), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)

class TrialPointer(TrialBase):
    __tablename__ = 'research_method_trial_pointers'
    __table_args__ = (CheckConstraint('version >= 1'),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    receipt_id: Mapped[str] = mapped_column(String(128))
    receipt_hash: Mapped[str] = mapped_column(String(64))
for operation in ('UPDATE', 'DELETE'):
    event.listen(TrialOriginal.__table__, 'after_create', DDL(f"CREATE TRIGGER trial_no_{operation.lower()} BEFORE {operation} ON research_method_trial_originals BEGIN SELECT RAISE(ABORT,'trial_original_immutable'); END").execute_if(dialect='sqlite'))
event.listen(TrialOriginal.__table__, 'after_create', DDL("CREATE TRIGGER trial_no_replace BEFORE INSERT ON research_method_trial_originals WHEN EXISTS(SELECT 1 FROM research_method_trial_originals WHERE kind=NEW.kind AND identity=NEW.identity) BEGIN SELECT RAISE(ABORT,'trial_original_immutable'); END").execute_if(dialect='sqlite'))

def pointer_key(protocol):
    return canonical_hash([protocol.scope_id, protocol.horizon, protocol.target.model_dump(mode='json')])

class TrialRepository:

    def __init__(self, session):
        self.session = session

    def read(self, kind, identity):
        row = self.session.get(TrialOriginal, (kind, identity), populate_existing=True)
        if row is None:
            return None
        cls = MethodTrialProtocol if kind == 'protocol' else MethodTrialReceipt
        value = cls.model_validate_json(row.payload_json)
        if value.id != row.identity or value.payload_hash != row.payload_hash or canonical_hash(value.model_dump(mode='json', exclude={'payload_hash'})) != row.payload_hash or (encoded(value.model_dump(mode='json')).decode() != row.payload_json) or (row.protocol_id != (value.id if kind == 'protocol' else value.protocol.ref)):
            raise ValueError('trial_original_projection_mismatch')
        return value

    def writer(self):
        connection = self.session.connection()
        if connection.dialect.name == 'sqlite' and (not connection.connection.driver_connection.in_transaction):
            raise ValueError('trial_explicit_writer_required')

    def append(self, kind, value):
        self.writer()
        old = self.read(kind, value.id)
        if old is not None:
            if old != value:
                raise ValueError('trial_original_conflict')
            return old
        self.session.add(TrialOriginal(kind=kind, identity=value.id, protocol_id=value.id if kind == 'protocol' else value.protocol.ref, payload_hash=value.payload_hash, payload_json=encoded(value.model_dump(mode='json')).decode()))
        self.session.flush()
        return self.read(kind, value.id)

    def pointer(self, protocol):
        row = self.session.get(TrialPointer, pointer_key(protocol), populate_existing=True)
        if row is None:
            return None
        receipt = self.read('receipt', row.receipt_id)
        if receipt is None or receipt.payload_hash != row.receipt_hash:
            raise ValueError('trial_pointer_original_missing')
        pointed = self.read('protocol', receipt.protocol.ref)
        if pointed is None or pointer_key(pointed) != row.key:
            raise ValueError('trial_pointer_scope_mismatch')
        return (row.version, row.receipt_id, row.receipt_hash)

    def point(self, protocol, receipt, expected):
        self.writer()
        actual = self.pointer(protocol)
        if actual != expected:
            raise ValueError('trial_pointer_cas_lost')
        key = pointer_key(protocol)
        if expected is None:
            self.session.add(TrialPointer(key=key, version=1, receipt_id=receipt.id, receipt_hash=receipt.payload_hash))
        else:
            count = self.session.execute(update(TrialPointer).where(TrialPointer.key == key, TrialPointer.version == expected[0], TrialPointer.receipt_id == expected[1], TrialPointer.receipt_hash == expected[2]).values(version=expected[0] + 1, receipt_id=receipt.id, receipt_hash=receipt.payload_hash)).rowcount
            if count != 1:
                raise ValueError('trial_pointer_cas_lost')
        self.session.flush()
