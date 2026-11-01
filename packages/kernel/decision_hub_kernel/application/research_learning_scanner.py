"""Exact first-terminal intake; this artifact does not register production tables."""
from datetime import datetime
from typing import Literal
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import ExecutionReceiptRecord
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import ExecutionRepository
from packages.kernel.decision_hub_kernel.ports.research_learning import FrozenModel, Hash, Identity, OriginalRef, aware
from packages.kernel.decision_hub_kernel.persistence.research_learning_records import LearningBase
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import LearningRepository, binding_identity, check, encoded, seal
from pydantic import AwareDatetime
from sqlalchemy import DDL, DateTime, String, Text, UniqueConstraint, event, exists, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column
from packages.kernel.decision_hub_kernel.persistence.action_records import ActionOutcomeRecord
from packages.kernel.decision_hub_kernel.persistence.db import EvolutionJobRecord
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_learning import LearningUnit
from packages.kernel.decision_hub_kernel.persistence.research_learning_records import LearningUnitRecord

def read_unit(session, identity):
    row = session.get(LearningUnitRecord, identity, populate_existing=True)
    if row is None:
        return None
    value = check(LearningUnit, row.payload_json, row.payload_hash)
    if (row.unit_id, row.protocol_id, row.portfolio_hash, row.opportunity_id, row.method_hash, row.job_id) != (value.id, value.protocol.ref, value.portfolio.hash, value.opportunity_id, value.method.hash, value.job_id):
        raise ValueError('learning_unit_projection_mismatch')
    return value

class TerminalOutcomeScanner:

    def __init__(self, intake):
        self.intake, self.database = (intake, intake.database)

    def _context(self, session, protocol_id, portfolio_id):
        protocol = LearningRepository(session).protocol(protocol_id)
        execution = ExecutionRepository(session)
        identity = execution.account(portfolio_id).portfolio_identity
        evaluation = execution.protocol(identity.protocol_ref)
        if protocol.scope_id != identity.scope_id or protocol.horizon != identity.horizon or protocol.source_method != OriginalRef(ref=identity.method_ref, hash=identity.method_hash) or (protocol.target.hash != identity.target_spec_hash) or (protocol.evaluation_protocol != OriginalRef(ref=evaluation.id, hash=evaluation.payload_hash)) or (evaluation.payload_hash != identity.protocol_hash):
            raise ValueError('learning_scanner_portfolio_protocol_mismatch')
        return (protocol, identity)

    def _enqueue(self, unit):
        job = self.intake.enqueue_source(protocol_id=unit.protocol.ref, source_kind='action_outcome', source_id=unit.source.ref)
        if job.job_id != unit.job_id:
            raise ValueError('learning_unit_job_mismatch')
        return job

    def scan(self, *, protocol_id, portfolio_id, limit=4):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('learning_scanner_budget_invalid')
        now = aware(self.intake.clock())
        with self.database.session() as session:
            protocol, portfolio = self._context(session, protocol_id, portfolio_id)
            pending = session.scalars(select(LearningUnitRecord.unit_id).where(LearningUnitRecord.protocol_id == protocol.id, LearningUnitRecord.portfolio_hash == portfolio.identity_hash, ~exists(select(EvolutionJobRecord.job_id).where(EvolutionJobRecord.job_id == LearningUnitRecord.job_id))).order_by(LearningUnitRecord.recorded_at, LearningUnitRecord.unit_id).limit(limit)).all()
            saved = [read_unit(session, item) for item in pending]
        jobs = [self._enqueue(item) for item in saved]
        if len(jobs) == limit:
            return jobs
        with self.database.session() as session:
            candidates = session.execute(select(ActionOutcomeRecord.outcome_id, ActionOutcomeRecord.payload_hash).join(ExecutionReceiptRecord, ActionOutcomeRecord.outcome_id == func.json_extract(ExecutionReceiptRecord.payload_json, '$.outcome_ref')).where(ExecutionReceiptRecord.portfolio_id == portfolio.id, ExecutionReceiptRecord.decided_at <= now, ActionOutcomeRecord.protocol_hash == portfolio.identity_hash, or_(ExecutionReceiptRecord.status != 'filled', ActionOutcomeRecord.status == 'settled'), ~exists(select(LearningUnitRecord.unit_id).where(LearningUnitRecord.protocol_id == protocol.id, LearningUnitRecord.portfolio_hash == portfolio.identity_hash, LearningUnitRecord.opportunity_id == ActionOutcomeRecord.opportunity_id, LearningUnitRecord.method_hash == portfolio.method_hash))).order_by(ExecutionReceiptRecord.decided_at, ExecutionReceiptRecord.receipt_id).limit(limit - len(jobs))).all()
        for source_id, expected_hash in candidates:
            prepared_source = self.intake.source_preparer.prepare('action_outcome', source_id)
            identity, originals, available = prepared_source.identity, prepared_source.bodies, prepared_source.available_at
            checked_at = aware(self.intake.clock())
            outcome, receipt = (originals['outcome'], originals['execution_receipt'])
            mature = datetime.fromisoformat(receipt['decided_at'].replace('Z', '+00:00'))
            if identity.portfolio != OriginalRef(ref=portfolio.id, hash=portfolio.identity_hash) or identity.source.hash != expected_hash or available > checked_at or (mature > available) or (identity.method != protocol.source_method) or (identity.target != protocol.target) or (identity.evaluation_protocol != protocol.evaluation_protocol) or (outcome['execution_status'] != receipt['status']):
                raise ValueError('learning_terminal_source_identity_mismatch')
            if receipt['status'] == 'filled':
                if outcome['status'] != 'settled' or outcome['settled_at'] is None or outcome['remaining_quantity'] != '0':
                    raise ValueError('learning_filled_episode_not_mature')
            elif receipt['status'] not in {'no_fill', 'rejected', 'unknown'} or outcome['status'] != 'unscorable':
                raise ValueError('learning_terminal_status_invalid')
            unit_id = 'learnunit_' + canonical_hash([protocol.payload_hash, identity.portfolio.hash, outcome['opportunity_id'], identity.method.ref, identity.method.hash])
            _, _, job_id = binding_identity(protocol, 'action_outcome', identity.source)
            unit = seal(LearningUnit, dict(schema_version='research-learning-unit.v1', id=unit_id, protocol=OriginalRef(ref=protocol.id, hash=protocol.payload_hash), portfolio=identity.portfolio, opportunity_id=outcome['opportunity_id'], method=identity.method, source=identity.source, execution_receipt=OriginalRef(ref=receipt['id'], hash=receipt['payload_hash']), execution_status=receipt['status'], outcome_status=outcome['status'], reason=receipt['reason'], matured_at=mature, first_read_at=available, recorded_at=checked_at, job_id=job_id))
            with self.database.session() as session:
                begin_immediate(session)
                old = read_unit(session, unit_id)
                if old is not None:
                    unit = old
                else:
                    prepared_source.verify(session)
                    current_protocol, current_portfolio = self._context(session, protocol_id, portfolio_id)
                    if current_protocol != protocol or current_portfolio != portfolio:
                        raise ValueError('learning_terminal_registration_changed')
                    row = session.get(ActionOutcomeRecord, source_id, populate_existing=True)
                    result = session.get(ExecutionReceiptRecord, receipt['id'], populate_existing=True)
                    if row is None or result is None or row.payload_hash != expected_hash or (result.payload_hash != receipt['payload_hash']):
                        raise ValueError('learning_terminal_original_changed')
                    session.add(LearningUnitRecord(unit_id=unit.id, protocol_id=protocol.id, portfolio_hash=unit.portfolio.hash, opportunity_id=unit.opportunity_id, method_hash=unit.method.hash, job_id=unit.job_id, recorded_at=checked_at, payload_json=encoded(unit), payload_hash=unit.payload_hash))
                    session.flush()
                    prepared_source.verify_after_writes(session)
            if unit.job_id not in {item.job_id for item in jobs}:
                jobs.append(self._enqueue(unit))
        return jobs

class LearningTriggerScanner:
    """Server configuration only; retain the existing scanner and policy."""

    def __init__(self, legacy, terminal, *, registrations, scanner_id='normal-method-learning'):
        self.legacy, self.terminal = (legacy, terminal)
        self.registrations, self.policy = (registrations, legacy.policy)
        self.scanner_id = scanner_id

    def scan(self):
        old = self.legacy.scan()
        jobs = []
        errors = list(self.registrations.reconcile_portfolios(limit=2))
        for identity in self.registrations.take_scan_batch(scanner_id=self.scanner_id, limit=2):
            try:
                registration = self.registrations.read(identity)
                jobs.extend(self.terminal.scan(protocol_id=registration.protocol.ref, portfolio_id=registration.portfolio.ref, limit=4))
            except ValueError as error:
                # Return a named per-account failure, preserve progress for other
                # registered accounts; database/programming failures still escape.
                errors.append({'registration_id': identity, 'error': str(error)})
        return {'legacy': old, 'learning_jobs': tuple((item.job_id for item in jobs)), 'learning_errors': tuple(errors)}
