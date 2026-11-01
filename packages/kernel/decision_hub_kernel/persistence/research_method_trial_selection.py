"""Private migration proposal: immutable distinct-Run trial reservations."""
from sqlalchemy import DDL, CheckConstraint, Integer, String, UniqueConstraint, event, select
from sqlalchemy.orm import Mapped, mapped_column
from packages.kernel.decision_hub_kernel.persistence.research_method_trial import TrialBase
from packages.kernel.decision_hub_kernel.application.research_skill_package import decoded
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

class TrialSelectedRun(TrialBase):
    __tablename__ = 'research_method_trial_selected_runs'
    __table_args__ = (UniqueConstraint('receipt_id', 'ordinal'), UniqueConstraint('run_id'), CheckConstraint('ordinal BETWEEN 1 AND 20'))
    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    receipt_hash: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    selection_id: Mapped[str] = mapped_column(String(128), unique=True)
    selection_hash: Mapped[str] = mapped_column(String(64))
for operation in ('UPDATE', 'DELETE'):
    event.listen(TrialSelectedRun.__table__, 'after_create', DDL(f"CREATE TRIGGER trial_run_no_{operation.lower()} BEFORE {operation} ON research_method_trial_selected_runs BEGIN SELECT RAISE(ABORT,'trial_run_immutable'); END").execute_if(dialect='sqlite'))
event.listen(TrialSelectedRun.__table__, 'after_create', DDL("CREATE TRIGGER trial_run_no_replace BEFORE INSERT ON research_method_trial_selected_runs WHEN EXISTS(SELECT 1 FROM research_method_trial_selected_runs WHERE run_id=NEW.run_id OR selection_id=NEW.selection_id OR (receipt_id=NEW.receipt_id AND ordinal=NEW.ordinal)) BEGIN SELECT RAISE(ABORT,'trial_run_immutable'); END").execute_if(dialect='sqlite'))

def census(session, receipt_id, receipt_hash):
    rows = session.scalars(select(TrialSelectedRun).where(TrialSelectedRun.receipt_id == receipt_id).order_by(TrialSelectedRun.ordinal).limit(21).execution_options(populate_existing=True)).all()
    if len(rows) > 20:
        raise ValueError('trial_run_census_exceeds_limit')
    for ordinal, row in enumerate(rows, 1):
        original = session.get(RunRoleOutputRecord, row.selection_id, populate_existing=True)
        if original is None:
            raise ValueError('trial_run_selection_missing')
        body = decoded(original.payload_json)
        if row.ordinal != ordinal or row.receipt_hash != receipt_hash or row.selection_hash != original.payload_hash or (canonical_hash(body) != original.payload_hash) or (original.role != 'research_skill_selection') or (original.schema_version != 'research-skill-selection.v2') or (body['run_id'] != row.run_id) or (original.run_id != row.run_id) or (body['trial_receipt'] != {'ref': receipt_id, 'hash': receipt_hash}) or (body['trial_run_ordinal'] != ordinal):
            raise ValueError('trial_run_census_original_mismatch')
    return tuple((row.run_id for row in rows))

def reserve(session, body, expected_runs):
    receipt = body['trial_receipt']
    if census(session, receipt['ref'], receipt['hash']) != expected_runs:
        raise ValueError('trial_quota_cas_lost')
    if body['run_id'] in expected_runs or body['trial_run_ordinal'] != len(expected_runs) + 1:
        raise ValueError('trial_quota_ordinal_mismatch')
    session.add(TrialSelectedRun(receipt_id=receipt['ref'], receipt_hash=receipt['hash'], run_id=body['run_id'], ordinal=body['trial_run_ordinal'], selection_id=body['selection_id'], selection_hash=canonical_hash(body)))
    session.flush()
