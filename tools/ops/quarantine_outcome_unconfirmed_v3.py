"""Explicit local maintenance; default prepares no mutation or model work.

The owner is the existing pinned local server claim, not a new login principal.
Only an exact original pre-claim unknown opportunity may become unconfirmed.
"""
from __future__ import annotations

import argparse,hashlib,json,os,re,subprocess,sys,traceback
from datetime import UTC,datetime
from pathlib import Path

CLI_REF='tools/ops/quarantine_outcome_unconfirmed_v3.py'
APP_REF='packages/kernel/decision_hub_kernel/application/history_outcome_reconciliation_v3.py'
CODEC_REF='packages/kernel/decision_hub_kernel/application/product_release_provenance.py'
BASELINE_SHA='5109d7cb67c863f1e5b150d98864522ed6ab944a9230d2cdc92395d744a96dda'
CLI_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
FILE_LIMIT=2*1024*1024

def require(value,code):
    if not value:raise ValueError(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def unique(pairs):
    result={}
    for key,value in pairs:require(key not in result,'history_outcome_quarantine_cli_duplicate_key');result[key]=value
    return result
def no_links(path,absent=False):
    path=Path(path).absolute();require('..' not in path.parts,'history_outcome_quarantine_cli_parent_path')
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current/=part
        try:stat=current.lstat()
        except FileNotFoundError:
            if absent:return
            raise
        require(not current.is_symlink() and not getattr(stat,'st_file_attributes',0)&0x400 and (not current.is_file() or stat.st_nlink==1),'history_outcome_quarantine_cli_link')
def read(path,maximum,expected=None):
    no_links(path);require(path.is_file() and path.stat().st_size<=maximum,'history_outcome_quarantine_cli_file_bound')
    raw=path.read_bytes();require(len(raw)<=maximum and (expected is None or sha(raw)==expected),'history_outcome_quarantine_cli_file_hash');return raw
def relative(value):
    require(type(value) is str and value and not Path(value).is_absolute() and '..' not in Path(value).parts,'history_outcome_quarantine_cli_relative_required');return value
def json_file(path,bound,expected=None):return json.loads(read(path,bound,expected),object_pairs_hook=unique)
def stopped_pointer50(root,state,baseline,raw,expected_hash):
    """The real pointer remains50 until the normal first51 START publishes it.

    This does not publish a pointer or confer a51 installed runtime identity.
    The51 source authority comes from its explicitly validated build/projection.
    """
    require(len(raw)<=8192 and sha(raw)==expected_hash,'history_outcome_quarantine_cli_stopped_pointer_hash')
    ref={'path':'data/product-local/installed-release-receipt.json','bytes':len(raw),'sha256':expected_hash}
    require(state.get('schema_version')=='local-product-launch51.v1' and state.get('release')=='51' and state.get('installed_pointer_before_first_launch50')==ref and state.get('original_build50')==baseline.get('build'),'history_outcome_quarantine_cli_saved_original50_pointer_required')
    build=baseline['build']
    require(type(build) is dict and set(build)=={'path','bytes','sha256'} and type(build['bytes']) is int and build['bytes']>0 and re.fullmatch('[a-f0-9]{64}',build['sha256']),'history_outcome_quarantine_cli_original50_build_reference')
    relative(build['path'])
    pointer=json.loads(raw,object_pairs_hook=unique)
    require(pointer=={'schema_version':'product-installed-release-pointer.v1','repository_root':str(root),'receipt_path':build['path'],'receipt_sha256':build['sha256']},'history_outcome_quarantine_cli_exact_original50_pointer_required')
    prior=read(root/build['path'],FILE_LIMIT,build['sha256']);require(len(prior)==build['bytes'],'history_outcome_quarantine_cli_original50_receipt_bytes')
    return pointer

def stopped_guard_v2(root,maintenance,output,receipt,expected_hash,installed):
    """Preserve the original stopped-lifetime gates; select its official V2 verifier."""
    refs=('tools/acceptance/upgrade_market_risk_schema.py',
          'infra/dsh/product-processes.ps1',
          'infra/dsh/validated-product-builds-v2.ps1',CODEC_REF)
    require(Path(maintenance.__file__).absolute()==root/refs[0],'history_outcome_quarantine_cli_stop_guard_origin')
    pinned={}
    for ref in refs:
        digest=installed.get(ref)
        require(type(digest) is str and re.fullmatch('[a-f0-9]{64}',digest),'history_outcome_quarantine_cli_stop_guard_source_required')
        pinned[ref]=read(root/ref,FILE_LIMIT,digest)
    read(receipt,FILE_LIMIT,expected_hash)
    original=maintenance.STOPPED_GUARD
    replacements=((". (Join-Path $RepositoryRoot 'infra/dsh/validated-product-builds.ps1')",
                   ". (Join-Path $RepositoryRoot 'infra/dsh/validated-product-builds-v2.ps1')"),
                  ('$validation=Test-ValidatedProductBuilds -ReceiptPath $ReceiptPath -RepositoryRoot $RepositoryRoot',
                   '$validation=Test-ValidatedProductBuildsV2 -ReceiptPath $ReceiptPath -RepositoryRoot $RepositoryRoot'))
    require(type(original) is str,'history_outcome_quarantine_cli_stop_guard_template_required')
    bridge=original
    for before,after in replacements:
        require(bridge.count(before)==1,'history_outcome_quarantine_cli_stop_guard_template_changed')
        bridge=bridge.replace(before,after,1)
    script=output/'standard-stopped-build-v2-guard.ps1';no_links(script,True)
    if not script.exists():
        with script.open('x',encoding='utf-8',newline='') as stream:stream.write(bridge)
    require(read(script,FILE_LIMIT).decode('utf-8')==bridge,'history_outcome_quarantine_cli_stop_guard_bridge_changed')
    run=subprocess.run(['pwsh','-NoProfile','-NonInteractive','-File',str(script),
        '-RepositoryRoot',str(root),'-ReceiptPath',str(receipt)],
        cwd=root,capture_output=True,text=True,timeout=120)
    raw=(run.stdout+run.stderr).encode('utf-8')
    require(len(raw)<=FILE_LIMIT,'history_outcome_quarantine_cli_stop_guard_log_bound')
    log=output/('guard-v2-'+str(datetime.now(UTC).timestamp())+'.private.log');no_links(log,True)
    with log.open('xb') as stream:stream.write(raw)
    require(run.returncode==0,'history_outcome_quarantine_cli_original_stopped_guard_failed')
    result=json.loads(run.stdout,object_pairs_hook=unique)
    validation=result.get('validation',{})
    require(result.get('passed') is True and validation.get('receipt_schema')=='product-validated-builds.v2'
        and validation.get('receipt_sha256')==expected_hash
        and validation.get('verifier_source_sha256')==installed[CODEC_REF],
        'history_outcome_quarantine_cli_original_stopped_guard_unverified')
    read(receipt,FILE_LIMIT,expected_hash)
    for ref,raw in pinned.items():require(read(root/ref,FILE_LIMIT,installed[ref])==raw,'history_outcome_quarantine_cli_stop_guard_changed')
    return result
def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository-root','database-path','data-directory','receipt-path','launch-state-path','source-projection-path','baseline-state-path','output-directory'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('receipt-sha256','installed-pointer-sha256','launch-state-sha256','source-projection-sha256','expected-native-hash','expected-fence-hash','launch-id','protocol-id','opportunity-id'):p.add_argument('--'+name,required=True)
    mode=p.add_mutually_exclusive_group();mode.add_argument('--inspect',action='store_true');mode.add_argument('--apply',action='store_true')
    return p.parse_args(argv)
def identity(a):
    root=a.repository_root.absolute();require(root==Path(__file__).absolute().parents[2],'history_outcome_quarantine_cli_installed_origin_required')
    for value in (a.receipt_sha256,a.installed_pointer_sha256,a.launch_state_sha256,a.source_projection_sha256,a.expected_native_hash,a.expected_fence_hash):require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value),'history_outcome_quarantine_cli_explicit_hash_required')
    require(re.fullmatch('[0-9]{8}T[0-9]{9}Z',a.launch_id),'history_outcome_quarantine_cli_explicit_launch_required')
    for path in (a.receipt_path,a.launch_state_path,a.source_projection_path,a.baseline_state_path):require(path.is_absolute() and path.absolute().is_relative_to(root/'artifacts'),'history_outcome_quarantine_cli_owned_identity_required')
    raw=read(a.receipt_path,FILE_LIMIT,a.receipt_sha256);receipt=json.loads(raw,object_pairs_hook=unique)
    state=json_file(a.launch_state_path,FILE_LIMIT,a.launch_state_sha256);baseline=json_file(a.baseline_state_path,FILE_LIMIT,BASELINE_SHA)
    pointer_raw=read(root/'data/product-local/installed-release-receipt.json',8192,a.installed_pointer_sha256)
    stopped_pointer50(root,state,baseline,pointer_raw,a.installed_pointer_sha256)
    release=state.get('release');require(type(release) is str and release.isdecimal() and state.get('schema_version')=='local-product-launch'+release+'.v1' and state.get('repository_root')==str(root) and state.get('build')=={'path':a.receipt_path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':a.receipt_sha256},'history_outcome_quarantine_cli_current_state_required')
    require(baseline.get('schema_version')=='local-product-launch50.v1' and baseline.get('repository_root')==str(root),'history_outcome_quarantine_cli_original50_required')
    for key in ('sources','configs','standard','protocol_id','protocol_acceptance_id'):require(state.get(key)==baseline.get(key),'history_outcome_quarantine_cli_original_grants_or_guards_changed')
    guards=state['sources'];require(type(guards) is list and len(guards)==65 and len({item['path'] for item in guards})==65,'history_outcome_quarantine_cli_original65_required')
    for item in guards+[state['standard']]+list(state['configs'].values()):
        require(type(item) is dict and set(item)=={'path','bytes','sha256'} and type(item['bytes']) is int,'history_outcome_quarantine_cli_reference_shape')
        data=read(root/relative(item['path']),FILE_LIMIT,item['sha256']);require(len(data)==item['bytes'],'history_outcome_quarantine_cli_reference_bytes')
    sources=receipt.get('sources');require(type(sources) is dict and sources.get(CLI_REF)==CLI_SHA,'history_outcome_quarantine_cli_source_not_installed')
    codec_hash=sources.get(CODEC_REF);require(type(codec_hash) is str and re.fullmatch('[a-f0-9]{64}',codec_hash),'history_outcome_quarantine_cli_normal_codec_required')
    read(root/CODEC_REF,FILE_LIMIT,codec_hash)
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    from packages.kernel.decision_hub_kernel.application import product_release_provenance as codec
    require(Path(codec.__file__).absolute()==root/CODEC_REF,'history_outcome_quarantine_cli_codec_origin')
    resolved=codec.resolve_product_receipt(raw,expected_root=root,read_resource=lambda ref:read(root/relative(ref),1024*1024),expected_sha256=a.receipt_sha256)
    maps={'raw_sources':sources,'resolved_sources':dict(resolved.value['sources']),'transport':dict(resolved.transport)}
    projected=json_file(a.source_projection_path,16*1024*1024,a.source_projection_sha256)
    require(projected.get('repository_root')==str(root) and projected.get('build')=={'path':a.receipt_path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':a.receipt_sha256},'history_outcome_quarantine_cli_projection_binding')
    for key,value in maps.items():require(projected.get(key)==value and projected.get('summary',{}).get(key)=={'count':len(value),'sha256':sha(codec.canonical(value))},'history_outcome_quarantine_cli_actual_projection_required')
    from packages.kernel.decision_hub_kernel.application import history_outcome_reconciliation_v3 as module
    from packages.kernel.decision_hub_kernel.persistence import callback_source_lease as lease
    for ref,loaded,digest in ((CLI_REF,Path(__file__),CLI_SHA),(APP_REF,Path(module.__file__),module.LOADED_SOURCE_SHA256),(module.LEASE_SOURCE_REF,Path(lease.__file__),module.LOADED_LEASE_SOURCE_SHA256)):
        require(loaded.absolute()==root/ref and maps['resolved_sources'].get(ref)==digest,'history_outcome_quarantine_cli_loaded_source_authority');read(root/ref,FILE_LIMIT,digest)
    supervisor=json_file(root/'data/product-local/supervisor.json',8192)
    require(supervisor.get('launch_id')==a.launch_id and supervisor.get('state')=='stopped','history_outcome_quarantine_cli_standard_stopped_required')
    require(a.protocol_id==state['protocol_id'],'history_outcome_quarantine_cli_original_protocol_only')
    require(a.database_path.absolute()==a.data_directory.absolute()/'db/decision_hub.sqlite3' and a.data_directory.absolute()==root/'data/core-live-20260913','history_outcome_quarantine_cli_existing_local_database_only')
    no_links(a.database_path);require(a.database_path.is_file(),'history_outcome_quarantine_cli_existing_database_required')
    return root,state,{'receipt_sha256':a.receipt_sha256,'installed_pointer_sha256':a.installed_pointer_sha256,'launch_state_sha256':a.launch_state_sha256,'source_projection_sha256':a.source_projection_sha256,'launch_id':a.launch_id,'source_guard_count':65,'new_grants':0}
def execute(a):
    root,state,binding=identity(a)
    if not a.apply and not a.inspect:return {'outcome':'prepared_only','DB_writes':0,'model_execution':0,'automatic_retry':False,'replay_authorized':False,'identity':binding}
    output=a.output_directory.absolute();require(output.is_relative_to(root/'artifacts') and not output.exists(),'history_outcome_quarantine_cli_fresh_private_output_required');no_links(output,True);output.mkdir(parents=True,exist_ok=False)
    names={};phase='normal_factory';failure=None;result=None
    try:
        # Reuse the existing standard stopped-build guard. No service starts,
        # migration, registration, DB initialize, or change of source guards.
        from tools.acceptance import upgrade_market_risk_schema as maintenance
        guard_ref='tools/acceptance/upgrade_market_risk_schema.py'
        installed=json_file(a.source_projection_path,16*1024*1024,a.source_projection_sha256)['resolved_sources']
        require(Path(maintenance.__file__).absolute()==root/guard_ref and sha(read(root/guard_ref,FILE_LIMIT))==installed.get(guard_ref),'history_outcome_quarantine_cli_original_stop_guard_source')
        stopped_guard_v2(root,maintenance,output,a.receipt_path,a.receipt_sha256,installed)
        names['DECISION_HUB_DATA_DIR']=os.environ.get('DECISION_HUB_DATA_DIR');os.environ['DECISION_HUB_DATA_DIR']=str(a.data_directory)
        for kind,name in [('history','DECISION_HUB_HISTORY_PRIVATE_CONFIG'),('qualification','DECISION_HUB_HISTORY_QUALIFICATION_PRIVATE_CONFIG'),('outcome','DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG')]:
            names[name]=os.environ.get(name);names[name+'_SHA256']=os.environ.get(name+'_SHA256');os.environ[name]=str(root/state['configs'][kind]['path']);os.environ[name+'_SHA256']=state['configs'][kind]['sha256']
        from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand,_service
        from apps.hub_api.history_outcome_bootstrap import _pinned_body
        from apps.hub_api.history_product_bootstrap41 import configured_history_product41
        from apps.hub_api.history_outcome_bootstrap41 import configured_history_outcome41
        from packages.kernel.decision_hub_kernel.application.history_outcome_reconciliation_v3 import HistoryOutcomeReconciliationV3
        pinned=_pinned_body();require(pinned is not None,'history_outcome_quarantine_cli_pinned_owner_required')
        command=HistoryObservedCliCommand('status',a.database_path,a.data_directory,pinned[3].owner_identity,'outcome_quarantine_explicit')
        with _service(command) as commands:
            history=configured_history_product41(commands.database);require(history is not None,'history_outcome_quarantine_cli_history_factory_required')
            boot=configured_history_outcome41(commands.database,history41=history);require(boot is not None and boot.protocol_ids==(a.protocol_id,),'history_outcome_quarantine_cli_original_grant_required')
            service=boot.service
            with service.commands._read_session() as session:require(session.connection().exec_driver_sql('SELECT version_num FROM alembic_version').all()==[('0091_research_publication_bundles',)],'history_outcome_quarantine_cli_schema0091_required')
            issuer=HistoryOutcomeReconciliationV3(service,protocol_ids=boot.protocol_ids);phase='prepare_exact_preclaim'
            with issuer.prepare_quarantine(a.protocol_id,a.opportunity_id,expected_native_hash=a.expected_native_hash,expected_fence_hash=a.expected_fence_hash) as packet:
                # Full normal installed identity is checked again before writer.
                require(identity(a)[2]==binding,'history_outcome_quarantine_cli_identity_changed')
                stopped_guard_v2(root,maintenance,output,a.receipt_path,a.receipt_sha256,installed)
                if a.apply:phase='apply_exact_CAS';result=issuer.quarantine_unconfirmed(packet)
                else:result={'operation':'inspect','state':'pending','eligible_exact_preclaim':True,'material_sha256':packet.material_hash,'original_native_hash':packet.native_hash,'original_fence_hash':packet.fence_hash,'committed':False}
        require(identity(a)[2]==binding,'history_outcome_quarantine_cli_identity_changed_at_exit')
        stopped_guard_v2(root,maintenance,output,a.receipt_path,a.receipt_sha256,installed)
    except BaseException as error:
        code=str(error);failure={'phase':phase,'exception_type':type(error).__module__+'.'+type(error).__qualname__,'message':code,'traceback':traceback.format_exc(),'maintenance_phase':getattr(error,'phase',None)}
    finally:
        for name,value in names.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
    private={'schema_version':'history-outcome-quarantine-operator.v3','identity':binding,'protocol_id':a.protocol_id,'opportunity_id':a.opportunity_id,'apply':a.apply,'result':result,'failure':failure,'model_execution':0,'automatic_retry':False,'replay_authorized':False,'owner_authenticated':False,'authority_scope':'local_process_with_pinned_server_configuration'}
    raw=json.dumps(private,ensure_ascii=False,sort_keys=True,indent=2).encode();require(len(raw)<=FILE_LIMIT,'history_outcome_quarantine_cli_evidence_bound');(output/'result.private.json').write_bytes(raw)
    summary={'outcome':'unconfirmed' if failure else 'quarantined' if a.apply else 'eligible_read_only','operation':'quarantine_unconfirmed','state':result.get('state') if result else None,'committed':result.get('committed') if result and not failure else None,'phase':failure['phase'] if failure else 'independent_readback_complete' if a.apply else 'inspect_complete','error_code':None,'private_sha256':sha(raw),'private_bytes':len(raw),'model_execution':0,'automatic_retry':False,'replay_authorized':False,'financial_qualified':False}
    if failure:summary['error_code']=failure['message'] if re.fullmatch('history_outcome_quarantine_[a-z0-9_]{1,120}',failure['message']) else failure['exception_type']
    (output/'receipt.json').write_text(json.dumps(summary,sort_keys=True,indent=2),encoding='utf-8');return summary
def main(argv=None):
    a=parse(argv)
    try:result=execute(a)
    except BaseException as error:
        code=str(error);result={'outcome':'refused','automatic_retry':False,'error_code':code if re.fullmatch('history_outcome_quarantine_[a-z0-9_]{1,120}',code) else type(error).__name__,'model_execution':0,'replay_authorized':False}
    print(json.dumps(result,sort_keys=True));return 1 if result['outcome'] in {'unconfirmed','refused'} else 0
if __name__=='__main__':raise SystemExit(main())
