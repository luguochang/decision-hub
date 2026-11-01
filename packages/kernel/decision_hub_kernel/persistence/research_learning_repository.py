"""Immutable learning originals. Repository writes do not authenticate model/source DTOs."""
import hashlib
import json
from packages.kernel.decision_hub_kernel.ports.research_learning import LearningBinding, LearningProtocol, LearningStageReceipt, OriginalRef, aware
from packages.kernel.decision_hub_kernel.persistence.research_learning_records import LearningBindingRecord, LearningProtocolRecord, LearningStageRecord
from sqlalchemy import select
from packages.kernel.decision_hub_kernel.persistence.db import EvolutionJobRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
PHASES = ('context', 'dispatch', 'reflection', 'package', 'trial', 'consumed', 'comparison')

def encoded(value):
    body = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def seal(cls, body):
    parsed = cls.model_validate(body | {'payload_hash': '0' * 64})
    wire = parsed.model_dump(mode='json')
    wire['payload_hash'] = canonical_hash({key: value for key, value in wire.items() if key != 'payload_hash'})
    return cls.model_validate(wire)

def check(cls, raw, digest):
    value = cls.model_validate_json(raw)
    expected = seal(cls, value.model_dump(mode='json'))
    if expected != value or value.payload_hash != digest or encoded(value) != raw:
        raise ValueError('learning_original_hash_mismatch')
    return value

def reference(value):
    return OriginalRef(ref=value.id, hash=value.payload_hash)

def binding_identity(protocol, source_kind, source):
    digest = canonical_hash([protocol.payload_hash, source_kind, source.ref, source.hash])
    trigger = 'research-recipe.v1:' + digest
    return ('learning_' + digest, trigger, 'evolution_job_' + hashlib.sha256(trigger.encode()).hexdigest()[:24])

class LearningRepository:

    def __init__(self, session):
        self.session = session

    def writer(self):
        connection = self.session.connection()
        if connection.dialect.name == 'sqlite' and (not connection.connection.driver_connection.in_transaction):
            raise ValueError('learning_explicit_writer_required')

    def protocol(self, protocol_id):
        row = self.session.get(LearningProtocolRecord, protocol_id, populate_existing=True)
        if row is None:
            raise ValueError('learning_protocol_missing')
        value = check(LearningProtocol, row.payload_json, row.payload_hash)
        if row.protocol_id != value.id:
            raise ValueError('learning_protocol_projection_mismatch')
        return value

    def register_protocol(self, value, *, now):
        self.writer()
        check(LearningProtocol, encoded(value), value.payload_hash)
        if aware(now) != value.registered_at:
            raise ValueError('learning_protocol_registration_clock_mismatch')
        existing = self.session.get(LearningProtocolRecord, value.id, populate_existing=True)
        if existing:
            if self.protocol(value.id) != value:
                raise ValueError('learning_protocol_conflict')
            return value
        self.session.add(LearningProtocolRecord(protocol_id=value.id, payload_json=encoded(value), payload_hash=value.payload_hash))
        self.session.flush()
        return value

    def binding(self, job_id):
        row = self.session.scalar(select(LearningBindingRecord).where(LearningBindingRecord.job_id == job_id).execution_options(populate_existing=True))
        if row is None:
            return None
        value = check(LearningBinding, row.payload_json, row.payload_hash)
        protocol = self.protocol(value.protocol.ref)
        if reference(protocol) != value.protocol or (value.scope_id, value.horizon, value.parent_manifest) != (protocol.scope_id, protocol.horizon, protocol.parent_manifest):
            raise ValueError('learning_binding_protocol_mismatch')
        if value.frozen_at < protocol.registered_at:
            raise ValueError('learning_binding_before_protocol')
        identity = binding_identity(protocol, value.source_kind, value.source)
        if identity != (value.id, value.trigger_key, value.job_id) or (row.binding_id, row.protocol_id, row.source_kind, row.source_id, row.source_hash, as_utc(row.frozen_at)) != (value.id, protocol.id, value.source_kind, value.source.ref, value.source.hash, value.frozen_at):
            raise ValueError('learning_binding_projection_mismatch')
        return value

    def append_binding(self, value):
        self.writer()
        check(LearningBinding, encoded(value), value.payload_hash)
        old = self.binding(value.job_id)
        if old:
            if old != value:
                raise ValueError('learning_binding_conflict')
            return old
        self.session.add(LearningBindingRecord(binding_id=value.id, job_id=value.job_id, protocol_id=value.protocol.ref, source_kind=value.source_kind, source_id=value.source.ref, source_hash=value.source.hash, frozen_at=value.frozen_at, payload_json=encoded(value), payload_hash=value.payload_hash))
        self.session.flush()
        return self.binding(value.job_id)

    def owned(self, job_id, worker_id, attempt, *, now):
        row = self.session.get(EvolutionJobRecord, job_id, populate_existing=True)
        binding = self.binding(job_id)
        if row is None or binding is None:
            raise ValueError('learning_job_binding_missing')
        protocol = self.protocol(binding.protocol.ref)
        if row.trigger_type != 'scheduled' or row.domain_pack_ref != 'crypto_macro.v1' or row.trigger_key != binding.trigger_key or (row.candidate_id is not None) or (json.loads(row.input_refs_json) != ['research-learning-binding:' + binding.id + ':' + binding.payload_hash]) or (row.max_attempts != protocol.max_attempts):
            raise ValueError('learning_job_binding_mismatch')
        if row.status != 'running' or row.lease_owner != worker_id or row.attempt != attempt or (row.lease_expires_at is None) or (as_utc(row.lease_expires_at) <= aware(now)):
            raise PermissionError('learning_lease_fence_lost')
        return binding

    def stage(self, job_id, phase):
        row = self.session.scalar(select(LearningStageRecord).where(LearningStageRecord.job_id == job_id, LearningStageRecord.phase == phase).execution_options(populate_existing=True))
        if row is None:
            return None
        value = check(LearningStageReceipt, row.payload_json, row.payload_hash)
        binding = self.binding(job_id)
        if binding is None or value.binding != reference(binding) or (row.stage_id, row.job_id, row.phase) != (value.id, value.job_id, value.phase):
            raise ValueError('learning_stage_binding_mismatch')
        index = PHASES.index(phase)
        previous = self.stage(job_id, PHASES[index - 1]) if index else None
        if (reference(previous) if previous else None) != value.previous or (index and previous is None):
            raise ValueError('learning_stage_chain_mismatch')
        if value.recorded_at < (previous.recorded_at if previous else binding.frozen_at):
            raise ValueError('learning_stage_clock_regressed')
        return value

    def append_stage(self, *, job_id, worker_id, attempt, phase, payload, now):
        self.writer()
        binding = self.owned(job_id, worker_id, attempt, now=now)
        old = self.stage(job_id, phase)
        if old:
            if old.payload != payload:
                raise ValueError('learning_stage_conflict')
            return old
        index = PHASES.index(phase)
        previous = self.stage(job_id, PHASES[index - 1]) if index else None
        if index and previous is None:
            raise ValueError('learning_previous_stage_missing')
        if aware(now) < (previous.recorded_at if previous else binding.frozen_at):
            raise ValueError('learning_stage_clock_regressed')
        value = seal(LearningStageReceipt, dict(schema_version='research-learning-stage.v1', id='learning_stage_' + canonical_hash([job_id, phase]), job_id=job_id, binding=reference(binding), phase=phase, payload=payload, previous=reference(previous) if previous else None, job_attempt=attempt, recorded_at=now))
        self.session.add(LearningStageRecord(stage_id=value.id, job_id=job_id, phase=phase, payload_json=encoded(value), payload_hash=value.payload_hash))
        self.session.flush()
        return value
