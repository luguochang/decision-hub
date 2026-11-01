"""Windows native source sharing controls and one real DEV closure capture."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    CallbackSourceLease, _Identity, callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import _ConsumerSourceGuard
from tests.dsh_native.test_provider_input_receipts import active as active
from tests.dsh_native.test_provider_input_receipts import inputs as inputs
from tests.dsh_native.test_provider_input_receipts import forbid_external_connections as forbid_external_connections

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows share-mode control")


def _source(tmp_path, *, bytes_count=32):
    root = tmp_path.resolve()
    for name in ("application", "decision", "persistence"):
        (root / "packages/kernel/decision_hub_kernel" / name).mkdir(parents=True)
    file = root / "packages/kernel/decision_hub_kernel/application/guarded.py"
    file.write_bytes(b"#" * bytes_count)
    relative = file.relative_to(root).as_posix()
    graph = SimpleNamespace(
        root=root,
        files=((relative, hashlib.sha256(file.read_bytes()).hexdigest()),),
        matches_identity=lambda: True,
        matches_scope_identity=lambda: True,
    )
    return _ConsumerSourceGuard.capture(graph, root), file


def test_native_source_bytes_delete_and_parent_rename_are_refused(tmp_path):
    source, file = _source(tmp_path)
    with callback_source_lease_scope() as lease:
        guard = lease.pin(source)
        assert guard.matches()
        for operation in (lambda: file.write_bytes(b"changed"), file.unlink,
                          lambda: file.parent.rename(file.parent.with_name("renamed"))):
            with pytest.raises(PermissionError):
                operation()
        assert guard.matches()
    file.write_bytes(b"after-own-close")
    with pytest.raises(ValueError, match="source_changed"):
        guard.matches()


def test_fresh_inventory_and_native_identity_drift_are_rejected(tmp_path, monkeypatch):
    source, file = _source(tmp_path)
    with callback_source_lease_scope() as lease:
        guard = lease.pin(source)
        added = file.with_name("additional.py")
        added.write_text("# new lexical source")
        assert not guard.matches()
        added.unlink()
        original = lease.native.identity
        changed_handle = lease._files[0][1]
        monkeypatch.setattr(lease.native, "identity", lambda handle: (
            _Identity(original(handle).volume, b"x" * 16, original(handle).final_path)
            if handle == changed_handle else original(handle)
        ))
        assert not guard.matches()


def test_source_change_between_full_capture_and_native_pin_is_rejected(tmp_path):
    source, file = _source(tmp_path)
    original_time = file.stat().st_mtime_ns
    file.write_bytes(b"!" * file.stat().st_size)
    os.utime(file, ns=(file.stat().st_atime_ns, original_time))
    with pytest.raises(ValueError, match="source_changed"):
        with callback_source_lease_scope() as lease:
            lease.pin(source)
    file.write_bytes(b"cleanup confirms no leaked HANDLE")


def test_authenticated_selection_seal_bounds_writer_to_live_and_selected_identity(
    tmp_path, monkeypatch,
):
    """The sealed cut excludes unrelated archives without weakening consumed bytes."""
    from packages.kernel.decision_hub_kernel.application import (
        research_graph_history_versions as history,
    )
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        _bounded_current_inventory,
    )

    source, live_file = _source(tmp_path)
    root = source.root
    retained = root / "packages/kernel/research_recipe_versions/unselected/root/old.py"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"unselected retained history")
    retained_relative = retained.relative_to(root).as_posix()
    retained_hash = hashlib.sha256(retained.read_bytes()).hexdigest()
    source.graph.files = (*source.graph.files, (retained_relative, retained_hash))
    component = root / "packages/kernel/market_component_versions/unselected/root/old.py"
    component.parent.mkdir(parents=True)
    component.write_bytes(b"unselected component history")
    component_relative = component.relative_to(root).as_posix()
    component_hash = hashlib.sha256(component.read_bytes()).hexdigest()
    source.graph.files = (*source.graph.files, (component_relative, component_hash))
    archive = root / "packages/kernel/research_recipe_versions/selected"
    selected = archive / "root/packages/selected.py"
    selected.parent.mkdir(parents=True)
    selected.write_bytes(b"selected immutable implementation")
    selected_relative = "packages/selected.py"
    selected_hash = hashlib.sha256(selected.read_bytes()).hexdigest()
    release = archive / "release.json"
    release.write_text("{}", encoding="utf-8")
    release_hash = hashlib.sha256(release.read_bytes()).hexdigest()
    selected_archive_relative = selected.relative_to(root).as_posix()
    release_relative = release.relative_to(root).as_posix()
    source.graph.files = (
        *source.graph.files,
        (selected_archive_relative, selected_hash),
        (release_relative, release_hash),
    )
    registration = SimpleNamespace(
        directory="selected", index_hash=release_hash, graph_hash="graph",
    )
    monkeypatch.setattr(history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(
        history, "_RESOURCE_ROOT", root / "packages/kernel/research_recipe_versions",
    )
    monkeypatch.setattr(history, "_REGISTRY_BINDING", None)
    monkeypatch.setattr(
        history, "_original_recipe_registration", lambda _session, _run_id: registration,
    )
    monkeypatch.setattr(
        history,
        "_verified_release",
        lambda _registration: ({
            "stored_files": [selected_relative],
            "all_files": {selected_relative: selected_hash},
        }, None, root),
    )
    monkeypatch.setattr(history, "_active_registrations", lambda: [registration])
    bounded_inventory = _bounded_current_inventory(root)

    full_identity_calls = 0
    scope_identity_calls = 0

    def full_identity() -> bool:
        nonlocal full_identity_calls
        full_identity_calls += 1
        return True

    def scope_identity() -> bool:
        nonlocal scope_identity_calls
        scope_identity_calls += 1
        return _bounded_current_inventory(root) == bounded_inventory

    source.graph.matches_identity = full_identity
    source.graph.matches_scope_identity = scope_identity

    # Default callers still pin every declared historical byte.
    with callback_source_lease_scope() as complete_lease:
        complete_lease.pin(source)
        assert retained_relative in complete_lease._expected
        assert component_relative in complete_lease._expected

    with callback_source_lease_scope(
        defer_unselected_recipe_archives=True,
    ) as lease:
        guard = lease.pin(source)
        assert retained_relative not in lease._expected
        assert retained_relative in lease._deferred_recipe_archives
        assert component_relative not in lease._expected
        assert component_relative in lease._deferred_recipe_archives
        assert selected_archive_relative in lease._deferred_recipe_archives
        assert release_relative in lease._deferred_recipe_archives
        with pytest.raises(ValueError, match="source_changed"):
            lease.seal_authenticated_selection("run")
        lease.extend_authenticated_selection(object(), "run")
        assert release_relative in lease._expected
        assert selected_archive_relative in lease._expected
        assert retained_relative in lease._deferred_recipe_archives
        assert selected_archive_relative not in lease._deferred_recipe_archives
        assert release_relative not in lease._deferred_recipe_archives
        lease.seal_authenticated_selection("run")
        complete_full_checks = full_identity_calls

        def forbidden_full_identity() -> bool:
            raise AssertionError("sealed writer recursively rechecked full inventory")

        source.graph.matches_identity = forbidden_full_identity
        monkeypatch.setattr(
            lease.native,
            "digest",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("sealed writer rehashed source bytes")
            ),
        )
        assert guard.matches()
        assert full_identity_calls == complete_full_checks and scope_identity_calls == 1

        # An unselected content-addressed payload cannot enter this already
        # authenticated callback and must not make writer cost grow with history.
        unrelated = root / "packages/kernel/research_recipe_versions/later/root/old.py"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(b"unselected history")
        assert guard.matches()
        assert full_identity_calls == complete_full_checks and scope_identity_calls == 2

        other_live = root / "packages/other_product/live.py"
        other_live.parent.mkdir(parents=True)
        other_live.write_bytes(b"new non-consumer current source")
        assert not guard.matches()
        other_live.unlink()
        assert guard.matches()

        # Existing live and selected bytes remain protected by native handles.
        for path in (live_file, selected, release):
            with pytest.raises(PermissionError):
                path.write_bytes(b"changed")

        # A newly introduced live consumer module is still a material identity
        # change even though unrelated retained payloads are excluded.
        added_live = live_file.with_name("added.py")
        added_live.write_bytes(b"new live code")
        assert not guard.matches()
        added_live.unlink()
        assert guard.matches()

        # None is an authenticated registry binding state, not a wildcard.
        monkeypatch.setattr(history, "_REGISTRY_BINDING", object())
        assert not guard.matches()
        with pytest.raises(ValueError, match="source_changed"):
            lease.extend_authenticated_selection(object(), "run")
    unrelated.unlink()


@pytest.mark.parametrize('relative',['../foreign.py','C:foreign.py','./guarded.py'])
def test_native_path_escape_and_noncanonical_lexical_inventory_fail_closed(tmp_path,relative):
    source,file=_source(tmp_path)
    source.graph.files=((relative,hashlib.sha256(file.read_bytes()).hexdigest()),)
    with pytest.raises(ValueError,match='source_changed'):
        with callback_source_lease_scope() as lease:lease.pin(source)
    assert not lease.active and not lease._files and not lease._directories


def test_chunk_deadline_aborts_before_writer_and_releases_only_own_handles(tmp_path):
    source, file = _source(tmp_path, bytes_count=3 * 1024 * 1024)
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls > 12:
            raise ValueError("research_provider_callback_lifetime_elapsed")
    with pytest.raises(ValueError, match="lifetime_elapsed"):
        with callback_source_lease_scope(checkpoint) as lease:
            lease.pin(source)
    assert not lease.active and not lease._files and not lease._directories
    file.write_bytes(b"after-cancel")


def test_other_thread_cannot_close_live_validation_handles(tmp_path):
    source, file = _source(tmp_path)
    with callback_source_lease_scope() as lease:
        guard = lease.pin(source)
        errors = []
        def close_from_other_thread():
            try:
                lease.close()
            except ValueError as error:
                errors.append(str(error))
        other = threading.Thread(target=close_from_other_thread)
        other.start()
        other.join(2)
        assert not other.is_alive() and errors == ["original_preparation_source_changed"]
        with pytest.raises(PermissionError):
            file.write_text("must remain denied while validator is alive")
        assert guard.matches()
    file.write_text("owner released only after scope exit")


def test_two_callback_owners_keep_independent_native_leases(tmp_path):
    source,file=_source(tmp_path)
    ready,release=threading.Event(),threading.Event()
    errors=[];second=[]
    def worker():
        try:
            with callback_source_lease_scope() as lease:
                second.append(lease);guard=lease.pin(source);ready.set()
                assert release.wait(3) and guard.matches()
        except BaseException as error:
            errors.append(error);ready.set()
    with callback_source_lease_scope() as first:
        first.pin(source)
        thread=threading.Thread(target=worker);thread.start()
        try:
            assert ready.wait(3) and second and not errors
            first.close()
            assert second[0].active
            with pytest.raises(PermissionError):file.write_bytes(b'first request must not release second')
        finally:release.set();thread.join(3)
    assert not thread.is_alive() and not errors
    assert not second[0].active and not second[0]._files and not second[0]._directories
    file.write_bytes(b'after both owners exit')


def test_actual_dev_complete_closure_has_no_full_byte_read_after_pin(monkeypatch):
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import _ReadSourceGuard
    start = time.monotonic()
    graph = _ReadSourceGuard.capture()
    assert graph is not None and graph.root.name == ".market-risk-b2-b4"
    source = _ConsumerSourceGuard.capture(graph, graph.root)
    captured_at = time.monotonic()
    evidence_dir = graph.root / "artifacts/market-risk-implementation-20261007/B2/provider-input-timeout48"
    (evidence_dir / "dev-closure-source-pins48_01.json").write_text(json.dumps({
        "root": str(graph.root), "graph_files": graph.files, "consumer_files": source.files,
        "scope": "actual current DEV byte set only; not a production proof",
    }, indent=2) + "\n")
    with callback_source_lease_scope() as lease:
        guard = lease.pin(source)
        pinned_at = time.monotonic()
        count = len(lease._files)
        hashes, size = count, lease.hashed_bytes
        # The immutable source HASH pass has already completed outside any DB.
        def forbid_full_byte_hash(*_args):
            raise AssertionError("writer/source identity cut attempted a full byte hash")
        monkeypatch.setattr(lease.native, "digest", forbid_full_byte_hash)
        original_read = Path.read_bytes
        identity_reads = []
        def read_at_identity(path):
            payload = original_read(path)
            identity_reads.append((str(path), len(payload)))
            return payload
        monkeypatch.setattr(Path, "read_bytes", read_at_identity)
        assert guard.matches() and guard.matches()
        checked_at = time.monotonic()
        identities = lease.identity_checks
    metrics = {
        "scope": "actual DEV closure only, no DB/writer/Host/provider",
        "source_capture_seconds": captured_at - start, "native_pin_hash_seconds": pinned_at - captured_at,
        "two_identity_checks_seconds": checked_at - pinned_at, "total_seconds": time.monotonic() - start,
        "source_files": count, "hash_calls_at_pin": hashes, "native_hash_bytes": size,
        "native_identity_checks": identities, "live_handles_after_exit": len(lease._files) + len(lease._directories),
        "identity_python_byte_reads": identity_reads,
        "real_30s_run_verified": False,
    }
    path = graph.root / "artifacts/market-risk-implementation-20261007/B2/provider-input-timeout48/dev-closure-metrics48_01.json"
    path.write_text(json.dumps(metrics, indent=2) + "\n")
    assert time.monotonic() - start < 30


def test_actual_selected_large_closure_has_bounded_short_writer(monkeypatch):
    """Real captured ROOT bytes, DEV copy; not a production permission proof."""
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        _original_inventory, _original_contract_signatures,
    )
    from packages.kernel.decision_hub_kernel.application.research_context_selection import _selector_identity
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import observation_json
    evidence = Path(__file__).resolve().parents[2] / 'artifacts/market-risk-implementation-20261007/B2/provider-input-timeout48'
    captured = json.loads((evidence/'root-source-closure48_02.private.json').read_text())
    assert captured['copy_completed'] and captured['selected_run_id']=='run_664096f1f03e43b285f05ce9bddc68f7'
    root = Path(captured['target'])
    native_root = Path('\\\\?\\'+str(root.absolute()))
    # Files outside the actual consumed closure need only their fresh lexical
    # names for the old guard. Empty inventory placeholders are explicit and
    # never included in file hashes, material authority, or archive validation.
    placeholders = 0
    for relative in captured['graph_inventory']:
        path = native_root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'')
            placeholders += 1
    inventory = _original_inventory(native_root)
    assert inventory == tuple(captured['graph_inventory'])
    selector = observation_json(_selector_identity())
    signatures = _original_contract_signatures()
    identity_reads = []
    original_read = Path.read_bytes
    def identity_read(path):
        raw=original_read(path)
        identity_reads.append((str(path),len(raw)))
        return raw
    def current_identity():
        return (_original_inventory(native_root)==inventory
                and observation_json(_selector_identity())==selector
                and _original_contract_signatures()==signatures)
    started=time.monotonic()
    expected={info['path']:info['sha256'] for info in captured['copied_source_files']}
    for relative, digest in expected.items():
        assert hashlib.sha256((native_root/relative).read_bytes()).hexdigest()==digest
    capture_at=time.monotonic()
    source=_ConsumerSourceGuard(SimpleNamespace(root=root,files=tuple(sorted(expected.items())),
                                              matches_identity=current_identity),root,
                                tuple(tuple(item) for item in captured['consumer_files']))
    def deadline():
        if time.monotonic()-started >=30:
            raise ValueError('research_provider_callback_lifetime_elapsed')
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE private_probe(value INTEGER)');db.commit()
    try:
        with callback_source_lease_scope(deadline) as lease:
            guard=lease.pin(source);pinned_at=time.monotonic()
            assert len(lease._files)==636 and lease.hashed_bytes==captured['copied_source_bytes']
            chosen=next(path for path,_h,_i in lease._files if '/80fa1df7' in path.as_posix() and path.suffix=='.py')
            # Actual captured capsule bytes deny writing, deletion and parent rename.
            for action in (lambda: Path('\\\\?\\'+str(chosen)).write_bytes(b'changed'),
                           lambda: Path('\\\\?\\'+str(chosen)).unlink(),
                           lambda: chosen.parent.rename(chosen.parent.with_name('must-not-rename'))):
                with pytest.raises(PermissionError): action()
            monkeypatch.setattr(lease.native,'digest',lambda *_a: (_ for _ in ()).throw(AssertionError('writer full hash')))
            monkeypatch.setattr(Path,'read_bytes',identity_read)
            writer_at=time.monotonic();db.execute('BEGIN IMMEDIATE')
            assert guard.matches();first_at=time.monotonic()
            db.execute('INSERT INTO private_probe VALUES (1)')
            assert guard.matches();last_at=time.monotonic()
            db.commit();committed_at=time.monotonic()
            native_checks=lease.identity_checks
        metrics={'schema_version':'source-lease-large-closure-control.v1','source_artifact':'root-source-closure48_02.private.json',
                 'original_guard_capture_seconds':captured['source_guard_capture_seconds'],
                 'dev_copy_capture_fullhash_seconds':capture_at-started,
                 'native_lease_fullhash_seconds':pinned_at-capture_at,
                 'first_writer_identity_seconds':first_at-writer_at,
                 'second_writer_identity_seconds':last_at-first_at,
                 'writer_total_seconds':committed_at-writer_at,'total_control_seconds':time.monotonic()-started,
                 'files':636,'native_hashed_bytes':lease.hashed_bytes,'native_identity_checks':native_checks,
                 'inventory_metadata_only_placeholders':placeholders,
                 'live_handles_after_exit':len(lease._files)+len(lease._directories),
                 'identity_python_byte_reads':identity_reads,'E_filesystem':'NTFS','FileIdInfo_bits':128,
                 'actual_Run_verified':False,'production_authority':False,'Root_share_locks':0}
        (evidence/'largeclosure-metrics48_01.json').write_text(json.dumps(metrics,indent=2)+'\n')
        assert committed_at-writer_at <=.5
        assert time.monotonic()-started <30
        assert not lease.active and not lease._files and not lease._directories
    finally:
        db.close()


@pytest.mark.usefixtures('forbid_external_connections')
def test_unknown_commit_ack_keeps_lease_until_worker_exit_and_authenticated_repeat(active,monkeypatch):
    from sqlalchemy import event, select
    from packages.kernel.decision_hub_kernel.application.provider_input_receipts import ProviderInputReceiptService, _address
    from packages.kernel.decision_hub_kernel.application.provider_callback_diagnostics import ProviderCallbackDiagnostics
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    prepared, submit=active
    leases=[]
    actual_init=CallbackSourceLease.__init__
    def capture_init(self,*args,**kwargs):
        actual_init(self,*args,**kwargs);leases.append(self)
    monkeypatch.setattr(CallbackSourceLease,'__init__',capture_init)
    class PhysicalCommitUnknown(ProviderCallbackDiagnostics):
        def observe_commit(self,session):
            super().observe_commit(session)
            # Bind this exact writer Session; class-wide events include private
            # mirror commits and expired RO ORM objects, which are not its ACK.
            native=session.connection().connection.driver_connection
            def unknown_commit(_session):
                assert not native.in_transaction
                assert native.execute('SELECT output_id FROM run_role_outputs WHERE output_id=?',(_address(submit)[0],)).fetchone()
                lease=leases[-1]
                assert lease.active and lease._files
                path=lease._files[0][0]
                with pytest.raises(PermissionError):
                    path.write_bytes(b'no-close-before-actual-commit-exit')
                raise OSError('private_control_commit_ack_unknown')
            event.listen(session,'after_commit',unknown_commit)
    diagnostics=[]
    with pytest.raises(Exception,match='private_control_commit_ack_unknown|committed'):
        ProviderInputReceiptService(prepared.database).record_input(submit,diagnostics=PhysicalCommitUnknown(submit,'input',sink=diagnostics.append))
    assert len(leases)==1 and not leases[0].active and not leases[0]._files and not leases[0]._directories
    with prepared.database.session() as session:
        rows=session.scalars(select(RunRoleOutputRecord).where(RunRoleOutputRecord.role.like('model_input:%'))).all()
        assert len(rows)==1
        original=tuple(getattr(rows[0],column.key) for column in rows[0].__table__.columns)
    repeated=ProviderInputReceiptService(prepared.database).record_input(submit)
    assert repeated.reference.output_id==original[0] and len(leases)==2
    with prepared.database.session() as session:
        row=session.get(RunRoleOutputRecord,repeated.reference.output_id)
        assert tuple(getattr(row,column.key) for column in row.__table__.columns)==original
    assert not leases[1].active and not leases[1]._files and not leases[1]._directories
    evidence=Path(__file__).resolve().parents[2]/'artifacts/market-risk-implementation-20261007/B2/provider-input-timeout48'
    (evidence/'private-callback-lease-metrics48_01.json').write_text(json.dumps({
        'scope':'private controlled callback; unknown physical COMMIT ACK then exact reauthentication',
        'leases':[{'files':len(lease.hash_observations),'native_hash_bytes':lease.hashed_bytes,
                   'entry_hash_observations':lease.hash_observations,
                   'live_handles_after_exit':len(lease._files)+len(lease._directories)} for lease in leases],
        'diagnostic_events':diagnostics,'actual_Run_verified':False,
    },indent=2)+'\n')


def test_real_registry_native_selection_extends_exact_capsule_and_refuses_changed_identity(tmp_path,monkeypatch):
    """Real source/install/native guards; controlled selection, no business execution."""
    from datetime import UTC,datetime
    from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as current
    from packages.kernel.decision_hub_kernel.application import research_recipe_archive as archive
    from packages.kernel.decision_hub_kernel.application import research_graph_history_versions as history
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import observation_json
    from packages.kernel.decision_hub_kernel.persistence.db import Database,RunRoleOutputRecord
    from tests.market_risk.test_recipe_selection_v3 import selection
    root=(tmp_path/'repo').resolve();root.mkdir()
    dev=Path(__file__).resolve().parents[2]
    manifest=current.graph_recipe_manifest_v3()
    source_items=(*manifest.source_files,*manifest.schema_files)
    values={item.path:item.payload_hash for item in source_items}
    def put(name,raw):
        path=Path('\\\\?\\'+str(root/name));path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    for item in source_items:put(item.path,(dev/item.path).read_bytes())
    put(manifest.registration.path,(dev/manifest.registration.path).read_bytes())
    values[manifest.registration.path]=manifest.registration.sha256
    dependency=hashlib.sha256(observation_json(manifest.model_dump(mode='json')).encode()).hexdigest()
    pointer=current.GraphRecipeInstalledPointerV3(registration=manifest.registration,dependency_hash=dependency,plugin_build_hash='a'*64)
    pointer_raw=observation_json(pointer.model_dump(mode='json')).encode();put(current.INSTALLED_POINTER_REF,pointer_raw)
    values[current.INSTALLED_POINTER_REF]=hashlib.sha256(pointer_raw).hexdigest()
    prior_path='artifacts/private-install/receipt.json'
    prior={'schema':'product-validated-builds.v1','repository_root':str(root),'sources':values,
           'artifacts':{'fixture-only':'b'*64},'plugin_build_hash':'a'*64,
           'evidence':{'research_graph_recipe_v3':{'installed_pointer':{'path':current.INSTALLED_POINTER_REF,'sha256':values[current.INSTALLED_POINTER_REF]}}}}
    prior_raw=observation_json(prior).encode();put(prior_path,prior_raw)
    result=archive.prepare_retained_graph_recipe_v3_archive(repository_root=root,prior_receipt_path=root/prior_path,
        prior_receipt_sha256=hashlib.sha256(prior_raw).hexdigest(),original_manifest=manifest.model_dump(mode='json'),staging_root=root/'artifacts/stage')
    for name in result.install_sources:put(name,archive._read(archive._path(root/'artifacts/stage',name)))
    new={**prior,'sources':{**values,**result.install_sources},'evidence':{**prior['evidence'],'retained_recipe_archives_v1':{'registry':dict(result.registry_ref)}}}
    new_path='artifacts/current/receipt.json';new_raw=observation_json(new).encode();put(new_path,new_raw)
    monkeypatch.setattr(history,'_CURRENT_ROOT',root)
    monkeypatch.setattr(history,'_RESOURCE_ROOT',root/'packages/kernel/research_recipe_versions')
    monkeypatch.setattr(history,'_REGISTRY_BINDING',None)
    history.configure_retained_recipe_registry_from_validated_release(receipt_path=root/new_path,receipt_sha256=hashlib.sha256(new_raw).hexdigest())
    model=selection(manifest).model_copy(update={
        'installation_receipt':current.GraphRecipeResourceV3(path=prior_path,sha256=hashlib.sha256(prior_raw).hexdigest()),
        'installed_pointer':current.GraphRecipeResourceV3(path=current.INSTALLED_POINTER_REF,sha256=values[current.INSTALLED_POINTER_REF]),
    })
    model=model.model_copy(update={'effective_hash':current._effective_hash(model)})
    raw=observation_json(model.model_dump(mode='json'));digest=hashlib.sha256(raw.encode()).hexdigest()
    database=Database(f'sqlite+pysqlite:///{tmp_path / "selection.sqlite3"}');database.create_all()
    try:
        with database.session() as session:
            session.add(RunRoleOutputRecord(output_id='graph_recipe_selection_'+digest,run_id=model.run_id,
                role='graph_recipe_selection',schema_version=model.schema_version,payload_json=raw,payload_hash=digest,created_at=datetime.now(UTC)))
        graph=SimpleNamespace(root=root,files=tuple((i.path,i.payload_hash) for i in source_items),matches_identity=lambda:True)
        source=_ConsumerSourceGuard.capture(graph,root)
        with callback_source_lease_scope() as lease,database.session() as session:
            guard=lease.pin(source);initial_count=len(lease._files)
            session.connection().exec_driver_sql('BEGIN')
            native=session.connection().connection.driver_connection;before=native.total_changes
            lease.extend_authenticated_selection(session,model.run_id)
            assert native.total_changes==before and len(lease._files)>initial_count and guard.matches()
            registration=history._original_recipe_registration(session,model.run_id)
            exact=root/'packages/kernel/research_recipe_versions'/registration.directory/'release.json'
            assert any(path==exact for path,_h,_i in lease._files)
            with pytest.raises(PermissionError):exact.write_bytes(b'changed index')
            binding=history._REGISTRY_BINDING
            history._REGISTRY_BINDING=None
            try:assert not guard.matches()
            finally:history._REGISTRY_BINDING=binding
            session.rollback()
        assert not lease.active and not lease._files
        with database.session() as session:
            row=session.get(RunRoleOutputRecord,'graph_recipe_selection_'+digest);row.payload_json+=' '
        with pytest.raises(ValueError,match='original_changed'),callback_source_lease_scope() as lease,database.session() as session:
            lease.pin(source);session.connection().exec_driver_sql('BEGIN')
            lease.extend_authenticated_selection(session,model.run_id)
        assert not lease.active and not lease._files and not lease._directories
    finally:database.engine.dispose()
