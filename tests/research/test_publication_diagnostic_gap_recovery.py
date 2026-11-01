"""Pure recovery controls; no SQLite, HTTP, provider, or product run.

The writer/authority seams below test orchestration, not a real COMMIT or new
Saved/ACK qualification. Separate controls exercise the complete archive reader
against bounded local fixture files and fake native rows.
"""
from __future__ import annotations

import ast
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import research_publication_jobs48 as jobs
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import (
    BundleArchive, BundleClaim, BundleCommitUnconfirmed, encoded, sha,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from test_confirmed_report_snapshot import ReadSession, snapshot

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
DEV = Path(__file__).resolve().parents[2]
BEFORE = DEV / "artifacts/core-goal-20261011/diagnostic-gap-recovery01/before/packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py"


def event(version, generation, state, phase, diagnostic=None):
    body = {"schema_version":"publication-bundle-state-event.v2", "run_id":"gap-run",
        "request_id":"gap-request", "version":version, "generation":generation,
        "state":state, "reason_code":None, "bundle_hash":None,
        "occurred_at":NOW.isoformat(), "transition_phase":phase}
    if diagnostic is not None:
        body["failure_diagnostic"] = diagnostic
    raw = encoded(body)
    return SimpleNamespace(**{name:body[name] for name in (
        "run_id", "request_id", "version", "generation", "state", "reason_code", "bundle_hash")},
        event_id=sha(raw), payload_json=raw.decode(), occurred_at=NOW)


class FakeSession:
    def __init__(self, control, *, writer=False):
        self.control, self.writer = control, writer

    def get(self, kind, key, **_options):
        assert kind is jobs.Job and key == "gap-run"
        return self.control.job

    def scalars(self, query):
        if len(list(query.selected_columns)) == 1:
            return [row.event_id for row in self.control.rows[:self.control.old_version]]
        return self.control.rows

    def connection(self):
        return self

    def exec_driver_sql(self, statement, parameters):
        if "research_publication_bundle_state_events" in statement:
            assert parameters == ("gap-run",self.control.old_version)
            columns=list(jobs.Event.__table__.columns)
            def native(row,column):
                value=getattr(row,column.name)
                return value.isoformat() if isinstance(value,datetime) else value
            return SimpleNamespace(keys=lambda:[column.name for column in columns],
                fetchall=lambda:[tuple(native(row,column) for column in columns)
                                 for row in self.control.rows[:self.control.old_version]])
        assert statement.startswith("SELECT * FROM research_publication_bundle")
        assert parameters == ("gap-run",)
        return SimpleNamespace(fetchall=lambda: self.control.target_saved)

    def execute(self, statement):
        assert self.writer
        sql = str(statement)
        for name in ("run_id", "request_id", "generation", "state_version", "state",
                     "owner IS NULL", "lease_expires_at IS NULL", "bundle_hash IS NULL", "confirmed_at IS NULL"):
            assert name in sql
        if self.control.cas_lost:
            return SimpleNamespace(rowcount=0)
        self.control.job.generation += 1
        self.control.job.state = "queued"
        return SimpleNamespace(rowcount=1)


@pytest.fixture
def control(tmp_path):
    life = object.__new__(jobs.DurablePublicationLifecycle)
    life.archive = BundleArchive(tmp_path / "archive", max_bundle_bytes=16*1024*1024,
                                 max_material_bytes=192*1024*1024)
    life.clock = lambda: NOW
    state = SimpleNamespace(life=life, job=SimpleNamespace(run_id="gap-run", request_id="gap-request",
        generation=1, state_version=2, state="unconfirmed", owner=None, lease_expires_at=None,
        bundle_hash=None, confirmed_at=None), rows=[event(1,1,"queued","claimed"),
        event(2,1,"unconfirmed","unconfirmed",{"status":"unavailable","sha256":None})],
        old_version=2, proof_calls=[], native_calls=[], source_calls=0, attempts=0,
        target_saved=[], cas_lost=False, writer_change=False, readback_change=False,
        source_change_at=None, unknown_commit=False, proofs=[{"inventory":[],"owners":[]}]*2,
        post_write=None, native_changed=False)
    active = [False]

    @contextmanager
    def ro():
        yield FakeSession(state)

    @contextmanager
    def authority(_session, *, run_id):
        assert run_id == "gap-run" and not active[0]
        active[0] = True
        try:
            yield object()
        finally:
            active[0] = False

    def native(session, **_options):
        assert active[0]
        state.native_calls.append(session)
        if state.native_changed:
            raise ValueError("publication_retry_native_changed")

    def sources(_permit):
        assert active[0]
        state.source_calls += 1
        if state.source_calls == state.source_change_at:
            raise ValueError("publication_retry_source_changed")

    def proof(session, run_id):
        assert run_id == "gap-run" and not session.writer
        index = len(state.proof_calls)
        state.proof_calls.append(session)
        value = state.proofs[index]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def unchanged(session, value):
        assert session.writer and value == state.proofs[0]
        if state.writer_change:
            raise ValueError("publication_retry_archive_ownership_changed")

    def attempt(claim, phase, digest, body, write):
        assert (phase,digest,body) == ("retry_queued","0"*64,{})
        state.attempts += 1
        fence = b"retry-owned"
        life.archive.exclusive(life.archive.fence_ref(claim.run_id), fence)
        # This is an in-memory writer seam. Real _attempt/COMMIT is unchanged.
        write(FakeSession(state, writer=True))
        state.job.state_version += 1
        state.rows.append(event(state.job.state_version,state.job.generation,"queued","retry_queued"))
        if state.post_write:
            state.post_write()
        if state.readback_change:
            state.job.state_version += 1
        if state.unknown_commit:
            raise BundleCommitUnconfirmed("closed-control")
        return fence

    life._ro, life._attempt = ro, attempt
    life._publication = lambda _session,_job: None
    life._retry_owned_archive_proof51, life._retry_owner_native_unchanged51 = proof, unchanged
    life.preparer = SimpleNamespace(retry_authority49=authority, verify_retry_native49=native,
                                   verify_retry_sources49=sources)
    return state


def retry(control, *, mode=jobs.DIAGNOSTIC_GAP_RECOVERY_MODE):
    return control.life.retry_unconfirmed("gap-run", expected_request="gap-request",
        expected_generation=control.job.generation, expected_version=control.job.state_version,
        recovery_mode=mode)


def add_saved(control):
    claim = BundleClaim("gap-run","gap-request","fixture",2)
    _reference, digest = control.life.archive.put_diagnostic(claim, {"schema_version":"closed-test.v1"})
    control.rows.extend([event(3,2,"queued","claimed"),
        event(4,2,"unconfirmed","unconfirmed",{"status":"saved","sha256":digest})])
    control.job.generation, control.job.state_version, control.old_version = 2,4,4
    return claim


def test_default_retry_and_get_remain_refused(control):
    assert control.life.recovery_status("gap-run")["reason_code"] == "publication_retry_diagnostic_unavailable"
    with pytest.raises(ValueError, match="publication_retry_diagnostic_unavailable"):
        retry(control, mode=None)
    assert not control.proof_calls and control.attempts == 0


@pytest.mark.parametrize("mode", ["", "auto", True, 1])
def test_unknown_mode_rejected(control, mode):
    with pytest.raises(ValueError, match="publication_retry_recovery_mode_invalid"):
        retry(control, mode=mode)
    assert control.attempts == 0


def test_custom_equal_object_cannot_enable_recovery(control):
    class Equal:
        def __eq__(self,_other): pytest.fail("custom equality must not run")
    with pytest.raises(ValueError,match="publication_retry_recovery_mode_invalid"):
        retry(control,mode=Equal())


def test_explicit_gap_uses_two_independent_proofs_and_preserves_event(control):
    original = copy.deepcopy(control.rows)
    result = retry(control)
    assert (result["generation"],result["state_version"],result["state"]) == (2,3,"queued")
    assert result["recovery_mode"] == jobs.DIAGNOSTIC_GAP_RECOVERY_MODE
    assert result["diagnostic_gap_generations"] == [1]
    assert result["diagnostic_gap_event_ids"] == [original[-1].event_id]
    with control.life._ro() as session:
        native_history=control.life._retry_gap_history_native49(session,"gap-run",2)
    assert result["event_history_sha256"] == sha(encoded(native_history))
    assert result["archive_ownership_sha256"] == sha(encoded(control.proofs[0]))
    assert control.rows[:2] == original and len(control.proof_calls) == 2
    assert control.proof_calls[0] is not control.proof_calls[1]
    assert not control.life.archive.fenced("gap-run")
    assert list((control.life.archive.root / "diagnostics").glob("*.json")) == []


@pytest.mark.parametrize("change", ["missing", "tampered", "wrong_hash"])
def test_saved_diagnostic_is_verified_after_prior_gap(control, change):
    claim = add_saved(control)
    path = control.life.archive._path(control.life.archive.diagnostic_ref(claim))
    if change == "missing":
        path.unlink()
    elif change == "tampered":
        path.write_bytes(b'{"different":true}')
    else:
        control.rows[-1] = event(4,2,"unconfirmed","unconfirmed",{"status":"saved","sha256":"f"*64})
    with pytest.raises(ValueError):
        retry(control)
    assert control.attempts == 0 and not control.proof_calls


def test_saved_diagnostic_changed_after_cas_keeps_own_fence(control):
    claim = add_saved(control)
    path = control.life.archive._path(control.life.archive.diagnostic_ref(claim))
    control.post_write = lambda: path.write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError, match="publication_retry_diagnostic_changed"):
        retry(control)
    assert control.life.archive.fenced("gap-run")


@pytest.mark.parametrize("failure", ["second_changed","second_error","writer_native","head","source_before",
    "source_after","unknown_commit","history","history_payload","history_native","cas","target_saved","native"])
def test_changed_permission_never_releases_execution_fence(control, failure):
    if failure == "second_changed": control.proofs[1] = {"inventory":[("different",)],"owners":[]}
    elif failure == "second_error": control.proofs[1] = ValueError("publication_retry_archive_ownership_unknown")
    elif failure == "writer_native": control.writer_change = True
    elif failure == "head": control.readback_change = True
    elif failure == "source_before": control.source_change_at = 1
    elif failure == "source_after": control.source_change_at = 2
    elif failure == "unknown_commit": control.unknown_commit = True
    elif failure == "history": control.post_write = lambda: setattr(control.rows[0],"event_id","a"*64)
    elif failure == "history_payload": control.post_write = lambda: setattr(control.rows[0],"payload_json",control.rows[0].payload_json+" ")
    elif failure == "history_native": control.post_write = lambda: setattr(control.rows[0],"occurred_at",NOW.replace(day=10))
    elif failure == "cas": control.cas_lost = True
    elif failure == "target_saved": control.target_saved = [object()]
    else: control.native_changed = True
    with pytest.raises((ValueError,PermissionError,BundleCommitUnconfirmed)):
        retry(control)
    assert control.life.archive.fenced("gap-run") == bool(control.attempts)


def test_preexisting_unknown_fence_is_untouched(control):
    path = control.life.archive._path(control.life.archive.fence_ref("gap-run"))
    control.life.archive.exclusive(control.life.archive.fence_ref("gap-run"), b"original-unknown")
    with pytest.raises(ValueError, match="publication_bundle_fence_requires_readonly_reconciliation"):
        retry(control)
    assert path.read_bytes() == b"original-unknown" and control.attempts == 0


@pytest.mark.parametrize("change", ["duplicate_gap","event_hash","event_field","version","no_gap"])
def test_ambiguous_event_history_is_not_recovery_permission(control, change):
    if change == "duplicate_gap":
        control.rows.append(event(3,1,"unconfirmed","unconfirmed",{"status":"unavailable","sha256":None}))
        control.job.state_version = 3
    elif change == "event_hash": control.rows[0].event_id = "a"*64
    elif change == "event_field": control.rows[0].payload_json += " "
    elif change == "version": control.rows[0].version = 4
    else:
        control.rows[-1] = event(2,1,"unconfirmed","unconfirmed")
    with pytest.raises(ValueError):
        retry(control)
    assert control.attempts == 0 and not control.proof_calls


class OwnerSession(ReadSession):
    def exec_driver_sql(self, statement, parameters):
        if not statement.startswith("SELECT *"):
            return super().exec_driver_sql(statement, parameters)
        table = statement.split(" FROM ")[1].split(" WHERE ")[0]
        record = {jobs.Job.__tablename__:jobs.Job, jobs.Saved.__tablename__:jobs.Saved,
            jobs.Ack.__tablename__:jobs.Ack, RunRecord.__tablename__:RunRecord,
            RunRoleOutputRecord.__tablename__:RunRoleOutputRecord}[table]
        if record is RunRoleOutputRecord:
            obj = self.publication if parameters[1] == jobs.PUBLICATION_SOURCE_ROLE else SimpleNamespace(**self.selection)
        else:
            obj = self.records.get((record,parameters[0]))
        columns = list(record.__table__.columns)
        def native(column):
            value = getattr(obj,column.name,None)
            if isinstance(value,datetime): return value.isoformat()
            if value is not None or column.nullable: return value
            kind = column.type.python_type
            return NOW.isoformat() if kind is datetime else {str:"fixture",int:0,float:0.0,bytes:b""}[kind]
        return SimpleNamespace(keys=lambda:[column.name for column in columns],
            fetchall=lambda:[] if obj is None else [tuple(native(column) for column in columns)])


@pytest.fixture
def owner(snapshot):
    # Repair only fixture material and remove unused fixture manifest. Production
    # archives are never touched; full _confirmed executes below, not a snapshot.
    archive = snapshot.archive
    for path in archive._path(snapshot.saved.bundle_ref).parent.iterdir():
        if path.name != snapshot.saved.bundle_hash + ".json": path.unlink()
    material = b"owned-fixture-material"
    digest = sha(material)
    bundle = snapshot.bundle.model_copy(update={"material_ref":"materials/"+digest+".json",
        "material_sha256":digest,"material_bytes":len(material)})
    path = archive._path(bundle.material_ref)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(material)
    previous = snapshot.saved.bundle_hash
    archive._path(snapshot.saved.bundle_ref).unlink()
    reference, bundle_hash, size = archive.put(bundle)
    snapshot.saved.bundle_ref, snapshot.saved.bundle_hash, snapshot.saved.bundle_bytes = reference,bundle_hash,size
    snapshot.saved.material_ref, snapshot.saved.material_hash, snapshot.saved.material_bytes = bundle.material_ref,digest,len(material)
    snapshot.job.bundle_hash = snapshot.ack.bundle_hash = bundle_hash
    snapshot.records.pop((jobs.Saved,previous))
    snapshot.records[(jobs.Saved,bundle_hash)] = snapshot.saved
    snapshot.bundle = bundle
    with snapshot.life._ro() as original:
        snapshot.session = OwnerSession(original.records,original.publication,original.selection)
    return snapshot


def test_existing_owned_archive_proof_reaudits_complete_material_and_native_rows(owner):
    proof = owner.life._retry_owned_archive_proof51(owner.session,"gap-run")
    assert len(proof["inventory"]) == 2 and len(proof["owners"]) == 1
    assert len(proof["owners"][0]["native"]) == 6
    owner.life._retry_owner_native_unchanged51(owner.session,proof)


@pytest.mark.parametrize("change", ["orphan","partial","target","ack","material","material_index","native","source","selection"])
def test_existing_owned_archive_proof_rejects_incomplete_or_changed_owner(owner, change):
    target = "gap-run"
    if change == "orphan":
        owner.archive._path("materials/"+"a"*64+".json").write_bytes(b"orphan")
    elif change == "partial":
        owner.archive._path(owner.bundle.material_ref).parent.joinpath("partial.tmp").write_bytes(b"partial")
    elif change == "target": target = owner.bundle.run_id
    elif change == "ack": owner.records.pop((jobs.Ack,owner.bundle.run_id))
    elif change == "material": owner.archive._path(owner.bundle.material_ref).write_bytes(b"changed")
    elif change == "material_index": owner.saved.material_bytes += 1
    elif change == "native": owner.job.generation = "invalid-native-type"
    elif change == "source": owner.publication.payload_json += " "
    else: owner.job.selection_native_hash = "a"*64
    with pytest.raises(ValueError):
        owner.life._retry_owned_archive_proof51(owner.session,target)


def test_short_writer_rejects_changed_native_material_index(owner):
    proof = owner.life._retry_owned_archive_proof51(owner.session,"gap-run")
    owner.saved.material_bytes += 1
    with pytest.raises(ValueError,match="publication_retry_archive_ownership_changed"):
        owner.life._retry_owner_native_unchanged51(owner.session,proof)


def functions(text):
    result = {}
    def visit(node,path=()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                name = path + (child.name,)
                if not isinstance(child,ast.ClassDef): result[".".join(name)] = ast.get_source_segment(text,child)
                visit(child,name)
            else: visit(child,path)
    visit(ast.parse(text))
    return result


def test_only_two_retry_functions_change_from_installed56():
    # Root authorized subsequent execution controls independently. Audit this
    # original gap candidate's sealed bytes, then pin the live composite chain.
    from test_confirmed_report_snapshot import _trusted_jobs_chain
    _trusted_jobs_chain()
    before = functions(BEFORE.read_text(encoding="utf-8"))
    sealed = DEV / "artifacts/core-goal-20261011/diagnostic-gap-recovery01/candidate/packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py"
    assert sha(sealed.read_bytes()) == "4e340cfc701509b3e1c23e9bc87c5238bcc4087f2cb0076c643c14a7d31381ef"
    after = functions(sealed.read_text(encoding="utf-8"))
    allowed = {"DurablePublicationLifecycle._retry_archive_proof49", "DurablePublicationLifecycle.retry_unconfirmed",
               "DurablePublicationLifecycle.retry_unconfirmed.write"}
    assert set(after)-set(before) == {"DurablePublicationLifecycle._retry_gap_history_native49"}
    assert not set(before)-set(after)
    assert {name for name in before if before[name] != after[name]} == allowed
    # Includes append_event/metadata, all SavedACK/claim/fence and GET functions.

