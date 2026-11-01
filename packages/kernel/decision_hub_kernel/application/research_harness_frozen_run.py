"""Manual, server-owned frozen ledger comparisons; never a deployment grant.

Only immutable source rows are reused. Provider callbacks, outcomes and old
Results are not imported. Ordinary research and its archived readers are not
changed. One signed assignment owns two fresh Runs and one original data PIT.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Callable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, insert, select, or_
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ExecutionBudget, ResearchSessionRequest
from packages.contracts_py.decision_hub_contracts.generated.harness_comparison import (
    HarnessFrozenRunCaseV1, HarnessComparisonView, HarnessComparisonCatalog,
)
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchRequestPackReceiptV2, ResearchTaskReceiptV2, ResearchCompiledPolicyReceiptV1,
    ResearchPackDefinitionV2,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import ResearchPolicyResource, ResearchRoleRubricBinding
from packages.kernel.decision_hub_kernel.application.market_scope import require_research_scope
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle, read_research_bundle_in_session, read_factory_source_in_session,
    task_scope, freeze_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context import build_task_policy, select_context
from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    HarnessArtifactIndexV1, InstalledHarnessBaseline, ValidatedHarnessArtifact,
    validate_harness_artifact_bytes,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_policy_compiler import compile_policy
from packages.kernel.decision_hub_kernel.application.run import RunService, RunClaim
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database, RunRecord, StudyRecord, RunRoleOutputRecord, ResearchResultRecord,
    ResearchTraceRecord, utcnow, as_utc,
)

STRATEGY = 'evaluation.harness.v1'
ROLE = 'research_harness_frozen_run_consumption'
SCHEMA = 'research-harness-frozen-run-consumption.v1'
METADATA = MetaData()
ASSIGNMENTS = Table('research_harness_case_assignments', METADATA,
    Column('assignment_id', String(128), primary_key=True),
    Column('request_id', String(256), unique=True, nullable=False),
    Column('baseline_run_id', String(128), unique=True, nullable=False),
    Column('candidate_run_id', String(128), unique=True, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('payload_json', Text, nullable=False), Column('payload_hash', String(64), nullable=False))

Hash = Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')]
Id = Annotated[StrictStr, Field(min_length=1, max_length=256)]

class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

class FrozenAssignment(Closed):
    schema_version: Literal['research-harness-frozen-run-assignment.v1']
    assignment_id: Id
    request_id: Id
    catalog_ref: Id
    candidate_ref: Id
    owner_identity: Id
    case_json: Annotated[StrictStr, Field(max_length=8_000_000)]
    case_sha256: Hash
    baseline_definition_json: Annotated[StrictStr, Field(max_length=1_000_000)]
    baseline_runtime_json: Annotated[StrictStr, Field(max_length=100_000)]
    baseline_artifact_json: Annotated[StrictStr, Field(max_length=1_000_000)]
    candidate_artifact_json: Annotated[StrictStr, Field(max_length=1_000_000)]
    baseline_index: HarnessArtifactIndexV1
    candidate_index: HarnessArtifactIndexV1
    budget_json: Annotated[StrictStr, Field(min_length=1, max_length=10000)]
    baseline_run_id: Id
    candidate_run_id: Id
    created_at: AwareDatetime
    authorization_sha256: Hash
    eligible_for_deployment: Literal[False]
    evolution_pointer_consumed: Literal[False]

class FrozenConsumption(Closed):
    schema_version: Literal['research-harness-frozen-run-consumption.v1']
    run_id: Id
    assignment_id: Id
    assignment_payload_sha256: Hash
    case_sha256: Hash
    world_sha256: Hash
    arm: Literal['baseline','candidate']
    artifact_index: HarnessArtifactIndexV1
    pack_definition_hash: Hash
    full_task_hash: Hash
    compiled_policy_hash: Hash
    factory_definition_consumed: Literal[True]
    native_execution_proof: Literal['unverified']
    eligible_for_deployment: Literal[False]
    recorded_at: AwareDatetime

def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value,str) else value).hexdigest()

def raw(value: BaseModel) -> str:
    return canonical_recipe(value.model_dump(mode='json'))

def dt(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)

KEYS = {'run_role_outputs':'output_id','snapshots':'snapshot_id','research_evidence':'evidence_id',
    'research_facts':'fact_id','run_input_refs':'fact_id','fact_versions':'fact_version_id',
    'evidence_parsed_links':'evidence_id','parsed_versions':'parsed_version_id',
    'raw_captures':'capture_id','source_acquisition_receipts':'receipt_id','product_commands':'request_id'}

def case_rows(case: HarnessFrozenRunCaseV1, table: str) -> list[dict]:
    return [json.loads(row.payload_json) for row in case.original_rows if row.table_name == table]

def validate_case(case: HarnessFrozenRunCaseV1) -> None:
    rows = case.original_rows
    if not rows or len({(r.table_name,r.primary_key) for r in rows}) != len(rows):
        raise ValueError('harness_frozen_original_duplicate')
    for row in rows:
        value=json.loads(row.payload_json)
        if (sha(row.payload_json)!=row.payload_sha256 or canonical_recipe(value)!=row.payload_json
                or value[KEYS[row.table_name]]!=row.primary_key):
            raise ValueError('harness_frozen_original_hash_invalid')
    if sha(canonical_recipe([r.model_dump(mode='json') for r in rows])) != case.world_sha256:
        raise ValueError('harness_frozen_world_hash_invalid')
    if not case.original_context_graph_refs or len({r.output_id for r in case.original_context_graph_refs})!=len(case.original_context_graph_refs):
        raise ValueError('harness_frozen_context_graph_reference_invalid')
    if any(not (r.role.startswith(('context_select.','context_execution_link:','research_graph_round:')) or r.role=='research_graph_final') for r in case.original_context_graph_refs):
        raise ValueError('harness_frozen_context_graph_reference_invalid')
    pack=ResearchRequestPackReceiptV2.model_validate_json(case.original_pack_json)
    task=ResearchTaskReceiptV2.model_validate_json(case.original_task_json)
    compiled=ResearchCompiledPolicyReceiptV1.model_validate_json(case.original_compiled_json)
    from .research_binding import _validate_payloads
    _validate_payloads(pack,task,compiled)
    if (pack.run_id!=case.source_run_id or task.run_id!=case.source_run_id
        or task.request.event_id!=case.source_event_id or task.factory_source.study_id!=case.source_study_id
        or task.request.pit_cutoff_at!=case.pit_cutoff_at):
        raise ValueError('harness_frozen_source_identity_invalid')
    # This first vertical supports the actual A9 ledger only. Do not silently
    # drop private Library permission, watch or window inputs from a later case.
    if task.library_inputs or task.factory_source.library_scope or task.factory_source.watch or task.request.event_window_samples:
        raise ValueError('harness_frozen_source_scope_unsupported')
    if any(item.received_at>case.pit_cutoff_at or item.observed_at>case.pit_cutoff_at for item in task.request.input_evidence):
        raise ValueError('harness_frozen_trigger_after_pit')
    originals={(r.table_name,r.primary_key):json.loads(r.payload_json) for r in rows}
    for role,body in [('research_request_pack',case.original_pack_json),('research_task_request',case.original_task_json),('research_compiled_policy',case.original_compiled_json)]:
        matches=[v for (table,_),v in originals.items() if table=='run_role_outputs' and v['role']==role]
        if len(matches)!=1 or matches[0]['payload_json']!=body or matches[0]['payload_hash']!=sha(body):
            raise ValueError('harness_frozen_bundle_original_missing')
    evidence={r['evidence_id']:r for r in case_rows(case,'research_evidence')}
    facts=case_rows(case,'research_facts')
    refs={r['fact_id']:r for r in case_rows(case,'run_input_refs')}
    links={r['evidence_id']:r for r in case_rows(case,'evidence_parsed_links')}
    for row in evidence.values():
        if row['run_id']!=case.source_run_id or any(dt(row[k])>case.pit_cutoff_at for k in ('received_at','observed_at','accepted_at')):
            raise ValueError('harness_frozen_evidence_after_pit')
        parsed=originals.get(('parsed_versions',links[row['evidence_id']]['parsed_version_id']))
        if parsed is None or parsed['content_hash']!=row['content_hash']:
            raise ValueError('harness_frozen_parsed_original_missing')
        for capture_id in json.loads(parsed['capture_ids_json']):
            if ('raw_captures',capture_id) not in originals:
                raise ValueError('harness_frozen_capture_metadata_missing')
    for row in facts:
        if row['run_id']!=case.source_run_id or row['evidence_id'] not in evidence or any(dt(row[k])>case.pit_cutoff_at for k in ('received_at','observed_at','accepted_at')):
            raise ValueError('harness_frozen_fact_after_pit')
        version=originals.get(('fact_versions',refs[row['fact_id']]['fact_version_id']))
        if (version is None or dt(version['known_at'])>case.pit_cutoff_at
            or json.loads(version['payload_json'])['payload_hash']!=row['payload_hash']
            or version['parsed_version_id']!=links[row['evidence_id']]['parsed_version_id']):
            raise ValueError('harness_frozen_fact_version_invalid')
    commands=case_rows(case,'product_commands')
    if not any(r['owner_id']==case.original_owner_identity and json.loads(r['result_json']).get('result',{}).get('target_run_id')==case.source_run_id for r in commands):
        raise ValueError('harness_frozen_owner_original_missing')

def verify_originals(session: Session, case: HarnessFrozenRunCaseV1) -> None:
    validate_case(case)
    for row in case.original_rows:
        key=KEYS[row.table_name]
        # All identifiers come from the closed enum above, never user SQL.
        found=session.connection().exec_driver_sql(
            f'SELECT * FROM {row.table_name} WHERE {key}=? LIMIT 2',(row.primary_key,)).mappings().all()
        if len(found)!=1 or canonical_recipe(dict(found[0]))!=row.payload_json:
            raise ValueError('harness_frozen_original_changed')
    for ref in case.original_context_graph_refs:
        row=session.get(RunRoleOutputRecord,ref.output_id,populate_existing=True)
        if (row is None or row.run_id!=case.source_run_id or row.role!=ref.role or row.schema_version!=ref.schema_version
            or row.payload_hash!=ref.payload_sha256 or sha(row.payload_json)!=ref.payload_sha256 or as_utc(row.created_at)!=ref.created_at):
            raise ValueError('harness_frozen_context_graph_original_changed')
    source=session.get(RunRecord,case.source_run_id,populate_existing=True)
    study=session.get(StudyRecord,case.source_study_id,populate_existing=True)
    if source is None or study is None or source.event_id!=case.source_event_id or source.study_id!=case.source_study_id:
        raise ValueError('harness_frozen_source_unavailable')
    task=ResearchTaskReceiptV2.model_validate_json(case.original_task_json)
    require_research_scope(study,domain_pack_ref=task.request.domain_pack_ref)
    identity=task.factory_source.study.model_dump(mode='json')
    for current,original in [('scope_key','scope_key'),('question_key','question_key'),('market','market'),('instrument','instrument'),('horizon_scope','raw_horizon_scope'),('identity_basis','identity_basis')]:
        if getattr(study,current)!=identity[original]:
            raise ValueError('harness_frozen_study_scope_changed')

def read_assignment(session: Session, *, assignment_id: str | None=None, run_id: str | None=None) -> tuple[FrozenAssignment,str] | None:
    predicate=ASSIGNMENTS.c.assignment_id==assignment_id if assignment_id else or_(ASSIGNMENTS.c.baseline_run_id==run_id,ASSIGNMENTS.c.candidate_run_id==run_id)
    row=session.execute(select(ASSIGNMENTS).where(predicate)).mappings().one_or_none()
    if row is None:return None
    body=FrozenAssignment.model_validate_json(row['payload_json'])
    if (row['payload_hash']!=sha(row['payload_json']) or raw(body)!=row['payload_json']
        or row['assignment_id']!=body.assignment_id or row['request_id']!=body.request_id
        or row['baseline_run_id']!=body.baseline_run_id or row['candidate_run_id']!=body.candidate_run_id
        or as_utc(row['created_at'])!=body.created_at or sha(body.case_json)!=body.case_sha256):
        raise ValueError('harness_frozen_assignment_corrupt')
    validate_case(HarnessFrozenRunCaseV1.model_validate_json(body.case_json))
    return body,row['payload_hash']

def artifact_for(body: FrozenAssignment, run_id: str) -> tuple[str,ValidatedHarnessArtifact]:
    if run_id==body.baseline_run_id:arm,index,text='baseline',body.baseline_index,body.baseline_artifact_json
    elif run_id==body.candidate_run_id:arm,index,text='candidate',body.candidate_index,body.candidate_artifact_json
    else:raise ValueError('harness_frozen_arm_identity_invalid')
    baseline=InstalledHarnessBaseline(body.baseline_definition_json.encode(),body.baseline_runtime_json.encode())
    return arm,validate_harness_artifact_bytes(text.encode(),index,baseline=baseline)

def _evaluation_definition(definition: ResearchPackDefinitionV2, mode: Literal['live','replay']) -> ResearchPackDefinitionV2:
    # The signed artifact stays immutable. Frozen material evaluation derives
    # only its execution clock mode; every policy/resource/budget byte remains
    # bound to that artifact. Existing live bundles retain their exact mode.
    if mode == 'live':
        return definition
    body=definition.model_dump(mode='json')
    body['execution_mode']='replay'
    return ResearchPackDefinitionV2.model_validate(body)

def read_consumption(session: Session,bundle: FrozenResearchBundle) -> FrozenConsumption | None:
    row=session.scalar(select(RunRoleOutputRecord).filter_by(run_id=bundle.pack.run_id,role=ROLE).execution_options(populate_existing=True))
    if row is None:return None
    found=read_assignment(session,run_id=bundle.pack.run_id)
    if found is None:raise ValueError('harness_frozen_assignment_missing')
    body,original_hash=found
    receipt=FrozenConsumption.model_validate_json(row.payload_json)
    arm,artifact=artifact_for(body,bundle.pack.run_id)
    case=HarnessFrozenRunCaseV1.model_validate_json(body.case_json)
    verify_originals(session,case)
    if (row.output_id!='harness-frozen-consumed_'+sha(bundle.pack.run_id) or row.schema_version!=SCHEMA
        or row.payload_hash!=sha(row.payload_json) or raw(receipt)!=row.payload_json
        or receipt.run_id!=bundle.pack.run_id or receipt.assignment_id!=body.assignment_id
        or receipt.assignment_payload_sha256!=original_hash or receipt.case_sha256!=body.case_sha256
        or receipt.world_sha256!=case.world_sha256 or receipt.arm!=arm or receipt.artifact_index!=artifact.index
        or _evaluation_definition(artifact.definition,bundle.request.execution_mode)!=bundle.pack.definition or receipt.pack_definition_hash!=bundle.pack.definition_hash
        or receipt.full_task_hash!=bundle.task.request_hash or receipt.compiled_policy_hash!=bundle.compiled.compiled.compiled_hash
        or receipt.recorded_at!=bundle.recorded_at or as_utc(row.created_at)!=receipt.recorded_at
        or bundle.request.pit_cutoff_at!=case.pit_cutoff_at):
        raise ValueError('harness_frozen_consumption_originals_changed')
    return receipt

def read_any_consumption(session: Session,bundle: FrozenResearchBundle):
    from .research_harness_consumption import read_harness_consumption_in_session
    release=read_harness_consumption_in_session(session,bundle)
    assigned=read_consumption(session,bundle)
    if release is not None and assigned is not None:raise ValueError('harness_consumption_scope_mixed')
    if assigned is None:return release
    body,_=read_assignment(session,run_id=bundle.pack.run_id)
    baseline=InstalledHarnessBaseline(body.baseline_definition_json.encode(),body.baseline_runtime_json.encode())
    return SimpleNamespace(selection=SimpleNamespace(candidate=SimpleNamespace(installed_runtime_binding=baseline.runtime,artifact_index=assigned.artifact_index)),
        pack_definition_hash=assigned.pack_definition_hash,full_task_hash=assigned.full_task_hash,
        compiled_policy_hash=assigned.compiled_policy_hash,consumption_role=ROLE)

@dataclass(frozen=True)
class FrozenCatalog:
    case_json: str
    baseline: InstalledHarnessBaseline
    baseline_artifact: ValidatedHarnessArtifact
    candidate_artifact: ValidatedHarnessArtifact
    candidate_ref: str
    candidate_label: str
    budget_json: str

class FrozenRunCostAccounting(ABC):
    """Trusted in-process accounting capability; never caller configuration.

    Implementations must authenticate complete per-Run usage and pricing for
    the exact installed runtime before declaring this capability available.
    This preflight grants neither a cost value nor deployment qualification;
    completion still verifies the immutable result against its original budget.
    """
    @abstractmethod
    def require_available(self, *, baseline: InstalledHarnessBaseline,
                          budget: ExecutionBudget) -> None:
        """Raise harness_cost_accounting_unavailable when accounting is absent."""


class FrozenRunHarnessService:
    def __init__(self,database: Database,*,catalog: FrozenCatalog,owner_identity: str,authorizer: object,
                 signing_key: bytes,clock: Callable[[],datetime]=utcnow,raw_verifier: Callable | None=None,
                 cost_accounting: FrozenRunCostAccounting | None=None):
        if not isinstance(signing_key,bytes) or len(signing_key)<32:raise ValueError('harness_frozen_private_key_required')
        self.database,self.catalog,self.owner_identity,self.authorizer,self.clock=database,catalog,owner_identity,authorizer,clock
        self._key=bytes(signing_key)
        self.raw_verifier=raw_verifier
        if cost_accounting is not None and not isinstance(cost_accounting,FrozenRunCostAccounting):
            raise ValueError('harness_cost_accounting_adapter_invalid')
        self._cost_accounting=cost_accounting
        self.case=HarnessFrozenRunCaseV1.model_validate_json(catalog.case_json);validate_case(self.case)
        if self.case.original_owner_identity!=owner_identity:raise ValueError('harness_frozen_owner_scope_invalid')
        for artifact in (catalog.baseline_artifact,catalog.candidate_artifact):
            validate_harness_artifact_bytes(artifact.raw_bytes,artifact.index,baseline=catalog.baseline)
        original=ResearchRequestPackReceiptV2.model_validate_json(self.case.original_pack_json)
        if original.definition!=catalog.baseline.definition:raise ValueError('harness_frozen_original_pack_changed')
        if catalog.baseline_artifact.definition!=catalog.baseline.definition or catalog.baseline_artifact.index==catalog.candidate_artifact.index:
            raise ValueError('harness_frozen_two_methods_required')
        budget=ExecutionBudget.model_validate_json(catalog.budget_json)
        old=ResearchTaskReceiptV2.model_validate_json(self.case.original_task_json).request.execution_budget
        for key,value in budget.model_dump().items():
            if value>getattr(old,key) or value>getattr(catalog.baseline.definition.manifest.execution_budget,key):
                raise ValueError('harness_frozen_budget_expanded')
        if self.raw_verifier is None:raise ValueError('harness_frozen_raw_verifier_required')
        self.raw_verifier(self.case)
    def _mac(self,body: dict) -> str:
        value=dict(body);value.pop('authorization_sha256',None)
        return hmac.new(self._key,b'harness-frozen-run-assignment.v1\0'+canonical_recipe(value).encode(),hashlib.sha256).hexdigest()
    def _verify_owned(self,session: Session,body: FrozenAssignment,*,current_catalog: bool=True) -> None:
        if body.owner_identity!=self.owner_identity or not hmac.compare_digest(body.authorization_sha256,self._mac(body.model_dump(mode='json'))):
            raise PermissionError('harness_frozen_assignment_authorization_invalid')
        # Config/catalog cannot rebind a queued arm to new bytes after restart.
        if current_catalog and (body.case_json!=self.catalog.case_json or body.budget_json!=self.catalog.budget_json
            or body.baseline_definition_json!=self.catalog.baseline.definition_json.decode()
            or body.baseline_runtime_json!=self.catalog.baseline.runtime_json.decode()
            or body.baseline_artifact_json!=self.catalog.baseline_artifact.raw_bytes.decode()
            or body.candidate_artifact_json!=self.catalog.candidate_artifact.raw_bytes.decode()
            or body.candidate_ref!=self.catalog.candidate_ref):raise ValueError('harness_frozen_catalog_changed')
        verify_originals(session,HarnessFrozenRunCaseV1.model_validate_json(body.case_json))
        self.raw_verifier(HarnessFrozenRunCaseV1.model_validate_json(body.case_json))
    def _require_cost_accounting(self) -> None:
        if self._cost_accounting is None:
            raise ValueError('harness_cost_accounting_unavailable')
        self._cost_accounting.require_available(
            baseline=self.catalog.baseline,
            budget=ExecutionBudget.model_validate_json(self.catalog.budget_json),
        )
    def catalog_view(self,owner_authenticated: bool=False) -> HarnessComparisonCatalog:
        with self.database.session() as session:
            verify_originals(session,self.case)
        task=ResearchTaskReceiptV2.model_validate_json(self.case.original_task_json)
        budget=ExecutionBudget.model_validate_json(self.catalog.budget_json)
        title=task.request.input_evidence[0].excerpt[:4000] if task.request.input_evidence else '冻结研究材料'
        available=True
        try:self._require_cost_accounting()
        except ValueError as error:
            if str(error)!='harness_cost_accounting_unavailable':raise
            available=False
        return HarnessComparisonCatalog.model_validate_json(canonical_recipe({
            'schema_version':'harness-comparison-catalog.v1','available':available,'blocked_reason':None if available else 'harness_cost_accounting_unavailable','owner_authenticated':owner_authenticated,
            'sources':[{'catalog_ref':self.case.catalog_ref,'source_run_id':self.case.source_run_id,'event_title':title,
                'pit_cutoff_at':self.case.pit_cutoff_at.isoformat(),'source_mode':'frozen_run',
                'evidence_count':len(case_rows(self.case,'research_evidence')),'fact_count':len(case_rows(self.case,'research_facts'))}],
            'methods':[{'candidate_ref':self.catalog.candidate_ref,'label':self.catalog.candidate_label,'artifact_sha256':self.catalog.candidate_artifact.index.artifact_bytes_sha256}],
            'budget':{'per_arm_max_cost_usd':budget.max_estimated_cost_usd,'total_max_cost_usd':2*budget.max_estimated_cost_usd,'total_deadline_seconds':budget.total_deadline_seconds},'eligible_for_deployment':False}))
    def create(self,*,catalog_ref: str,candidate_ref: str,request_id: str,principal: object) -> str:
        pack=self.catalog.baseline.definition.manifest
        domain=f'{pack.pack_id}.v{pack.version.split(".")[0]}'
        self.authorizer.require_owner(principal,owner_identity=self.owner_identity,domain_pack_ref=domain,operation='evaluate')
        if catalog_ref!=self.case.catalog_ref or candidate_ref!=self.catalog.candidate_ref:raise ValueError('harness_frozen_catalog_selection_changed')
        if not request_id or len(request_id)>256:raise ValueError('harness_frozen_request_id_invalid')
        assignment_id='hpair_'+sha(self.owner_identity+'\0'+request_id)
        # Large validation/read happens before the short authoritative writer.
        with self.database.session() as read:
            prior=read_assignment(read,assignment_id=assignment_id)
            if prior is not None:
                self._verify_owned(read,prior[0]);return assignment_id
            self._require_cost_accounting()
            verify_originals(read,self.case)
        self.raw_verifier(self.case)
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            prior=read_assignment(session,assignment_id=assignment_id)
            if prior is not None:
                self._verify_owned(session,prior[0]);return assignment_id
            verify_originals(session,self.case)
            self.raw_verifier(self.case)
            self.authorizer.require_owner(principal,owner_identity=self.owner_identity,domain_pack_ref=domain,operation='evaluate')
            self._require_cost_accounting()
            # One submitted request creates a pair once, with fixed per-arm
            # budget. An unfinished prior pair blocks another chargeable pair.
            active=session.execute(select(ASSIGNMENTS.c.baseline_run_id,ASSIGNMENTS.c.candidate_run_id).order_by(ASSIGNMENTS.c.created_at.desc()).limit(32)).all()
            for left,right in active:
                if any((r:=session.get(RunRecord,rid,populate_existing=True)) is not None and r.status in {'admitted','dispatched','running'} for rid in (left,right)):
                    raise ValueError('harness_frozen_comparison_active')
            now=self.clock();runs=RunService(self.database,clock=self.clock)
            ids=[runs.create(self.case.source_event_id,assignment_id+':'+arm,strategy_version=STRATEGY,
                parent_run_id=self.case.source_run_id,admission_origin='manual',study_id=self.case.source_study_id,session=session)[0] for arm in ('baseline','candidate')]
            value={'schema_version':'research-harness-frozen-run-assignment.v1','assignment_id':assignment_id,'request_id':request_id,
                'catalog_ref':catalog_ref,'candidate_ref':candidate_ref,'owner_identity':self.owner_identity,
                'case_json':self.catalog.case_json,'case_sha256':sha(self.catalog.case_json),
                'baseline_definition_json':self.catalog.baseline.definition_json.decode(),'baseline_runtime_json':self.catalog.baseline.runtime_json.decode(),
                'baseline_artifact_json':self.catalog.baseline_artifact.raw_bytes.decode(),'candidate_artifact_json':self.catalog.candidate_artifact.raw_bytes.decode(),
                'baseline_index':self.catalog.baseline_artifact.index.model_dump(mode='json'),'candidate_index':self.catalog.candidate_artifact.index.model_dump(mode='json'),
                'budget_json':self.catalog.budget_json,'baseline_run_id':ids[0],'candidate_run_id':ids[1],'created_at':now.isoformat(),
                'eligible_for_deployment':False,'evolution_pointer_consumed':False}
            # Sign the exact typed JSON that will be saved (AwareDatetime
            # canonicalizes UTC to Z). Signing pre-validation +00:00 text
            # would reject the first read of our own immutable assignment.
            value['authorization_sha256']='0'*64
            value=FrozenAssignment.model_validate_json(canonical_recipe(value)).model_dump(mode='json')
            value['authorization_sha256']=self._mac(value)
            body=FrozenAssignment.model_validate_json(canonical_recipe(value));encoded=raw(body)
            session.execute(insert(ASSIGNMENTS).values(assignment_id=assignment_id,request_id=request_id,baseline_run_id=ids[0],candidate_run_id=ids[1],created_at=now,payload_json=encoded,payload_hash=sha(encoded)))
            return assignment_id
    def contains(self,run_id: str) -> bool:
        with self.database.session() as session:
            found=read_assignment(session,run_id=run_id)
            if found is None:return False
            self._verify_owned(session,found[0]);return True
    def validate_scope(self,event_id: str,run_id: str):
        with self.database.session() as session:
            found=read_assignment(session,run_id=run_id)
            if found is None:raise ValueError('harness_frozen_assignment_missing')
            self._verify_owned(session,found[0])
            run=session.get(RunRecord,run_id,populate_existing=True)
            if run is None or run.event_id!=event_id or run.strategy_version!=STRATEGY:raise ValueError('harness_frozen_run_original_changed')
            saved=read_research_bundle_in_session(session,run_id)
            if not isinstance(saved,FrozenResearchBundle) or read_consumption(session,saved) is None:raise ValueError('harness_frozen_bundle_original_missing')
            return saved.pack.definition.manifest
    def claim_next(self,*,worker_id: str,lease_seconds: int) -> RunClaim | None:
        now=self.clock()
        with self.database.session() as session:
            eligible=select(RunRecord.run_id).where(
                RunRecord.run_id.in_((ASSIGNMENTS.c.baseline_run_id,ASSIGNMENTS.c.candidate_run_id)),
                RunRecord.strategy_version==STRATEGY,
                or_(RunRecord.status=='admitted',
                    (RunRecord.status=='running') & (RunRecord.lease_expires_at<=now)))
            rows=session.execute(select(ASSIGNMENTS).where(eligible.exists()).order_by(ASSIGNMENTS.c.created_at,ASSIGNMENTS.c.assignment_id).limit(8)).mappings().all()
            for row in rows:
                body,_=read_assignment(session,assignment_id=row['assignment_id']);self._verify_owned(session,body)
                for rid in (body.baseline_run_id,body.candidate_run_id):
                    current=session.get(RunRecord,rid,populate_existing=True)
                    if current is None or current.strategy_version!=STRATEGY:raise ValueError('harness_frozen_run_original_changed')
                    recovered=current.status=='running' and current.lease_expires_at is not None and as_utc(current.lease_expires_at)<=now
                    if current.status=='admitted' or recovered:
                        # Claim CAS repeats strategy/lease/cancellation eligibility.
                        if RunService(self.database,clock=self.clock).claim_for_execution(rid,worker_id=worker_id,lease_seconds=lease_seconds,strategy_version=STRATEGY):
                            return RunClaim(event_id=current.event_id,run_id=rid,strategy_version=STRATEGY,worker_id=worker_id,lease_expires_at=now+timedelta(seconds=lease_seconds),recovered=recovered)
        return None
    def build(self,event_id: str,run_id: str) -> ResearchSessionRequest:
        from .research_graph_recipe import graph_recipe_task_creation_scope, freeze_graph_recipe_selection_in_session
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            found=read_assignment(session,run_id=run_id)
            if found is None:raise ValueError('harness_frozen_assignment_missing')
            body,original_hash=found;self._verify_owned(session,body)
            run=session.get(RunRecord,run_id,populate_existing=True)
            if run is None or run.event_id!=event_id or run.strategy_version!=STRATEGY or run.status!='running':raise ValueError('harness_frozen_run_not_claimed')
            saved=read_research_bundle_in_session(session,run_id)
            if saved is not None:
                if not isinstance(saved,FrozenResearchBundle):raise ValueError('harness_frozen_legacy_bundle_forbidden')
                read_consumption(session,saved);return saved.request
            case=HarnessFrozenRunCaseV1.model_validate_json(body.case_json)
            original=ResearchTaskReceiptV2.model_validate_json(case.original_task_json)
            arm,artifact=artifact_for(body,run_id);definition=_evaluation_definition(artifact.definition,'replay')
            from packages.kernel.decision_hub_kernel.persistence.db import SnapshotRecord
            from .snapshot import _run_snapshot_id
            original_trigger=session.get(SnapshotRecord,original.request.trigger_snapshot_id,populate_existing=True)
            if original_trigger is None:raise ValueError('harness_frozen_trigger_original_missing')
            now=self.clock()
            # Ordinary Factory requires a Run-owned trigger container. Its
            # material bytes/PIT/hash stay exact; this is a new manual container
            # referencing the old one, never a fabricated new source event.
            trigger_id=_run_snapshot_id(run_id,original_trigger.snapshot_hash)
            if session.get(SnapshotRecord,trigger_id,populate_existing=True) is not None:raise ValueError('harness_frozen_trigger_container_conflict')
            session.add(SnapshotRecord(snapshot_id=trigger_id,event_id=original_trigger.event_id,
                pack_version=original_trigger.pack_version,snapshot_hash=original_trigger.snapshot_hash,
                cutoff_at=original_trigger.cutoff_at,evidence_json=original_trigger.evidence_json,
                snapshot_type='trigger',run_id=run_id,generation=original_trigger.generation,
                parent_snapshot_id=original_trigger.snapshot_id,created_at=now))
            run.snapshot_id=trigger_id;session.flush()
            source=read_factory_source_in_session(session,run_id)
            compare=source.model_dump(mode='json');compare['run_id']=case.source_run_id;compare.pop('source_hash')
            expected=original.factory_source.model_dump(mode='json');expected.pop('source_hash')
            # Tracking pause updates this bookkeeping timestamp. Manual case
            # evaluation retains the current first source while all semantic
            # identity, permissions, creation time and trigger remain exact.
            compare['study'].pop('updated_at');expected['study'].pop('updated_at')
            for field in ('snapshot_id','run_id','created_at'):
                compare['trigger'][field]=expected['trigger'][field]
            if compare!=expected:raise ValueError('harness_frozen_factory_source_changed')
            budget=ExecutionBudget.model_validate_json(body.budget_json)
            request_body=original.request.model_dump(mode='json')
            policy=build_task_policy(research_mode=request_body['research_mode'],requirements=list(original.request.evidence_requirements),created_at=now,max_evidence_rounds=budget.max_evidence_rounds)
            inputs,manifest=select_context(list(original.request.input_evidence),[],requirements=list(original.request.evidence_requirements),policy=policy,manifest_prefix='context:'+run_id,revision=1,created_at=now,allowed_capabilities=list(original.request.allowed_capabilities),eligibility_cutoff_at=case.pit_cutoff_at)
            request_body.update(request_id='research-request:'+run_id,run_id=run_id,current_round=1,execution_mode='replay',
                trigger_snapshot_id=trigger_id,
                input_evidence=[i.model_dump(mode='json') for i in inputs],evidence_refs=[i.evidence_id for i in inputs],
                context_manifest=manifest.model_dump(mode='json'),task_policy_ref=f'{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}',
                execution_budget=budget.model_dump(mode='json'),deadline_at=(now+timedelta(seconds=budget.total_deadline_seconds)).isoformat(),target_gaps=[],
                repair_instructions=f'正式方法对照：只使用来源研究 {case.source_run_id} 的固定材料，数据截止 {case.pit_cutoff_at.isoformat()}，世界摘要 {case.world_sha256}。本次执行时钟与资料时间分离；工具仅返回已冻结指标，不抓取新数据，缺失指标保持未知。结果仅比较材料与推理，不准入交易或部署。')
            request=ResearchSessionRequest.model_validate(request_body);scope=task_scope(request,source)
            compiled=compile_policy(ResearchPolicyResource.model_validate(definition.gate_policy_resource.model_dump(mode='json')),
                ResearchPolicyResource.model_validate(definition.rubric_policy_resource.model_dump(mode='json')),
                [ResearchRoleRubricBinding.model_validate(i.model_dump(mode='json')) for i in definition.role_rubric_bindings],scope=scope)
            dh=sha(canonical_recipe(definition.model_dump(mode='json')));th=sha(canonical_recipe(request.model_dump(mode='json')))
            pack=ResearchRequestPackReceiptV2.model_validate_json(canonical_recipe({'schema_version':'research-request-pack.v2','run_id':run_id,'definition':definition.model_dump(mode='json'),'definition_hash':dh}))
            task=ResearchTaskReceiptV2.model_validate_json(canonical_recipe({'schema_version':'research-task-request.v2','run_id':run_id,'request':request.model_dump(mode='json'),'request_hash':th,'request_hash_kind':'complete_task_request.v1','factory_source':source.model_dump(mode='json'),'scope':scope.model_dump(mode='json'),'pack_definition_hash':dh,'compiled_policy_hash':compiled.compiled_hash,'library_inputs':[]}))
            comp=ResearchCompiledPolicyReceiptV1.model_validate_json(canonical_recipe({'schema_version':'research-compiled-policy-receipt.v1','run_id':run_id,'event_id':event_id,'full_task_hash':th,'pack_definition_hash':dh,'compiled':compiled.model_dump(mode='json')}))
            with graph_recipe_task_creation_scope(session,run_id):
                bundle=freeze_research_bundle_in_session(session,run_id,pack,task,comp,now)
                freeze_graph_recipe_selection_in_session(session,run_id=run_id,recorded_at=now)
                receipt=FrozenConsumption(schema_version=SCHEMA,run_id=run_id,assignment_id=body.assignment_id,
                    assignment_payload_sha256=original_hash,case_sha256=body.case_sha256,world_sha256=case.world_sha256,
                    arm=arm,artifact_index=artifact.index,pack_definition_hash=dh,full_task_hash=th,compiled_policy_hash=compiled.compiled_hash,
                    factory_definition_consumed=True,native_execution_proof='unverified',eligible_for_deployment=False,recorded_at=now)
                encoded=raw(receipt);session.add(RunRoleOutputRecord(output_id='harness-frozen-consumed_'+sha(run_id),run_id=run_id,role=ROLE,schema_version=SCHEMA,payload_json=encoded,payload_hash=sha(encoded),created_at=now));session.flush()
                read_consumption(session,bundle)
            return bundle.request

    def by_request(self,request_id: str) -> str | None:
        with self.database.session() as session:
            return session.scalar(select(ASSIGNMENTS.c.assignment_id).where(ASSIGNMENTS.c.request_id==request_id))

    def complete(self,run_id: str,event_id: str,result,reference,*,worker_id: str) -> None:
        from .research_graph_binding import verify_declared_graph_result_in_session
        from .research_observability import ResearchObservabilityService
        from .research_graph_binding import _permission,_fence
        if reference is None:raise ValueError('harness_frozen_current_graph_final_required')
        # Prepare original Graph/Context/native execution proofs without a
        # writer, then repeat them in the actual publication transaction.
        with self.database.session() as read:
            verify_declared_graph_result_in_session(read,run_id=run_id,reference=reference,declared_result=result,expected_lease_owner=worker_id)
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            found=read_assignment(session,run_id=run_id)
            if found is None:raise ValueError('harness_frozen_assignment_missing')
            self._verify_owned(session,found[0])
            _permission(session,run_id,worker_id)
            frozen=verify_declared_graph_result_in_session(session,run_id=run_id,reference=reference,declared_result=result,expected_lease_owner=worker_id)
            bundle=read_research_bundle_in_session(session,run_id)
            if not isinstance(bundle,FrozenResearchBundle) or read_consumption(session,bundle) is None:raise ValueError('harness_frozen_bundle_original_missing')
            if verify_recorded_root(session,bundle) is None:raise ValueError('harness_frozen_current_root_witness_required')
            budget=ExecutionBudget.model_validate_json(found[0].budget_json)
            _fence(session,run_id,worker_id)
            ResearchObservabilityService(self.database).save_result_in_session(session,run_id,frozen.result)
            run=session.get(RunRecord,run_id,populate_existing=True)
            if run.event_id!=event_id or run.strategy_version!=STRATEGY:raise ValueError('harness_frozen_run_original_changed')
            now=self.clock()
            if result.estimated_cost_usd is None or result.estimated_cost_usd>budget.max_estimated_cost_usd:
                run.status='failed';run.error_code='harness_frozen_cost_unverified_or_exceeded'
            else:
                run.status='degraded' if result.status=='degraded' else 'completed' if result.status=='completed' else 'failed'
                if run.status=='failed':run.error_code=result.stop_reason.code
            run.runtime_version=result.runtime_version;run.cost_usd=result.estimated_cost_usd
            run.latency_ms=round((result.finished_at-result.started_at).total_seconds()*1000)
            run.finished_at=now;run.updated_at=now;run.lease_owner=None;run.lease_expires_at=None
            # Comparison Results remain in their two Runs. Do not publish
            # Artifact/forecast/Study revision or schedule automatic rechecks.

    def cancel(self,assignment_id: str,request_id: str,*,principal: object) -> None:
        from packages.contracts_py.decision_hub_contracts import ResearchRunCommand,ResearchRunCommandResult,ResearchRunCommandReceipt
        from .research_observability import _command_payload_hash,ResearchCommandService
        from packages.kernel.decision_hub_kernel.persistence.db import ResearchCommandRecord,ProductCommandRecord
        pack=self.catalog.baseline.definition.manifest
        self.authorizer.require_owner(principal,owner_identity=self.owner_identity,domain_pack_ref=f'{pack.pack_id}.v{pack.version.split(".")[0]}',operation='evaluate')
        if not request_id or len(request_id)>256:raise ValueError('harness_frozen_cancel_request_invalid')
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            found=read_assignment(session,assignment_id=assignment_id)
            if found is None:raise KeyError(assignment_id)
            self._verify_owned(session,found[0])
            for arm,rid in [('baseline',found[0].baseline_run_id),('candidate',found[0].candidate_run_id)]:
                command_id='hcancel_'+sha(assignment_id+'\0'+request_id+'\0'+arm)
                reason='停止本次正式研究方法对照，保留已产生的材料与执行记录。'
                old=session.get(ProductCommandRecord,command_id,populate_existing=True)
                if old is not None:
                    receipt=ResearchCommandService._receipt_view(old)
                    if receipt.source_run_id!=rid or receipt.owner_id!=self.owner_identity or receipt.command.reason!=reason:raise ValueError('harness_frozen_cancel_original_changed')
                    continue
                run=session.get(RunRecord,rid,populate_existing=True)
                if run is None or run.strategy_version!=STRATEGY:raise ValueError('harness_frozen_run_original_changed')
                now=self.clock();accepted=run.status in {'admitted','dispatched','running','cancelled'}
                command=ResearchRunCommand(schema_version='research-run-command.v1',request_id=command_id,command='cancel',reason=reason)
                status='accepted' if accepted else 'rejected'
                result=ResearchRunCommandResult(schema_version='research-run-command-result.v1',request_id=command_id,command='cancel',source_run_id=rid,target_run_id=None,status=status,created_at=now)
                receipt=ResearchRunCommandReceipt(schema_version='research-run-command-receipt.v1',request_id=command_id,owner_id=self.owner_identity,source_run_id=rid,command=command,result=result)
                session.add(ResearchCommandRecord(request_id=command_id,run_id=rid,command='cancel',reason=reason,target_run_id=None,status=status,created_at=now))
                session.add(ProductCommandRecord(request_id=command_id,owner_id=self.owner_identity,target_id=rid,kind=ResearchCommandService.KIND,payload_hash=_command_payload_hash(self.owner_identity,rid,command),result_json=raw(receipt),created_at=now))
                if accepted:
                    run.status='cancelled';run.error_code='cancelled_by_owner';run.finished_at=now;run.updated_at=now;run.lease_owner=None;run.lease_expires_at=None

    def detail(self,assignment_id: str) -> HarnessComparisonView:
        from .research_harness_execution import verify_harness_method_in_session
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN')
            found=read_assignment(session,assignment_id=assignment_id)
            if found is None:raise KeyError(assignment_id)
            body,_=found
            old_runs=[session.get(RunRecord,rid,populate_existing=True) for rid in (body.baseline_run_id,body.candidate_run_id)]
            terminal=all(run is not None and run.strategy_version==STRATEGY and run.status in {'completed','degraded','failed','cancelled'} for run in old_runs)
            # Only immutable terminal display may outlive the installed catalog.
            # Signed owner/original material/artifact/bundle/root proofs remain;
            # every operation that can execute or cancel keeps the current gate.
            self._verify_owned(session,body,current_catalog=not terminal)
            def view(rid: str):
                run=session.get(RunRecord,rid,populate_existing=True);arm,artifact=artifact_for(body,rid)
                if run is None or run.strategy_version!=STRATEGY:raise ValueError('harness_frozen_run_original_changed')
                proof='pending';reason=None;mh=ch=wh=None
                bundle=read_research_bundle_in_session(session,rid)
                if bundle is not None:
                    if not isinstance(bundle,FrozenResearchBundle):raise ValueError('harness_frozen_bundle_original_invalid')
                    consumed=read_consumption(session,bundle)
                    if consumed is None:raise ValueError('harness_frozen_consumption_missing')
                    cr=session.scalar(select(RunRoleOutputRecord).filter_by(run_id=rid,role=ROLE));ch=cr.payload_hash
                    mr=session.scalar(select(RunRoleOutputRecord).filter_by(run_id=rid,role='research_harness_execution_method'))
                    if mr is not None:
                        verify_harness_method_in_session(session,bundle);mh=mr.payload_hash
                        wh=verify_recorded_root(session,bundle)
                        if wh is not None:proof='verified'
                result=session.get(ResearchResultRecord,rid,populate_existing=True)
                if result is not None and sha(result.payload_json)!=result.payload_hash:raise ValueError('harness_frozen_result_corrupt')
                if run.status in {'failed','cancelled'} and proof!='verified':proof='blocked';reason=run.error_code or 'harness_frozen_execution_incomplete'
                traces=session.scalars(select(ResearchTraceRecord).filter_by(run_id=rid).order_by(ResearchTraceRecord.sequence_no.desc()).limit(100)).all()
                return {'arm':arm,'label':'当前基线方法' if arm=='baseline' else self.catalog.candidate_label,
                    'artifact_sha256':artifact.index.artifact_bytes_sha256,'run_id':rid,'status':run.status,'error_code':run.error_code,
                    'result_json':result.payload_json if result else None,'result_payload_sha256':result.payload_hash if result else None,
                    'proof_status':proof,'proof_reason':reason,'method_payload_sha256':mh,'consumption_payload_sha256':ch,'root_witness_sha256':wh,
                    'traces':[{'trace_id':str(t.id),'event_type':t.event_type,'status':t.status,'summary':t.summary[:8000],'created_at':as_utc(t.created_at).isoformat()} for t in reversed(traces)]}
            case=HarnessFrozenRunCaseV1.model_validate_json(body.case_json)
            return HarnessComparisonView.model_validate_json(canonical_recipe({'schema_version':'harness-comparison-view.v1','assignment_id':assignment_id,'request_id':body.request_id,'catalog_ref':body.catalog_ref,'candidate_ref':body.candidate_ref,'source_run_id':case.source_run_id,'source_mode':'frozen_run','pit_cutoff_at':case.pit_cutoff_at.isoformat(),'created_at':body.created_at.isoformat(),'baseline':view(body.baseline_run_id),'candidate':view(body.candidate_run_id),'eligible_for_deployment':False,'evolution_pointer_consumed':False}))

def verify_recorded_root(session: Session,bundle: FrozenResearchBundle) -> str | None:
    """Audit accepted new root originals without inventing an active lease."""
    from .research_harness_execution import (RecordedRootConsumption,HarnessExecutionView,
        verify_harness_method_in_session,validate_root_input,_witness_role,WITNESS_SCHEMA)
    from .model_dispatch import ModelDispatchService
    from packages.kernel.decision_hub_kernel.persistence.db import DshSessionPromptRecord
    method=verify_harness_method_in_session(session,bundle)
    if method is None:return None
    methodrow=session.scalar(select(RunRoleOutputRecord).filter_by(run_id=bundle.pack.run_id,role='research_harness_execution_method'))
    dispatchrow=session.scalar(select(RunRoleOutputRecord).filter_by(run_id=bundle.pack.run_id,role='research_model_dispatch'))
    if dispatchrow is None:return None
    dispatch=ModelDispatchService._read(dispatchrow)
    originals=session.scalars(select(RunRoleOutputRecord).filter_by(run_id=bundle.pack.run_id).where(RunRoleOutputRecord.role.startswith('harness_root:')).order_by(RunRoleOutputRecord.created_at).limit(100)).all()
    for row in originals:
        if row.schema_version!=WITNESS_SCHEMA or sha(row.payload_json)!=row.payload_hash:raise ValueError('harness_frozen_root_original_corrupt')
        recorded=RecordedRootConsumption.model_validate_json(row.payload_json);payload=recorded.payload
        prompt=session.get(DshSessionPromptRecord,(bundle.pack.run_id,payload.generation),populate_existing=True)
        if (prompt is None or row.role!=_witness_role(payload.generation,payload.host_scope)
            or as_utc(row.created_at)!=recorded.recorded_at
            or prompt.request_hash!=payload.request_hash or prompt.request_id!=payload.request_id
            or prompt.dsh_session_id!=payload.dsh_session_id or dispatch.dsh_session_id!=payload.dsh_session_id
            or dispatch.request_hash!=payload.request_hash or dispatch.plugin_build_hash!=payload.plugin_build_hash
            or dispatch.execution_session_id is not None or dispatch.parent_session_id is not None):
            raise ValueError('harness_frozen_root_originals_changed')
        view=HarnessExecutionView(schema_version='research-harness-execution-view.v1',method=method,
            method_payload_hash=methodrow.payload_hash,run_id=bundle.pack.run_id,dsh_session_id=prompt.dsh_session_id,
            request_id=prompt.request_id,request_hash=prompt.request_hash,generation=prompt.generation,
            plugin_build_hash=method.plugin_build_hash,prompt=prompt.prompt,prompt_hash=sha(prompt.prompt),witness=None)
        validate_root_input(payload,view)
        return row.payload_hash
    return None
