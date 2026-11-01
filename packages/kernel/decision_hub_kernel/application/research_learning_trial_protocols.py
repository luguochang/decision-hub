"""Register finite future trial/behavior protocols from pre-reflection originals."""
from dataclasses import dataclass
from datetime import timedelta

from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.research_learning_repository import LearningRepository
from ..persistence.research_method_trial import TrialRepository
from ..persistence.research_skill_behavior import BehaviorOriginal
from ..ports.market_path import canonical_hash
from ..ports.research_learning import BehaviorProtocol, MethodTrialProtocol
from .research_learning_suite import read_context_suite
from .research_skill_package import encoded, sha


@dataclass(frozen=True)
class TrialProtocols:
    behavior_id: str
    trial_id: str


def _stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def _seal(cls, body):
    return cls.model_validate(body | {'payload_hash': canonical_hash(body)})


def _reference(value):
    return {'ref': value.id, 'hash': value.payload_hash}


def frozen_case_originals(suite, archive):
    """Always all cases, in their frozen order; no post-response case selection."""
    values = []
    for case in suite.cases:
        originals = []
        for body in (case.input_body, case.expected_schema):
            digest, locator = archive.put(encoded(body))
            originals.append({'ref': locator, 'hash': digest})
        values.append(dict(id=case.id, purpose=case.purpose, inputs=originals[0],
                           expected=originals[1], material_kind=case.material_kind,
                           known_at=_stamp(case.known_at)))
    return values


def require_frozen_cases(protocol, context, archive):
    """Independent read, not a claim that the factory once verified these cases."""
    suite = read_context_suite(context)
    if len(protocol.cases) != len(suite.cases) or suite.registered_at > protocol.registered_at:
        raise ValueError('learning_behavior_suite_protocol_mismatch')
    for declared, case in zip(protocol.cases, suite.cases, strict=True):
        if (declared.id, declared.purpose, declared.material_kind, declared.known_at) != (case.id, case.purpose, case.material_kind, case.known_at):
            raise ValueError('learning_behavior_case_projection_changed')
        for reference, expected in ((declared.inputs, case.input_body), (declared.expected, case.expected_schema)):
            raw = archive.read(reference.ref)
            if sha(raw) != reference.hash or raw != encoded(expected):
                raise ValueError('learning_behavior_case_original_changed')


class LearningTrialProtocolFactory:
    def __init__(self, database, materials, sources, behavior_store, *, recipe_hash, call_timeout_seconds, clock):
        if materials.database is not database or sources.database is not database or behavior_store.database is not database:
            raise ValueError('learning_trial_protocol_database_mismatch')
        if type(call_timeout_seconds) is not int or call_timeout_seconds <= 0:
            raise ValueError('learning_behavior_call_timeout_required')
        self.database, self.materials, self.sources, self.behavior_store = database, materials, sources, behavior_store
        self.recipe_hash, self.call_timeout_seconds, self.clock = recipe_hash, call_timeout_seconds, clock

    def prepare_for_claimed(self, *, job_id, package_receipt_id, worker_id, attempt):
        with self.database.session() as session:
            binding = LearningRepository(session).owned(job_id, worker_id, attempt, now=self.clock())
        source = self.sources.prepare(binding.source_kind, binding.source.ref)

        def read(session):
            material = self.materials.read_in_session(session, job_id, package_receipt_id)
            if (material.binding != binding or source.identity.source != binding.source
                    or source.available_at < binding.source_available_at
                    or encoded(source.bodies) != encoded(material.snapshot['originals'])):
                raise ValueError('learning_trial_source_changed')
            suite = read_context_suite(material.context)
            key = canonical_hash([material.stage.payload_hash, suite.payload_hash, self.recipe_hash])
            behavior_id, trial_id = 'behavior_protocol_' + key, 'method_protocol_' + key
            return (material, suite, behavior_id, trial_id,
                    self.behavior_store.read_in_session(session, 'protocol', behavior_id),
                    TrialRepository(session).read('protocol', trial_id))

        prepared = prepare_originals(self.database, read)
        material, suite, behavior_id, trial_id, old_behavior, old_trial = prepared.value
        if (old_behavior is None) != (old_trial is None):
            raise ValueError('learning_trial_protocol_pair_incomplete')
        cases = frozen_case_originals(suite, self.materials.archive)
        with self.database.session() as session:
            begin_immediate(session)
            LearningRepository(session).owned(job_id, worker_id, attempt, now=self.clock())
            source.verify(session); prepared.verify(session); material.verify_bytes()
            now = old_behavior.registered_at if old_behavior else self.clock()
            common = dict(job_id=job_id, package_stage=_reference(material.stage),
                          candidate={'ref': material.candidate.manifest.version_id, 'hash': material.candidate.manifest.payload_hash},
                          parent={'ref': material.parent.manifest.version_id, 'hash': material.parent.manifest.payload_hash})
            behavior = _seal(BehaviorProtocol, dict(schema_version='skill-behavior-protocol.v1',
                id=behavior_id, **common, recipe_hash=self.recipe_hash, registered_at=_stamp(now), cases=cases))
            # Twelve provider calls (six cases, both methods), each under the existing
            # configured hard timeout. Admission must finish before this future window.
            start = now + timedelta(seconds=2 * len(cases) * self.call_timeout_seconds + 60)
            trial = _seal(MethodTrialProtocol, dict(schema_version='research-method-trial-protocol.v1',
                id=trial_id, **common,
                behavior_protocol={'ref': behavior.id, 'hash': canonical_hash(behavior.model_dump(mode='json'))},
                scope_id=material.learning_protocol.scope_id, horizon=material.learning_protocol.horizon,
                target=material.learning_protocol.target.model_dump(mode='json'), registered_at=_stamp(now),
                start_at=_stamp(start), end_at=_stamp(start + timedelta(hours=24)), max_runs=4,
                financial_qualified=False))
            require_frozen_cases(behavior, material.context, self.materials.archive)
            if old_behavior is not None:
                if (behavior, trial) != (old_behavior, old_trial):
                    raise ValueError('learning_trial_protocol_saved_original_changed')
            else:
                wire = encoded(behavior.model_dump(mode='json'))
                session.add(BehaviorOriginal(kind='protocol', identity=behavior.id, protocol_id=behavior.id,
                    payload_json=wire.decode(), payload_hash=sha(wire)))
                session.flush()
                TrialRepository(session).append('protocol', trial)
            source.verify_after_writes(session); prepared.verify_after_writes(session); material.verify_bytes()
            if self.behavior_store.read_in_session(session, 'protocol', behavior_id) != behavior:
                raise ValueError('learning_trial_protocol_readback_failed')
            return TrialProtocols(behavior_id, trial_id)
