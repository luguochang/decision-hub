"""Identity-only trial selection over the actual W5 compiler and prepared Run.

Artifact imports are temporary integration seams, never a second model runtime.
"""
# Scoped trial-original preparation reuse; application-private, never a permission cache.
import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from packages.kernel.decision_hub_kernel.decision.research_method_trial import (
    require_future_trial_selection,
)
from packages.kernel.decision_hub_kernel.persistence.research_method_trial import (
    TrialPointer,
    TrialRepository,
)
from packages.kernel.decision_hub_kernel.persistence.research_method_trial_selection import (
    census,
    reserve,
)
from packages.kernel.decision_hub_kernel.ports.research_learning import BehaviorOriginalRef as Ref

from packages.kernel.decision_hub_kernel.application.research_skill_package import decoded, encoded
from packages.kernel.decision_hub_kernel.application.research_skill_selection import (
    SkillSelectionService,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

_TRIAL_READ_SCOPE = ContextVar("trial_admission_original_read", default=None)


def _read_owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


class _TrialReadScope:
    def __init__(self, session, boundary):
        self.session, self.boundary = session, boundary
        self.owner = _read_owner()
        self.prepared = {}

    def require_unchanged(self):
        if (
            self.owner != _read_owner()
            or not self.boundary.unchanged()
            or self.boundary.connection.connection.driver_connection is not self.boundary.driver
        ):
            raise ValueError("trial_original_read_scope_changed")

    def read(self, session, reader, receipt_id):
        self.require_unchanged()
        key = (reader, receipt_id)
        original = self.prepared.get(key)
        if original is None:
            if len(self.prepared) >= 8:
                raise ValueError("trial_original_read_scope_limit")
            original = reader.prepare_receipt(receipt_id)
            original.verify(session)
            self.require_unchanged()
            self.prepared[key] = original
        return original

    def verify(self):
        self.require_unchanged()
        for original in self.prepared.values():
            original.verify(self.session)
            self.require_unchanged()


@contextmanager
def trial_original_read_scope(session):
    """Reuse a preparation only within one clean physical RO transaction.

    First use and successful exit check complete SQL/source/raw originals.
    Every use still checks this physical boundary and its business relations.
    Writer preparations carry explicit originals; this ContextVar never grants
    cross-snapshot reuse or a cached permission decision.
    """
    from .research_graph_binding import _local_original_read
    with _local_original_read(session) as boundary:
        scope = _TrialReadScope(session, boundary)
        scope.require_unchanged()
        token = _TRIAL_READ_SCOPE.set(scope)
        try:
            with session.no_autoflush:
                yield scope
                scope.verify()
        finally:
            scope.prepared.clear()
            _TRIAL_READ_SCOPE.reset(token)


def _prepared_trial_in_scope(session, reader, receipt_id):
    scope = _TRIAL_READ_SCOPE.get()
    if scope is None or scope.session is not session or scope.owner != _read_owner():
        original = reader.prepare_receipt(receipt_id)
        original.verify(session)
        return original
    return scope.read(session, reader, receipt_id)


@dataclass(frozen=True)
class TrialRunScope:
    scope_id: str
    horizon: str
    target: Ref

def read_trial_run_scope(session, execution):
    from packages.kernel.decision_hub_kernel.application.action_synthesis_preflight import (
        frozen_prompt_catalog,
    )
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
        read_prepared_execution_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.research_run_scope import (
        read_run_spot_admission_in_session,
    )
    from packages.kernel.decision_hub_kernel.decision.action_research_policy import (
        research_action_catalog,
    )
    prepared = read_prepared_execution_in_session(session, execution.run_id, execution.generation)
    admission = read_run_spot_admission_in_session(session, execution.run_id)
    if admission is None or prepared.execution.task_request_hash != execution.task_request_hash:
        raise ValueError('trial_run_admission_missing')
    catalog = frozen_prompt_catalog(prepared.prompt.prompt, research_action_catalog(admission.instrument))
    if catalog['scope_id'] != admission.scope.scope_id:
        raise ValueError('trial_run_catalog_scope_mismatch')
    target = catalog['targets']['24h']
    return TrialRunScope(admission.scope.scope_id, '24h', Ref(ref=target['ref'], hash=target['hash']))

def pointer_for(session, scope):
    key = canonical_hash([scope.scope_id, scope.horizon, scope.target.model_dump(mode='json')])
    row = session.get(TrialPointer, key, populate_existing=True)
    if row is None:
        return None
    repository = TrialRepository(session)
    receipt = repository.read('receipt', row.receipt_id)
    if receipt is None or receipt.payload_hash != row.receipt_hash:
        raise ValueError('trial_pointer_original_missing')
    protocol = repository.read('protocol', receipt.protocol.ref)
    if protocol is None or (protocol.scope_id, protocol.horizon, protocol.target) != (scope.scope_id, scope.horizon, scope.target):
        raise ValueError('trial_pointer_scope_mismatch')
    return (row.version, row.receipt_id, row.receipt_hash)

@dataclass(frozen=True)
class TrialGrant:
    admission: object
    scope: TrialRunScope
    pointer: tuple
    selected_runs: tuple[str, ...]

@dataclass(frozen=True)
class TrialSelectionPreparation:
    originals: object
    grant: TrialGrant

    @property
    def value(self):
        return self.originals.value

    def verify(self, session):
        self.originals.verify(session)
        self.grant.admission.verify(session)

    def verify_after_writes(self, session):
        self.originals.verify_after_writes(session)
        self.grant.admission.verify_after_writes(session)

class TrialSkillSelectionService(SkillSelectionService):

    def __init__(self, *args, admission, scope_reader=read_trial_run_scope, **kwargs):
        super().__init__(*args, **kwargs)
        if admission.database is not self.packages.registry.database:
            raise ValueError('trial_selection_database_mismatch')
        self.admission, self.scope_reader = (admission, scope_reader)

    def _check_selection(self, body):
        if body.get('schema_version') in {
            'research-skill-selection.v1', 'research-skill-selection.v3'
        }:
            return super()._check_selection(body)
        self.packages.contracts.check("selection_v2", body)
        encoded(body)

    def prepare_current(self, run_id):
        """No candidate/receipt/scope/allow boolean is supplied by the caller."""

        def locate(session):
            old = self._row(session, run_id)
            if old is not None:
                body = self.read_in_session(session, run_id)
                return ('existing', body)
            execution = self.execution_reader(session, run_id, None)
            if execution.generation != 1 or execution.turn_kind != 'research_round':
                return ('repair_unselected_legacy', None)
            scope = self.scope_reader(session, execution)
            pointer = pointer_for(session, scope)
            return ('trial_pointer', (scope, pointer)) if pointer else ('no_trial_pointer', None)
        located = self.originals_preparer(self.packages.registry.database, locate)
        status, value = located.value
        if status == 'existing':
            return (status, self.originals_preparer(self.packages.registry.database, lambda s: self._prepare(s, run_id, value['skill_version_id'])))
        if status != 'trial_pointer':
            return (status, None)
        scope, pointer = value
        admission = self.admission.prepare_receipt(pointer[1])
        protocol, receipt = (admission.protocol, admission.receipt)
        if receipt is None or receipt.payload_hash != pointer[2]:
            raise ValueError('trial_pointer_receipt_changed')
        if protocol.horizon != '24h':
            return ('trial_horizon_unsupported', None)

        def prepare(session):
            located.verify(session)
            admission.verify(session)
            if pointer_for(session, scope) != pointer:
                raise ValueError('trial_pointer_cas_lost')
            execution = self.execution_reader(session, run_id, None)
            actual_scope = self.scope_reader(session, execution)
            if actual_scope != scope:
                raise ValueError('trial_run_scope_changed')
            runs = census(session, receipt.id, receipt.payload_hash)
            if execution.cutoff < protocol.start_at:
                return ('trial_not_started', None)
            if execution.cutoff >= protocol.end_at or aware_utc(self.clock()) >= protocol.end_at:
                return ('trial_expired', None)
            if len(runs) >= protocol.max_runs:
                return ('trial_quota_exhausted', None)
            require_future_trial_selection(protocol=protocol, receipt=receipt, run_id=run_id, generation=execution.generation, turn_kind=execution.turn_kind, cutoff=execution.cutoff, scope_id=scope.scope_id, horizon=scope.horizon, target=scope.target, actual_selected_runs=runs, now=aware_utc(self.clock()))
            grant = TrialGrant(admission, scope, pointer, runs)
            material = self._prepare(session, run_id, receipt.candidate.ref, authorization=grant)
            return ('prepared', (material, grant))
        result = self.originals_preparer(self.packages.registry.database, prepare)
        status, payload = result.value
        if status != 'prepared':
            return (status, None)
        material, grant = payload
        from dataclasses import replace
        return (status, TrialSelectionPreparation(replace(result, value=material), grant))

    def _authorize_package(self, session, execution, package, authorization):
        if authorization is None:
            return super()._authorize_package(session, execution, package, None)
        if not isinstance(authorization, TrialGrant):
            raise ValueError('trial_service_preparation_required')
        grant = authorization
        grant.admission.verify(session)
        protocol, receipt = (grant.admission.protocol, grant.admission.receipt)
        if package.manifest != grant.admission.material.candidate.manifest or package.manifest.version_id != receipt.candidate.ref or package.manifest.payload_hash != receipt.candidate.hash or (package.manifest.parent_version_id != receipt.parent.ref) or (package.version['horizon'] != protocol.horizon):
            raise ValueError('trial_selection_candidate_mismatch')
        require_future_trial_selection(protocol=protocol, receipt=receipt, run_id=execution.run_id, generation=execution.generation, turn_kind=execution.turn_kind, cutoff=execution.cutoff, scope_id=grant.scope.scope_id, horizon=grant.scope.horizon, target=grant.scope.target, actual_selected_runs=grant.selected_runs, now=aware_utc(self.clock()))

    def _selection_extension(self, execution, package, authorization):
        if authorization is None:
            return super()._selection_extension(execution, package, None)
        grant, receipt = (authorization, authorization.admission.receipt)
        return dict(schema_version='research-skill-selection.v2', selection_origin='trial_registered', trial_receipt={'ref': receipt.id, 'hash': receipt.payload_hash}, trial_protocol=receipt.protocol.model_dump(mode='json'), trial_parent=receipt.parent.model_dump(mode='json'), trial_target=grant.scope.target.model_dump(mode='json'), trial_pointer_version=grant.pointer[0], trial_run_ordinal=len(grant.selected_runs) + 1, financial_qualified=False)

    def _authorize_saved(self, session, body, execution, package):
        if body['selection_origin'] == 'baseline_registered':
            return super()._authorize_saved(session, body, execution, package)
        prepared = _prepared_trial_in_scope(session, self.admission, body['trial_receipt']['ref'])
        protocol, receipt = (prepared.protocol, prepared.receipt)
        scope = self.scope_reader(session, execution)
        if receipt is None or receipt.payload_hash != body['trial_receipt']['hash'] or receipt.protocol.model_dump(mode='json') != body['trial_protocol'] or (receipt.parent.model_dump(mode='json') != body['trial_parent']) or (scope.target.model_dump(mode='json') != body['trial_target']) or ((scope.scope_id, scope.horizon, scope.target) != (protocol.scope_id, protocol.horizon, protocol.target)) or (package.manifest != prepared.material.candidate.manifest) or (package.manifest.payload_hash != receipt.candidate.hash) or (package.manifest.parent_version_id != receipt.parent.ref) or (not receipt.admitted_at <= protocol.start_at <= execution.cutoff < protocol.end_at) or (not execution.cutoff <= datetime.fromisoformat(body['selected_at']) < protocol.end_at):
            raise ValueError('trial_saved_selection_authority_mismatch')
        runs = census(session, receipt.id, receipt.payload_hash)
        ordinal = body['trial_run_ordinal']
        if not 1 <= ordinal <= len(runs) <= protocol.max_runs or runs[ordinal - 1] != body['run_id']:
            raise ValueError('trial_saved_selection_quota_mismatch')

    def freeze_in_session(self, session, prepared):
        if not isinstance(prepared, TrialSelectionPreparation):
            return super().freeze_in_session(session, prepared)
        prepared.verify(session)
        grant = prepared.grant
        protocol, receipt = (grant.admission.protocol, grant.admission.receipt)
        body = decoded(prepared.value.intent_json)
        if self._row(session, body['run_id']) is not None:
            raise ValueError('trial_selection_reprepare_required')
        if pointer_for(session, grant.scope) != grant.pointer:
            raise ValueError('trial_pointer_cas_lost')
        if census(session, receipt.id, receipt.payload_hash) != grant.selected_runs:
            raise ValueError('trial_quota_cas_lost')
        now = aware_utc(self.clock())
        if not protocol.start_at <= now < protocol.end_at:
            raise ValueError('trial_writer_window_elapsed')
        result = super().freeze_in_session(session, prepared)
        prepared.verify_after_writes(session)
        reserve(session, result, grant.selected_runs)
        return result
