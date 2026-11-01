"""Identity-only enrollment of an existing paper portfolio in finite method learning."""
from dataclasses import dataclass
from datetime import datetime
import hashlib

from ..persistence.action_execution_repository import ExecutionRepository
from ..persistence.db import as_utc
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.research_learning_cursor import LearningScanCursor
from ..persistence.research_learning_records import LearningRegistrationRecord
from ..persistence.research_learning_repository import LearningRepository, check, encoded, reference, seal
from ..ports.market_path import canonical_hash
from ..ports.research_learning import LearningProtocol, LearningRegistration, OriginalRef, aware


POLICY = {
    "schema_version": "research-learning-enrollment-policy.v1",
    "id": "spot-terminal-method-learning.v1",
    "scope_ids": ["crypto:spot:okx:BTC-USDT:trade:1m", "crypto:spot:okx:ETH-USDT:trade:1m"],
    "horizon": "24h",
    "parent_basis": "registered_template",
    "allowed_resource_paths": ["SKILL.md", "workflow.json"],
    "allowed_capabilities": ["spot_market_facts"],
    "max_attempts": 3,
    "historical_terminal_intake": True,
    "first_terminal_per_portfolio_opportunity_method": True,
    "unknown_is_profit_label": False,
    "financial_qualified": False,
}
POLICY_HASH = canonical_hash(POLICY)


@dataclass(frozen=True)
class _EnrollmentInputs:
    identity: object
    evaluation: object
    target: OriginalRef
    parent: object
    operations: tuple[str, ...]
    byte_refs: tuple[tuple[str, str], ...]


def read_registration_in_session(session, registration_id):
    row = session.get(LearningRegistrationRecord, registration_id, populate_existing=True)
    if row is None:
        return None
    value = check(LearningRegistration, row.payload_json, row.payload_hash)
    protocol = LearningRepository(session).protocol(value.protocol.ref)
    if (row.registration_id, row.protocol_id, row.portfolio_id) != (
        value.id, value.protocol.ref, value.portfolio.ref
    ) or value.protocol != reference(protocol) or value.registered_at != protocol.registered_at or as_utc(row.registered_at) != value.registered_at:
        raise ValueError("learning_registration_projection_mismatch")
    if value.policy != OriginalRef(ref=POLICY["id"], hash=POLICY_HASH):
        raise ValueError("learning_registration_policy_unavailable")
    return value


class LearningRegistrationService:
    def __init__(self, database, installed, *, clock):
        if installed.database is not database or installed.packages.registry.database is not database:
            raise ValueError("learning_registration_database_mismatch")
        self.database, self.installed, self.clock = database, installed, clock

    def _inputs(self, session, portfolio_id, now):
        repository = ExecutionRepository(session)
        identity = repository.account(portfolio_id).portfolio_identity
        evaluation = repository.protocol(identity.protocol_ref)
        if identity.scope_id not in POLICY["scope_ids"] or identity.horizon != POLICY["horizon"]:
            raise ValueError("learning_registration_scope_unsupported")
        targets = [value for value in evaluation.targets if value.horizon == identity.horizon]
        if evaluation.payload_hash != identity.protocol_hash or len(targets) != 1 or targets[0].target_spec_hash != identity.target_spec_hash or not any(
            value.ref == identity.method_ref and value.hash == identity.method_hash for value in evaluation.methods
        ) or evaluation.registered_at > now:
            raise ValueError("learning_registration_evaluation_mismatch")
        current = self.installed.versions.get(identity.scope_id)
        context = self.installed.contexts.get(identity.scope_id)
        if current is None or context is None:
            raise ValueError("learning_registration_parent_unavailable")
        parent = self.installed.packages.read_in_session(session, current[0])
        version = parent.version
        known = aware(datetime.fromisoformat(version["first_known_at"]))
        if parent.manifest.scope_id != identity.scope_id or version["horizon"] != identity.horizon or known > now or self.installed.baselines.get(current[0]) != parent.manifest.payload_hash or context.compiler_hash != self.installed.packages.installed.code_hash:
            raise ValueError("learning_registration_parent_mismatch")
        if not set(POLICY["allowed_resource_paths"]).issubset({item.path for item in parent.resources}) or not set(POLICY["allowed_capabilities"]).issubset(context.allowed_capabilities):
            raise ValueError("learning_registration_parent_capability_mismatch")
        refs = [(item["body_ref"], item["body_hash"]) for item in version["files"]]
        refs += [(item["controlled_routes_ref"], item["controlled_routes_hash"]) for item in version["capability_surfaces"]]
        return _EnrollmentInputs(identity, evaluation,
            OriginalRef(ref=targets[0].target_spec_ref, hash=targets[0].target_spec_hash), parent,
            tuple(sorted({item.op for item in context.operations})), tuple(refs))

    def _verify_bytes(self, inputs):
        self.installed.packages.installed.verify()
        for locator, digest in inputs.byte_refs:
            if hashlib.sha256(self.installed.packages.archive.read(locator)).hexdigest() != digest:
                raise ValueError("learning_registration_parent_bytes_changed")

    def register_for_portfolio(self, portfolio_id: str, policy_id: str):
        if policy_id != POLICY["id"]:
            raise ValueError("learning_registration_policy_unavailable")
        now = aware(self.clock())
        prepared = prepare_originals(self.database, lambda session: self._inputs(session, portfolio_id, now))
        inputs = prepared.value
        identity, parent = inputs.identity, inputs.parent.manifest
        digest = canonical_hash([POLICY_HASH, identity.identity_hash, parent.version_id, parent.payload_hash])
        protocol_id, registration_id = "learnprotocol_" + digest, "learnregistration_" + digest
        protocol = seal(LearningProtocol, dict(
            schema_version="research-learning-protocol.v1", id=protocol_id, registered_at=now,
            scope_id=identity.scope_id, horizon=identity.horizon, target=inputs.target,
            parent_manifest=OriginalRef(ref=parent.version_id, hash=parent.payload_hash),
            evaluation_protocol=OriginalRef(ref=inputs.evaluation.id, hash=inputs.evaluation.payload_hash),
            source_method=OriginalRef(ref=identity.method_ref, hash=identity.method_hash),
            parent_basis=POLICY["parent_basis"], allowed_resource_paths=POLICY["allowed_resource_paths"],
            allowed_operations=list(inputs.operations), allowed_capabilities=POLICY["allowed_capabilities"],
            max_attempts=POLICY["max_attempts"], financial_qualified=False,
        ))
        registration = seal(LearningRegistration, dict(
            schema_version="research-learning-registration.v1", id=registration_id,
            protocol=reference(protocol), portfolio=OriginalRef(ref=identity.id, hash=identity.identity_hash),
            policy=OriginalRef(ref=policy_id, hash=POLICY_HASH), registered_at=now,
        ))
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            self._verify_bytes(inputs)
            old = read_registration_in_session(session, registration_id)
            if old is not None:
                return old
            LearningRepository(session).register_protocol(protocol, now=now)
            session.add(LearningRegistrationRecord(
                registration_id=registration.id, protocol_id=protocol.id, portfolio_id=identity.id,
                registered_at=now, payload_json=encoded(registration), payload_hash=registration.payload_hash,
            ))
            session.flush()
            prepared.verify_after_writes(session)
            self._verify_bytes(inputs)
            return read_registration_in_session(session, registration_id)

    def take_scan_batch(self, *, scanner_id: str, limit: int = 2):
        # Advance before slow source work so a failure cannot starve subsequent accounts.
        # A crash skips at most one rotation; immutable first-terminal unit identity prevents duplicates.
        with self.database.session() as session:
            begin_immediate(session)
            cursor = LearningScanCursor(session)
            batch = cursor.prepare(scanner_id, limit=limit)
            cursor.advance(batch, now=self.clock())
            return batch.registration_ids

    def read(self, registration_id):
        with self.database.session() as session:
            value = read_registration_in_session(session, registration_id)
            if value is None:
                raise ValueError("learning_registration_missing")
            return value

    def reconcile_portfolios(self, *, limit: int = 2):
        """Identity-only normal enrollment, bounded and fair across failed accounts."""
        with self.database.session() as session:
            begin_immediate(session)
            cursor = LearningScanCursor(session)
            batch = cursor.prepare('normal-learning-enrollment', limit=limit, source_kind='portfolio')
            cursor.advance(batch, now=self.clock())
        errors = []
        for identity in batch.registration_ids:
            try:
                self.register_for_portfolio(identity, POLICY['id'])
            except ValueError as error:
                errors.append({'portfolio_id': identity, 'error': str(error)})
        return tuple(errors)
