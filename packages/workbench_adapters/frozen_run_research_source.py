"""Serve only a signed arm's verified original ledger slots; HTTP fallback=0."""
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from urllib.parse import urlsplit

from sqlalchemy import DateTime, select
from packages.contracts_py.decision_hub_contracts import ResearchCapabilityResult
from packages.contracts_py.decision_hub_contracts.generated.harness_comparison import HarnessFrozenRunCaseV1
from packages.kernel.decision_hub_kernel.application.research_harness_frozen_run import (
    FrozenRunHarnessService, read_assignment, read_consumption, case_rows, sha, STRATEGY,
)
from packages.kernel.decision_hub_kernel.application.research_binding import read_research_bundle_in_session,FrozenResearchBundle
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    _evidence_model,ResearchCapabilityError,research_evidence_instance_id,
    ResearchCapabilityGatewayService,_effective_domains,_validate_target_url,
    _validate_candidate,_validate_fact,
)
from packages.kernel.decision_hub_kernel.application.fact_store import _fact_model,research_fact_instance_id
from packages.kernel.decision_hub_kernel.persistence.db import ResearchEvidenceRecord,ResearchFactRecord,RunRecord,DshSessionLinkRecord,as_utc

def orm_value(model,row):
    values=dict(row)
    for column in model.__table__.columns:
        if isinstance(column.type,DateTime) and values[column.name] is not None:
            value=datetime.fromisoformat(values[column.name].replace('Z','+00:00'))
            values[column.name]=as_utc(value)
    return model(**values)

def local_historical_manifest(manifest):
    return (manifest.capability_id=='data.historical' and manifest.implementation_ref=='adapter://local/historical-data'
        and manifest.kind=='python_adapter' and 'storage:read' in manifest.permissions and not manifest.allowed_domains)

def retained_query_symbol(capability_id, row):
    if capability_id != 'data.historical':
        return row['instrument']
    from packages.provider_adapters.historical.research import HistoricalDataResearchAdapter
    dataset_id = json.loads(row['attributes_json']).get('dataset_id')
    # A dataset query key is separate from its market instrument. It must be
    # both retained in the signed world and supported by the current route.
    return dataset_id if isinstance(dataset_id, str) and dataset_id in HistoricalDataResearchAdapter.supported_symbols else None

@dataclass(frozen=True)
class FrozenSourceReturn:
    result: ResearchCapabilityResult
    pit_cutoff_at: datetime
    reused_evidence: dict[str,str]
    reused_facts: dict[str,str]

class FrozenRunCapabilitySource:
    def __init__(self,service: FrozenRunHarnessService,*,clock,manifests,enabled_capabilities):
        self.service,self.clock=service,clock
        self.manifests={item.capability_id:item for item in manifests}
        self.enabled=frozenset(enabled_capabilities)
    def assigned(self,run_id: str) -> bool:
        return self.service.contains(run_id)
    def scoped_query_catalog(self,catalog,session_id: str):
        """Expose retained slots before model execution; never require a future root witness.

        A non-assigned strategy still uses the unchanged ordinary catalog reader.
        This is query metadata only; execution repeats signed owner/PIT/rights gates.
        """
        from packages.kernel.decision_hub_kernel.application.research_execution_binding import read_prepared_execution_in_session
        from packages.kernel.decision_hub_kernel.application.research_harness_execution import _fence,verify_harness_method_in_session
        with self.service.database.session() as session:
            link=session.scalar(select(DshSessionLinkRecord).where(DshSessionLinkRecord.dsh_session_id==session_id).execution_options(populate_existing=True))
            if link is None:return None
            run=session.get(RunRecord,link.run_id,populate_existing=True)
            if run is None or run.strategy_version!=STRATEGY:return None
            found=read_assignment(session,run_id=run.run_id)
            if found is None:raise ValueError('harness_frozen_assignment_missing')
            body,_=found
            try:self.service._verify_owned(session,body)
            except PermissionError as error:raise ValueError(str(error)) from error
            case=HarnessFrozenRunCaseV1.model_validate_json(body.case_json)
            if run.event_id!=case.source_event_id or run.study_id!=case.source_study_id or run.parent_run_id!=case.source_run_id:
                raise ValueError('harness_frozen_catalog_run_identity_invalid')
            _fence(session,run.run_id,self.clock())
            prepared=read_prepared_execution_in_session(session,run.run_id)
            bundle=read_research_bundle_in_session(session,run.run_id)
            if not isinstance(bundle,FrozenResearchBundle) or read_consumption(session,bundle) is None:
                raise ValueError('harness_frozen_consumption_missing')
            if verify_harness_method_in_session(session,bundle) is None:
                raise ValueError('harness_method_v4_required')
            if (prepared.link.dsh_session_id!=session_id or prepared.link.request_hash!=link.request_hash
                or prepared.link.generation!=link.generation or prepared.link.upstream_identity.plugin_build_hash!=link.plugin_build_hash):
                raise ValueError('harness_frozen_catalog_method_mismatch')
            definition=prepared.method.definition
            allowed=set(definition.allowed_capabilities)&set(bundle.request.allowed_capabilities)
            mode=definition.execution_mode
            ledger_replay=mode=="replay" and bundle.request.execution_mode=="replay"
            rows=case_rows(case,'research_evidence');facts=case_rows(case,'research_facts')
            capabilities=[]
            for item in catalog['capabilities']:
                capability=item['capability_id'];manifest=self.manifests.get(capability)
                retained=[row for row in rows if row['capability_id']==capability]
                if (capability not in allowed or capability not in self.enabled or not ledger_replay and mode not in item['modes']
                    or manifest is None or manifest.audit_status!='approved' or manifest.license_status!='approved' or not retained):continue
                hosts={urlsplit(row['source_url']).hostname for row in retained if row['source_url']}
                hosts={host for host in hosts if host and any(host==domain or host.endswith('.'+domain) for domain in manifest.allowed_domains)}
                local_dataset=local_historical_manifest(manifest)
                if not hosts and not local_dataset:continue
                retained_facts=[row for row in facts if row['capability_id']==capability]
                routes=[]
                for route in item['routes']:
                    domains=sorted(host for host in hosts if any(host==domain or host.endswith('.'+domain) for domain in route['allowed_domains']))
                    local_route=local_dataset and route['provider_id'] is None and not route['allowed_domains']
                    if not ledger_replay and mode not in route['modes'] or not domains and not local_route:continue
                    fields=None if route['fields'] is None else sorted({row['field'] for row in retained_facts}&set(route['fields']))
                    symbols=None if route.get('symbols') is None else sorted({retained_query_symbol(capability,row) for row in retained_facts}&set(route['symbols']))
                    if route.get('symbols_required') and not symbols:continue
                    narrowed={**route,'allowed_domains':domains,'modes':[mode],'fields':fields,'symbols':symbols,
                        'usage_notes':'Retained original ledger only; no HTTP or live-provider fallback. Missing metrics remain unavailable. '+(route.get('usage_notes') or '')}
                    if route.get('symbol_fields') is not None:
                        narrowed['symbol_fields']={symbol:sorted({row['field'] for row in retained_facts if retained_query_symbol(capability,row)==symbol}&set(values)) for symbol,values in route['symbol_fields'].items() if symbols and symbol in symbols}
                    if route.get('sources') is not None:
                        sources=[]
                        for source in route['sources']:
                            selected=[]
                            for row in retained:
                                url=urlsplit(row['source_url'] or '')
                                if (url.hostname not in domains or not any(url.hostname==domain or url.hostname.endswith('.'+domain) for domain in source['domains'])
                                    or row['requirement_id'] not in source['requirement_ids'] or any(url.path.startswith(path) for path in source.get('excluded_paths',[]))):continue
                                if any(url.path==path if source.get('match_paths_exactly') else url.path.startswith(path) for path in source['allowed_paths']):selected.append((url.hostname,url.path,row['requirement_id']))
                            if selected:sources.append({**source,'domains':sorted({value[0] for value in selected}),'allowed_paths':sorted({value[1] for value in selected}),'requirement_ids':sorted({value[2] for value in selected}),'match_paths_exactly':True})
                        narrowed['sources']=sources
                        if route.get('target_url_required') and not sources:continue
                    # Query metadata comes only from this signed world and
                    # this approved route. Aggregate windows are not raw bars
                    # that can be recomputed for an arbitrary guessed range.
                    eligible_evidence=[]
                    for row in retained:
                        url=urlsplit(row['source_url'] or '')
                        if not local_route and url.hostname not in domains:continue
                        if narrowed.get('sources') is not None and not any(
                            row['requirement_id'] in source['requirement_ids']
                            and url.hostname in source['domains']
                            and any(url.path==path for path in source['allowed_paths'])
                            for source in narrowed['sources']):continue
                        eligible_evidence.append(row)
                    by_evidence={row['evidence_id']:row for row in eligible_evidence}
                    slots={}
                    for fact in retained_facts:
                        evidence=by_evidence.get(fact['evidence_id'])
                        symbol=retained_query_symbol(capability,fact)
                        if (evidence is None or fact['requirement_id']!=evidence['requirement_id']
                            or fields is not None and fact['field'] not in fields
                            or symbols is not None and symbol not in symbols):continue
                        if narrowed.get('symbol_fields') is not None and fact['field'] not in narrowed['symbol_fields'].get(symbol,[]):continue
                        def utc_value(value):
                            return None if value is None else as_utc(datetime.fromisoformat(value.replace('Z','+00:00'))).isoformat()
                        key=(fact['requirement_id'],symbol,utc_value(fact['window_start_at']),utc_value(fact['window_end_at']))
                        slots.setdefault(key,set()).add(fact['field'])
                    available=[{'requirement_id':key[0],'symbol':key[1],'fields':sorted(values),
                        'window_start_at':key[2],'window_end_at':key[3]}
                        for key,values in sorted(slots.items(),key=lambda entry:json.dumps(entry[0],sort_keys=True))][:32]
                    notes={'retained_slots':available,'slots_truncated':len(slots)>len(available),
                        'query_rule':'Use the exact saved window or omit window filters to read its original aggregate. A different range is not recalculated. Unlisted material is unavailable.',
                        'read_mode':'signed-frozen-ledger-replay' if ledger_replay else 'signed-frozen-ledger',
                        'original_provider_modes':route['modes'],'provider_execution':False}
                    narrowed['usage_notes']='Retained original ledger only; no HTTP or live-provider fallback, no provider replay capability is claimed. '+json.dumps(notes,ensure_ascii=False,sort_keys=True,separators=(',',':'))+' '+(route.get('usage_notes') or '')[:2048]
                    routes.append(narrowed)
                if routes:capabilities.append({**item,'allowed_domains':sorted({domain for route in routes for domain in route['allowed_domains']}),'modes':[mode],'routes':routes})
            _fence(session,run.run_id,self.clock())
            payload={'schema_version':'research-query-catalog.v1','capabilities':capabilities,
                'scope':{'run_id':run.run_id,'dsh_session_id':session_id,'request_hash':link.request_hash,'generation':link.generation,'status':'bound','method_hash':prepared.method.method_hash,'execution_mode':mode}}
            return {**payload,'catalog_hash':sha(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')))}
    def execute(self,run_id: str,query) -> FrozenSourceReturn:
        with self.service.database.session() as session:
            found=read_assignment(session,run_id=run_id)
            if found is None:raise ValueError('harness_frozen_assignment_missing')
            body,_=found;self.service._verify_owned(session,body)
            run=session.get(RunRecord,run_id,populate_existing=True)
            bundle=read_research_bundle_in_session(session,run_id)
            if run is None or run.strategy_version!=STRATEGY or run.status!='running' or not isinstance(bundle,FrozenResearchBundle):
                raise ValueError('harness_frozen_source_run_not_active')
            if read_consumption(session,bundle) is None:raise ValueError('harness_frozen_consumption_missing')
            case=HarnessFrozenRunCaseV1.model_validate_json(body.case_json)
            if query.mode!=bundle.request.execution_mode or (query.mode=='replay' and query.cutoff_at>case.pit_cutoff_at):
                raise ValueError('harness_frozen_query_clock_scope_invalid')
            manifest=self.manifests.get(query.capability_id)
            if manifest is None or query.capability_id not in self.enabled or manifest.license_status!='approved' or manifest.audit_status!='approved':
                raise ValueError('harness_frozen_capability_rights_revoked')
            if query.capability_id == 'data.historical':
                if not local_historical_manifest(manifest):raise ValueError('harness_frozen_capability_rights_revoked')
                from packages.provider_adapters.historical.research import HistoricalDataResearchAdapter
                if len(query.symbols) != 1 or query.symbols[0] not in HistoricalDataResearchAdapter.supported_symbols:
                    raise ResearchCapabilityError('historical_dataset_required','固定材料查询需要一个已批准的历史数据集。',origin='gateway',cause_code='frozen_dataset_scope_denied',capability_id=query.capability_id,tool_call_id=query.request_id)
                if query.fields and not set(query.fields)<=HistoricalDataResearchAdapter.symbol_fields[query.symbols[0]]:
                    raise ResearchCapabilityError('historical_field_invalid','固定材料查询包含未批准的历史字段。',origin='gateway',cause_code='frozen_dataset_scope_denied',capability_id=query.capability_id,tool_call_id=query.request_id)
            effective_domains=_effective_domains(query,manifest)
            _validate_target_url(str(query.target_url) if query.target_url else None,effective_domains)
            if query.capability_id not in bundle.request.allowed_capabilities or query.event_id not in {None,case.source_event_id}:
                raise ValueError('harness_frozen_capability_scope_denied')
            candidates=[];facts=[];er={};fr={}
            links={r['evidence_id']:r['parsed_version_id'] for r in case_rows(case,'evidence_parsed_links')}
            refs={r['fact_id']:r['fact_version_id'] for r in case_rows(case,'run_input_refs')}
            original_facts=case_rows(case,'research_facts')
            for row in case_rows(case,'research_evidence'):
                if row['capability_id']!=query.capability_id or row['requirement_id']!=query.requirement_id:continue
                if query.target_url is not None and row['source_url']!=str(query.target_url):continue
                eid=research_evidence_instance_id(content_hash=row['content_hash'],research_session_id=query.research_session_id,
                    content_ref=row['content_ref'],acquisition_receipt_id=row['acquisition_receipt_id'])
                if query.evidence_ref is not None and query.evidence_ref not in {row['evidence_id'],eid}:continue
                if query.content_ref is not None and query.content_ref!=row['content_ref']:continue
                if query.allowed_domains and row['source_url']:
                    host=urlsplit(row['source_url']).hostname
                    if not any(host==domain or (host or '').endswith('.'+domain) for domain in query.allowed_domains):continue
                selected=[]
                for fact in original_facts:
                    if fact['evidence_id']!=row['evidence_id']:continue
                    if query.fields and fact['field'] not in query.fields:continue
                    symbol=retained_query_symbol(query.capability_id,fact)
                    if query.capability_id == 'data.historical' and (symbol is None or fact['field'] not in HistoricalDataResearchAdapter.symbol_fields[symbol]):continue
                    if query.symbols and symbol not in query.symbols:continue
                    if query.requested_event_offsets and fact['event_offset'] not in query.requested_event_offsets:continue
                    if query.window_start_at is not None and fact['window_start_at'] is not None and as_utc(datetime.fromisoformat(fact['window_start_at']))<query.window_start_at:continue
                    if query.window_end_at is not None and fact['window_end_at'] is not None and as_utc(datetime.fromisoformat(fact['window_end_at']))>query.window_end_at:continue
                    selected.append(fact)
                if (query.fields or query.symbols or query.requested_event_offsets) and not selected:continue
                if len(candidates)>=query.max_results:break
                candidate=_evidence_model(orm_value(ResearchEvidenceRecord,row)).model_copy(update={
                    'evidence_id':eid,'tool_call_id':query.request_id,'research_session_id':query.research_session_id,'round':query.round})
                candidates.append(candidate);er[eid]=links[row['evidence_id']]
                for original in selected:
                    fact=_fact_model(orm_value(ResearchFactRecord,original))
                    # Exact historical daily receipts stay inside the original
                    # hashed FactVersion as data. They are never executed or
                    # promoted to this new Run's daily/native authority role.
                    if any(('execution_receipt' in key and not (query.capability_id=='data.historical' and key=='daily_execution_receipt_json')) or 'native_observation' in key for key in fact.attributes):
                        raise ValueError('harness_frozen_native_proof_import_forbidden')
                    fid=research_fact_instance_id(evidence_id=eid,payload_hash=fact.payload_hash)
                    facts.append(fact.model_copy(update={'evidence_id':eid,'fact_id':fid}));fr[fid]=refs[original['fact_id']]
            if not candidates:
                raise ResearchCapabilityError('harness_frozen_slot_unavailable','固定材料中没有此查询的来源或指标；本次对照不会改用实时来源。',origin='gateway',cause_code='frozen_ledger_slot_missing',capability_id=query.capability_id,tool_call_id=query.request_id)
            now=self.clock()
            if now>bundle.request.deadline_at:raise ValueError('harness_frozen_execution_deadline_elapsed')
            # completed_at measures this new local read. Source observed/
            # received times remain original; persistence uses original PIT.
            result=ResearchCapabilityResult(schema_version='research-capability-result.v1',request_id=query.request_id,
                capability_id=query.capability_id,provider='frozen-run-ledger',evidence_candidates=candidates,
                facts=facts,cost_usd=0.0,completed_at=now,provider_attempts=[])
            # Completion is this real local read, not a historical source time.
            # Validate its budget/shape against the trusted execution deadline,
            # then retain the original query PIT for every evidence/fact check.
            validation_query=query.model_copy(update={'cutoff_at':bundle.request.deadline_at})
            ResearchCapabilityGatewayService._validate_result(validation_query,result,effective_domains)
            candidates_by_id={item.evidence_id:item for item in candidates}
            for candidate in candidates:_validate_candidate(query,candidate,effective_domains)
            for fact in facts:_validate_fact(query,fact,candidates_by_id)
            return FrozenSourceReturn(result,case.pit_cutoff_at,er,fr)

    def persist_facts(self,session,*,run_id,capability_id,facts,cutoff_at,lineage_reuse):
        """Reuse only the frozen versions; never derive a new world fact.

        Ordinary FactStore derives OI deltas using new instance IDs. This
        explicit fixed-world lane instead retains original derived slots and
        delegates PIT, immutable record and version checks to the same helpers.
        """
        from packages.kernel.decision_hub_kernel.application.fact_store import _validate_fact_pit,_same_semantic_fact,_fact_record
        from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
        if set(lineage_reuse)!=set(f.fact_id for f in facts):raise ValueError('harness_frozen_fact_version_missing')
        accepted=[]
        for fact in facts:
            evidence=session.get(ResearchEvidenceRecord,fact.evidence_id)
            if evidence is None or evidence.run_id!=run_id or evidence.requirement_id!=fact.requirement_id or evidence.source_id!=fact.source_id:
                raise ValueError('research_fact_lineage_mismatch')
            _validate_fact_pit(fact,cutoff_at)
            normalized=fact.model_copy(update={'quality':'accepted' if evidence.quality=='accepted' else evidence.quality})
            existing=session.get(ResearchFactRecord,fact.fact_id,populate_existing=True)
            if existing is None:session.add(_fact_record(run_id,capability_id,normalized));accepted.append(normalized)
            else:
                persisted=_fact_model(existing)
                if not _same_semantic_fact(persisted,normalized):raise ValueError('research_fact_identity_conflict')
                accepted.append(persisted)
        session.flush()
        FactLineageStore(self.service.database).bind_facts(session,facts=[session.get(ResearchFactRecord,f.fact_id) for f in accepted],cutoff_at=cutoff_at,reused=lineage_reuse)
        return accepted
