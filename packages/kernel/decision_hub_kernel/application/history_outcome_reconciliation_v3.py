"""Explicit quarantine of one authenticated pre-claim unknown opportunity.

This preserves its original fence, receipt, counters, lease and error. It never
publishes, retries, expires, backfills or restores an old issued job claim.
"""
from __future__ import annotations

import base64,hashlib,json,os,re,secrets,threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from ..persistence.callback_source_lease import _WindowsFiles
from ..persistence import callback_source_lease as _source_lease_module
from ..persistence.history_outcome_v2_records import HistoryOutcomeOpportunityV2Record as Opportunity,HistoryOutcomeProtocolV2Record as Protocol,HistoryOutcomePublicationV2Record as Publication,HistoryOutcomeObservationV2Record as Observation
from ..ports.market_path import canonical_hash
from .history_forecast_transfer39 import native_equal39
from .history_observed_dataset import encoded
from .history_outcome_jobs_v2 import HistoryOutcomeJobsV2
from .history_outcome_v2 import HistoryOutcomeV2Service,_native

SOURCE_REF='packages/kernel/decision_hub_kernel/application/history_outcome_reconciliation_v3.py'
LOADED_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LEASE_SOURCE_REF='packages/kernel/decision_hub_kernel/persistence/callback_source_lease.py'
LOADED_LEASE_SOURCE_SHA256=hashlib.sha256(Path(_source_lease_module.__file__).read_bytes()).hexdigest()
FILE_LIMIT=2*1024*1024
RESOURCE_LIMIT=32*1024*1024
MATERIAL_LIMIT=8*1024*1024
INTENT_LIMIT=32768
SHA=re.compile('[a-f0-9]{64}')

def _require(value,code):
    if not value:raise ValueError(code)
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            _require(key not in result,'history_outcome_quarantine_duplicate_key');result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)
def _cell(value):
    if value is None:return ['null',None]
    if type(value) is int:return ['integer',value]
    if type(value) is str:return ['text',value]
    if type(value) is float:return ['real',value.hex()]
    if type(value) is bytes:return ['blob',base64.b64encode(value).decode()]
    raise ValueError('history_outcome_quarantine_native_type')
def _no_links(path,absent=False):
    path=Path(path).absolute();_require('..' not in path.parts,'history_outcome_quarantine_parent_path')
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current/=part
        try:stat=current.lstat()
        except FileNotFoundError:
            if absent:return
            raise
        _require(not current.is_symlink() and not getattr(stat,'st_file_attributes',0)&0x400 and (not current.is_file() or stat.st_nlink==1),'history_outcome_quarantine_link')

class OutcomeQuarantineCommitUnconfirmed(RuntimeError):
    automatic_retry=False
    def __init__(self,phase):
        self.phase=phase;super().__init__('history_outcome_quarantine_commit_unconfirmed')

class _ResourceLease:
    """One call, full bytes outside writer; actual Windows identities inside."""
    def __init__(self,root,check):
        _require(os.name=='nt','history_outcome_quarantine_windows_runtime_required')
        self.root=Path(root).absolute();self.native=_WindowsFiles();self.check=check;self.owner=threading.get_ident();self.closed=False
        self.files={};self.directories={};self.absent=set();self.total=0
    def pin(self,path,expected=None,maximum=FILE_LIMIT,absent=False):
        self.check();path=Path(path).absolute();_require(path.is_relative_to(self.root),'history_outcome_quarantine_resource_root')
        _no_links(path,absent)
        for directory in reversed([path.parent,*path.parent.parents]):
            if directory in self.directories:continue
            if not directory.exists():
                _require(absent,'history_outcome_quarantine_resource_missing');self.absent.add(directory);continue
            handle=self.native.open(directory,directory=True)
            try:
                identity=self.native.identity(handle)
                _require(identity.final_path==self.native.canonical(str(directory)),'history_outcome_quarantine_directory_identity')
            except BaseException:self.native.close(handle);raise
            self.directories[directory]=(handle,identity)
        if not path.exists():
            _require(absent,'history_outcome_quarantine_resource_missing');self.absent.add(path);return None
        if path in self.files:
            raw=self.files[path][3];_require(expected is None or _sha(raw)==expected,'history_outcome_quarantine_resource_conflict');return raw
        _require(path.stat().st_size<=maximum,'history_outcome_quarantine_resource_bound')
        handle=self.native.open(path)
        try:
            identity=self.native.identity(handle)
            digest,size=self.native.digest(handle,self.check)
            _require(size<=maximum and (expected is None or digest==expected),'history_outcome_quarantine_resource_hash')
            raw=path.read_bytes();_require(len(raw)==size and _sha(raw)==digest,'history_outcome_quarantine_resource_readback')
            _require(self.total+size<=RESOURCE_LIMIT,'history_outcome_quarantine_resource_total')
        except BaseException:self.native.close(handle);raise
        self.total+=size
        self.files[path]=(handle,identity,digest,raw);return raw
    def verify(self):
        self.check();_require(not self.closed and threading.get_ident()==self.owner,'history_outcome_quarantine_resource_scope')
        for path,(handle,identity) in self.directories.items():
            _no_links(path);fresh=self.native.open(path,directory=True)
            try:_require(self.native.identity(handle)==identity==self.native.identity(fresh),'history_outcome_quarantine_directory_changed')
            finally:self.native.close(fresh)
        for path,(handle,identity,_digest,_raw) in self.files.items():
            _no_links(path);fresh=self.native.open(path)
            try:_require(self.native.identity(handle)==identity==self.native.identity(fresh),'history_outcome_quarantine_file_changed')
            finally:self.native.close(fresh)
        for path in self.absent:_no_links(path,True);_require(not path.exists(),'history_outcome_quarantine_absence_changed')
    def close(self):
        if self.closed:return
        self.closed=True;first=None
        for handle in [item[0] for item in self.files.values()]+[item[0] for item in self.directories.values()]:
            try:self.native.close(handle)
            except BaseException as error:
                if first is None:first=error
        if first is not None:raise first

@dataclass(frozen=True,slots=True)
class OutcomeQuarantinePreparedV3:
    issuer:object
    thread_id:int
    protocol_id:str
    opportunity_id:str
    native_hash:str
    fence_hash:str
    material_hash:str
    nonce:str

class HistoryOutcomeReconciliationV3:
    """Internal maintenance on the existing locally pinned server authority."""
    def __init__(self,service,*,protocol_ids):
        _require(type(service) is HistoryOutcomeV2Service and type(protocol_ids) is tuple and len(protocol_ids)==len(set(protocol_ids)),'history_outcome_quarantine_actual_service')
        self.service=service;self.grants=protocol_ids;self.jobs=HistoryOutcomeJobsV2(service)
        self.root=service.originals.source_root.absolute();self.archive=service.archive.root.absolute()
        _require(self.archive.is_relative_to(self.root),'history_outcome_quarantine_owned_archive')
        self.identity=(service,service.commands,service.owner_claim,service.archive,self.root,self.archive,protocol_ids)
        self._issued={}
    def _authority(self):
        _require(all(getattr(cls,name) is method and method.__code__ is code for cls,name,method,code in LOADED_METHODS),'history_outcome_quarantine_loaded_callable_changed')
        _require(self.identity==(self.service,self.service.commands,self.service.owner_claim,self.service.archive,self.service.originals.source_root.absolute(),self.service.archive.root.absolute(),self.grants),'history_outcome_quarantine_authority_changed')
        _require(_sha((self.root/SOURCE_REF).read_bytes())==LOADED_SOURCE_SHA256,'history_outcome_quarantine_loaded_source_changed')
        _require(_sha((self.root/LEASE_SOURCE_REF).read_bytes())==LOADED_LEASE_SOURCE_SHA256,'history_outcome_quarantine_lease_source_changed')
        return self.jobs._sources()
    @staticmethod
    def _row(session,record,key):
        table=record.__table__;columns=tuple(c.name for c in table.columns);pk=tuple(c.name for c in table.primary_key.columns)
        _require(len(pk)==1,'history_outcome_quarantine_primary_key')
        raw=session.connection().exec_driver_sql('SELECT '+','.join('"'+c+'"' for c in columns)+' FROM "'+table.name+'" WHERE "'+pk[0]+'"=?',(key,)).all()
        _require(len(raw)<=1,'history_outcome_quarantine_native_unique')
        return columns,None if not raw else tuple(raw[0])
    @staticmethod
    def _absence(session,identifier):
        for record in (Publication,Observation):
            rows=session.connection().exec_driver_sql('SELECT '+','.join('"'+c.name+'"' for c in record.__table__.columns)+' FROM "'+record.__tablename__+'" WHERE opportunity_id=? LIMIT 2',(identifier,)).all()
            _require(not rows,'history_outcome_quarantine_subject_present')
    def _paths(self,protocol,identifier):
        requests=['publication:'+identifier]
        for attempt in range(1,5):
            observed='hobservation2_'+canonical_hash([identifier,attempt])[:48]
            requests.extend(['observation:'+identifier+':'+str(attempt),'confirm:'+observed])
        return tuple(self.archive/'fences'/(canonical_hash([self.service.owner_claim,[request]])+'.json') for request in requests)+(self.archive/'runtime-score-fences'/(canonical_hash([self.service.owner_claim,protocol,'outcome40-final:'+protocol])+'.json'),)
    @contextmanager
    def prepare_quarantine(self,protocol_id,opportunity_id,*,expected_native_hash,expected_fence_hash,budget_seconds=120):
        _require(protocol_id in self.grants and re.fullmatch('heval2_[a-f0-9]{48}',protocol_id) and re.fullmatch('hopportunity2_[a-f0-9]{48}',opportunity_id),'history_outcome_quarantine_exact_granted_target')
        _require(type(budget_seconds) in (int,float) and 0<budget_seconds<=120 and SHA.fullmatch(expected_native_hash) and SHA.fullmatch(expected_fence_hash),'history_outcome_quarantine_bounded_expectations')
        deadline=monotonic()+budget_seconds
        def check():_require(monotonic()<deadline,'history_outcome_quarantine_deadline')
        lease=_ResourceLease(self.root,check);packet=None
        try:
            sources=self._authority()
            for path,digest in (*sources,(SOURCE_REF,LOADED_SOURCE_SHA256),(LEASE_SOURCE_REF,LOADED_LEASE_SOURCE_SHA256)):lease.pin(self.root/path,digest)
            with self.service.commands._read_session() as session:
                native=session.connection().connection.driver_connection
                _require(native.in_transaction and native.execute('PRAGMA query_only').fetchone()==(1,),'history_outcome_quarantine_physical_RO')
                initial=native.total_changes
                protocol,_,protocol_row=self.service._read(session,'protocol',protocol_id)
                _require(protocol_row.confirmation_id is not None,'history_outcome_quarantine_confirmed_protocol')
                _,_,ack_row=self.service._acceptance(session,protocol_row.confirmation_id,'publication')
                value,_,row=self.service._read(session,'opportunity',opportunity_id)
                _require(type(row.generation) is int and row.generation==0 and type(row.attempts) is int,'history_outcome_quarantine_native_header')
                _require(row.owner_claim==self.service.owner_claim and row.protocol_id==protocol_id and row.state=='pending' and row.attempts==0 and row.lease_owner is None and row.lease_expires_at is None and row.first_publication_id is None and row.first_acceptance_id is None and row.first_accepted_observation_id is None and row.label_cutoff_at is None and row.label_deadline_at is None,'history_outcome_quarantine_exact_preclaim_head')
                native_hash=canonical_hash(list(_native(row)));_require(native_hash==expected_native_hash,'history_outcome_quarantine_expected_native_changed')
                jobbody=lease.pin(self.jobs._fence(opportunity_id),expected_fence_hash)
                fence=_json(jobbody)
                _require(type(fence) is dict and set(fence)=={'schema_version','owner_claim','protocol_id','opportunity_id','operation','generation','attempt','lease_owner','lease_expires_at','source_files','skipped_attempts','opportunity_original_native_hash'} and encoded(fence)==jobbody,'history_outcome_quarantine_original_fence_format')
                _require(fence['schema_version']=='history-outcome-job-fence.v2' and (fence['owner_claim'],fence['protocol_id'],fence['opportunity_id'],fence['operation'])==(self.service.owner_claim,protocol_id,opportunity_id,'reconcile') and type(fence['generation']) is int and fence['generation']==row.generation+1 and type(fence['attempt']) is int and fence['attempt']==row.attempts and fence['opportunity_original_native_hash']==native_hash and fence['source_files']==[list(item) for item in sources] and fence['skipped_attempts']==[],'history_outcome_quarantine_original_fence_binding')
                _require(type(fence['lease_owner']) is str and re.fullmatch('outcome_job_[a-f0-9]{32}',fence['lease_owner']) and type(fence['lease_expires_at']) is str,'history_outcome_quarantine_original_fence_native_lease')
                receipt_path=self.archive/'job-receipts'/(canonical_hash([self.service.owner_claim,opportunity_id,fence['generation']])+'.json')
                _require(lease.pin(receipt_path)==jobbody,'history_outcome_quarantine_original_receipt_changed')
                self._absence(session,opportunity_id)
                for path in self._paths(protocol_id,opportunity_id):_require(lease.pin(path,absent=True) is None,'history_outcome_quarantine_subject_or_score_fence')
                dependencies=[]
                for record,key,original in ((Protocol,protocol_id,protocol_row),(Publication,protocol_row.confirmation_id,ack_row),(Opportunity,opportunity_id,row)):
                    columns,cells=self._row(session,record,key);_require(cells is not None and len(encoded([_cell(c) for c in cells]))<=FILE_LIMIT,'history_outcome_quarantine_complete_native_bound')
                    dependencies.append((record,key,columns,cells))
                    lease.pin(original.original_ref,original.original_hash,RESOURCE_LIMIT)
                    lease.pin(original.source_original_ref,original.source_original_hash,RESOURCE_LIMIT)
                _require(native.total_changes==initial and native.in_transaction,'history_outcome_quarantine_RO_exit')
            _require(self._authority()==sources,'history_outcome_quarantine_source_changed');lease.verify()
            key=canonical_hash([self.service.owner_claim,protocol_id,opportunity_id])
            directory=self.archive/'job-quarantines-v3'
            _no_links(directory,True)
            intent_path=directory/(key+'.intent.json');unknown_path=directory/(key+'.fence.json');confirmed_path=directory/(key+'.confirmed.json');material_path=directory/(key+'.material.json')
            for path in (intent_path,unknown_path,confirmed_path,material_path):_no_links(path,True);_require(not path.exists(),'history_outcome_quarantine_prior_maintenance_requires_RO')
            material=encoded({'schema_version':'history-outcome-quarantine-material.v3','owner_claim':self.service.owner_claim,'protocol_id':protocol_id,'opportunity_id':opportunity_id,
                'native_dependencies':[{'table':r.__tablename__,'key':k,'columns':list(cols),'cells':[_cell(c) for c in cells]} for r,k,cols,cells in dependencies],
                'job_fence_base64':base64.b64encode(jobbody).decode(),'job_receipt_base64':base64.b64encode(jobbody).decode(),'source_files':sources,'subject_ranges_empty':True,'subject_and_score_fences_absent':True,
                'resource_hashes':{path.relative_to(self.root).as_posix():item[2] for path,item in lease.files.items()},'absent_resources':[path.relative_to(self.root).as_posix() for path in sorted(lease.absent)]})
            _require(len(material)<=MATERIAL_LIMIT,'history_outcome_quarantine_material_bound')
            packet=OutcomeQuarantinePreparedV3(self,threading.get_ident(),protocol_id,opportunity_id,native_hash,expected_fence_hash,_sha(material),secrets.token_hex(32))
            self._issued[id(packet)]=(packet,lease,dependencies,sources,material,(intent_path,unknown_path,confirmed_path,material_path),check)
            yield packet
        finally:
            if packet is not None:self._issued.pop(id(packet),None)
            lease.close()
    def _auth(self,packet):
        _require(type(packet) is OutcomeQuarantinePreparedV3 and packet.issuer is self and packet.thread_id==threading.get_ident() and id(packet) in self._issued and self._issued[id(packet)][0] is packet,'history_outcome_quarantine_issued_packet_required')
        value=self._issued[id(packet)];value[-1]();value[1].verify();return value
    @staticmethod
    def _put(path,body,bound):
        _require(len(body)<=bound,'history_outcome_quarantine_append_bound');_no_links(path,True);path.parent.mkdir(parents=True,exist_ok=True);_no_links(path.parent)
        with path.open('xb') as handle:handle.write(body);handle.flush();os.fsync(handle.fileno())
    def quarantine_unconfirmed(self,packet):
        _,lease,dependencies,sources,material,paths,check=self._auth(packet)
        _require(self._authority()==sources,'history_outcome_quarantine_source_changed')
        intent_path,unknown_path,confirmed_path,material_path=paths
        intent=encoded({'schema_version':'history-outcome-quarantine-intent.v3','owner_claim':self.service.owner_claim,'protocol_id':packet.protocol_id,'opportunity_id':packet.opportunity_id,
            'action':'quarantine_unconfirmed','from_state':'pending','to_state':'unconfirmed','original_native_hash':packet.native_hash,'original_fence_hash':packet.fence_hash,'material_path':str(material_path),'material_sha256':packet.material_hash,
            'nonce':packet.nonce,'automatic_retry':False,'replay_authorized':False,'financial_qualified':False})
        self._put(material_path,material,MATERIAL_LIMIT);self._put(intent_path,intent,INTENT_LIMIT);self._put(unknown_path,intent,INTENT_LIMIT)
        phase='writer_entry'
        try:
            # Every file was fully authenticated outside the writer and remains
            # held by native no-write/delete handles; only identities inside.
            with self.service.commands._write_session() as session:
                check();self._auth(packet)
                for record,key,columns,expected in dependencies:
                    actual_columns,actual=self._row(session,record,key)
                    _require(columns==actual_columns and native_equal39(actual,expected),'history_outcome_quarantine_full_native_changed')
                self._absence(session,packet.opportunity_id);lease.verify()
                _,_,columns,cells=dependencies[-1]
                where=' AND '.join('"'+name+'" IS ?' for name in columns)
                result=session.connection().exec_driver_sql('UPDATE "'+Opportunity.__tablename__+'" SET state=? WHERE '+where,('unconfirmed',*cells))
                _require(result.rowcount==1,'history_outcome_quarantine_native_CAS_failed')
                self._absence(session,packet.opportunity_id);lease.verify();check();phase='commit_or_rollback'
            phase='independent_readback'
            with self.service.commands._read_session() as session:
                for record,key,columns,expected in dependencies:
                    actual_columns,actual=self._row(session,record,key)
                    if record is Opportunity:expected=tuple('unconfirmed' if column=='state' else cell for column,cell in zip(columns,expected,strict=True))
                    _require(columns==actual_columns and native_equal39(actual,expected),'history_outcome_quarantine_committed_readback_changed')
                self._absence(session,packet.opportunity_id);lease.verify();check()
                clock=self.service.now().isoformat()
            _require(self._authority()==sources,'history_outcome_quarantine_source_changed_at_exit')
            for path,body,bound in ((material_path,material,MATERIAL_LIMIT),(intent_path,intent,INTENT_LIMIT),(unknown_path,intent,INTENT_LIMIT)):
                _no_links(path);_require(path.stat().st_size<=bound and path.read_bytes()==body,'history_outcome_quarantine_maintenance_evidence_changed')
            receipt=encoded({'schema_version':'history-outcome-quarantine-confirmed.v3','intent_sha256':_sha(intent),'material_sha256':packet.material_hash,'independently_verified_at':clock,
                'owner_claim':self.service.owner_claim,'protocol_id':packet.protocol_id,'opportunity_id':packet.opportunity_id,'state':'unconfirmed','quarantine_reason':'original_preclaim_commit_unconfirmed',
                'original_fence_preserved':True,'original_receipt_preserved':True,'counters_lease_error_preserved':True,'automatic_retry':False,'replay_authorized':False,'financial_qualified':False})
            self._put(confirmed_path,receipt,INTENT_LIMIT)
            _no_links(unknown_path);_require(unknown_path.stat().st_size<=INTENT_LIMIT and unknown_path.read_bytes()==intent,'history_outcome_quarantine_maintenance_fence_changed');unknown_path.unlink()
            return {'operation':'quarantine_unconfirmed','state':'unconfirmed','committed':True,'confirmation_sha256':_sha(receipt),'material_sha256':packet.material_hash,'original_native_hash':packet.native_hash,
                'original_fence_preserved':True,'original_receipt_preserved':True,'automatic_retry':False,'replay_authorized':False,'financial_qualified':False}
        except BaseException as error:
            # Original unknown fences plus this new intent/fence remain. A saved
            # row alone cannot authorize replay or a second maintenance attempt.
            raise OutcomeQuarantineCommitUnconfirmed(phase) from error

LOADED_METHODS=tuple((cls,name,method,method.__code__) for cls in (HistoryOutcomeReconciliationV3,_ResourceLease,_WindowsFiles) for name in cls.__dict__ for method in (getattr(cls,name),) if callable(method) and hasattr(method,'__code__'))
