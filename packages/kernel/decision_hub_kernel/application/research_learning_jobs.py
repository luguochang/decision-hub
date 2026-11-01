"""Identity-only source intake and routing on the existing EvolutionJob lease queue."""
from __future__ import annotations
import hashlib
import json
from packages.kernel.decision_hub_kernel.ports.research_learning import LearningBinding, LearningSourceIdentity, OriginalRef, aware
from packages.kernel.decision_hub_kernel.persistence.research_learning_records import LearningBindingRecord
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import LearningRepository, binding_identity, encoded, reference, seal
from sqlalchemy import select, update
from packages.contracts_py.decision_hub_contracts.models import EvolutionJobCreate
from packages.kernel.decision_hub_kernel.persistence.db import EvolutionJobRecord
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.application.research_method_trial_sources import ActionTrialSourceReader

class LearningJobs:
    """Composition supplies real W6 service and archive; no caller-supplied verified result."""

    def __init__(self, database, jobs, sources, archive, *, clock):
        if jobs.database is not database:
            raise ValueError('learning_queue_database_mismatch')
        self.database, self.jobs, self.sources, self.archive, self.clock = (database, jobs, sources, archive, clock)
        self.source_preparer = ActionTrialSourceReader(sources.service)

    def _read_source(self, kind, source_id):
        if kind == 'action_outcome':
            originals = self.sources.read_outcome(source_id)
            value = originals['outcome'].model_dump(mode='json', by_alias=True)
            receipt = originals['execution_receipt'].model_dump(mode='json', by_alias=True)
            if value.get('schema_version') != 'action-outcome.v2' or receipt.get('schema_version') != 'action-execution-receipt.v1' or receipt.get('outcome_ref') != source_id or (receipt.get('outcome_hash') != value.get('payload_hash')):
                raise ValueError('learning_outcome_receipt_mismatch')
            bodies = {'outcome': value, 'execution_receipt': receipt}
            if originals.get('financial_qualified') is not False:
                raise ValueError('learning_source_qualification_mismatch')
            if 'research_originals' in originals:
                bodies['research_originals'] = {name: item.model_dump(mode='json', by_alias=True) if hasattr(item, 'model_dump') else item for name, item in originals['research_originals'].items()}
        elif kind == 'execution_observation':
            reader = getattr(self.sources, 'read_observation', None)
            if reader is None:
                raise ValueError('learning_observation_reader_unavailable')
            originals = reader(source_id)
            value = originals['observation']
            if hasattr(value, 'model_dump'):
                value = value.model_dump(mode='json', by_alias=True)
            bodies = {'observation': value}
            if originals.get('is_trade_outcome') is not False or value.get('is_trade_outcome') is not False:
                raise ValueError('learning_observation_must_remain_nontrade')
        else:
            raise ValueError('learning_source_kind_unsupported')
        identity = LearningSourceIdentity.model_validate(originals['source_identity'])
        if value.get('id') != source_id or identity.source != OriginalRef(ref=source_id, hash=value['payload_hash']):
            raise ValueError('learning_source_identity_mismatch')
        bodies['source_identity'] = identity.model_dump(mode='json')
        available = aware(originals['available_at'])
        return (identity, bodies, available)

    def enqueue_source(self, *, protocol_id, source_kind, source_id):
        with self.database.session() as session:
            protocol = LearningRepository(session).protocol(protocol_id)
        # An observation remains denominator evidence. It is not a mature unknown
        # settlement and cannot create a reflection job through this intake.
        if source_kind != 'action_outcome':
            raise ValueError('learning_observation_not_mature_for_intake')
        prepared_source = self.source_preparer.prepare(source_kind, source_id)
        identity, bodies, available = prepared_source.identity, prepared_source.bodies, prepared_source.available_at
        now = aware(self.clock())
        if identity.scope_id != protocol.scope_id or identity.horizon != protocol.horizon or identity.target != protocol.target or (identity.method != protocol.source_method) or (identity.evaluation_protocol != protocol.evaluation_protocol) or (available > now):
            raise ValueError('learning_source_protocol_mismatch')
        source = identity.source
        identity, trigger, job_id = binding_identity(protocol, source_kind, source)
        with self.database.session() as session:
            old = LearningRepository(session).binding(job_id)
        if old is None:
            snapshot = {'schema_version': 'research-learning-source.v1', 'source_kind': source_kind, 'source': source.model_dump(mode='json'), 'source_available_at': available.isoformat(), 'originals': bodies, 'financial_qualified': False}
            digest, locator = self.archive.put(encoded(snapshot).encode())
            binding = seal(LearningBinding, dict(schema_version='research-learning-binding.v1', id=identity, job_id=job_id, trigger_key=trigger, protocol=reference(protocol), source_kind=source_kind, source=source, source_snapshot=OriginalRef(ref=locator, hash=digest), source_available_at=available, frozen_at=now, scope_id=protocol.scope_id, horizon=protocol.horizon, parent_manifest=protocol.parent_manifest, financial_qualified=False))
            with self.database.session() as session:
                begin_immediate(session)
                repository = LearningRepository(session)
                old = repository.binding(job_id)
                if old is None:
                    prepared_source.verify(session)
                    if repository.protocol(protocol_id) != protocol:
                        raise ValueError('learning_source_protocol_changed')
                    repository.append_binding(binding)
                    prepared_source.verify_after_writes(session)
        return self.ensure_enqueued(job_id)

    def _snapshot(self, binding):
        raw = self.archive.read(binding.source_snapshot.ref)
        if hashlib.sha256(raw).hexdigest() != binding.source_snapshot.hash:
            raise ValueError('learning_source_archive_hash_mismatch')
        body = json.loads(raw)
        if encoded(body).encode() != raw or body.get('schema_version') != 'research-learning-source.v1' or body.get('source_kind') != binding.source_kind or (body.get('source') != binding.source.model_dump(mode='json')) or (body.get('source_available_at') != binding.source_available_at.isoformat()) or (body.get('financial_qualified') is not False):
            raise ValueError('learning_source_snapshot_mismatch')
        key = 'outcome' if binding.source_kind == 'action_outcome' else 'observation'
        source = body['originals'][key]
        identity = LearningSourceIdentity.model_validate(body['originals']['source_identity'])
        if (source['id'], source['payload_hash'], identity.scope_id, identity.horizon) != (binding.source.ref, binding.source.hash, binding.scope_id, binding.horizon):
            raise ValueError('learning_source_projection_mismatch')
        if identity.source != binding.source:
            raise ValueError('learning_source_projection_mismatch')
        return body

    def ensure_enqueued(self, job_id):
        with self.database.session() as session:
            repository = LearningRepository(session)
            binding = repository.binding(job_id)
            if binding is None:
                raise ValueError('learning_job_binding_missing')
            protocol = repository.protocol(binding.protocol.ref)
        self._snapshot(binding)
        return self.jobs.enqueue(EvolutionJobCreate(trigger_key=binding.trigger_key, trigger_type='scheduled', domain_pack_ref='crypto_macro.v1', input_refs=['research-learning-binding:' + binding.id + ':' + binding.payload_hash], max_attempts=protocol.max_attempts))

    def repair_missing_queue_entries(self, *, limit=10):
        if not 1 <= limit <= 100:
            raise ValueError('learning_scan_budget_invalid')
        with self.database.session() as session:
            ids = session.scalars(select(LearningBindingRecord.job_id).outerjoin(EvolutionJobRecord, EvolutionJobRecord.job_id == LearningBindingRecord.job_id).where(EvolutionJobRecord.job_id.is_(None)).order_by(LearningBindingRecord.frozen_at, LearningBindingRecord.job_id).limit(limit)).all()
        return [self.ensure_enqueued(job_id) for job_id in ids]

    def read_claimed(self, job, worker_id):
        with self.database.session() as session:
            repository = LearningRepository(session)
            binding = repository.binding(job.job_id)
            if binding is None:
                if job.trigger_key.startswith('research-recipe.v1:') or any((ref.startswith('research-learning-binding:') for ref in job.input_refs)):
                    raise ValueError('learning_job_binding_missing')
                return None
            binding = repository.owned(job.job_id, worker_id, job.attempt, now=self.clock())
            protocol = repository.protocol(binding.protocol.ref)
        snapshot = self._snapshot(binding)
        _, current, available = self._read_source(binding.source_kind, binding.source.ref)
        if encoded(current) != encoded(snapshot['originals']) or available < binding.source_available_at:
            raise ValueError('learning_source_changed_after_freeze')
        return (binding, protocol, snapshot)

    def resume_after_saved_response(self, job_id):
        """Only a real saved reflection stage can resolve the unconfirmed-call wait."""
        now = aware(self.clock())
        with self.database.session() as session:
            begin_immediate(session)
            repository = LearningRepository(session)
            if repository.binding(job_id) is None or repository.stage(job_id, 'reflection') is None:
                raise ValueError('learning_saved_response_required')
            row = session.get(EvolutionJobRecord, job_id, populate_existing=True)
            if row is None or row.candidate_id is not None:
                raise ValueError('learning_job_binding_mismatch')
            if row.status != 'waiting_inputs' or row.last_error_code != 'learning_provider_completion_unconfirmed':
                return False
            result = session.execute(update(EvolutionJobRecord).where(EvolutionJobRecord.job_id == job_id, EvolutionJobRecord.status == 'waiting_inputs', EvolutionJobRecord.candidate_id.is_(None), EvolutionJobRecord.last_error_code == 'learning_provider_completion_unconfirmed').values(status='queued', next_attempt_at=now, updated_at=now, last_error_code=None))
            return result.rowcount == 1
