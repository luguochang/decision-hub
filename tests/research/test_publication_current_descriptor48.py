"""Current descriptor delegation / IO primitives, not active business proof.

Managed receipt/selection/guard leaves are explicit private seams. resolve()
itself issues an opaque closure; no diagnostic _issued injection is used.
These controls do not replace real normal registration and full active replay.
"""
import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from packages.kernel.decision_hub_kernel.application import research_publication_resources as module
from packages.kernel.decision_hub_kernel.application.research_publication_resources import (
    OriginalPublicationResourceAuthority, ResourceBounds, sha,
)
from packages.kernel.decision_hub_kernel.application.research_publication_runtime48 import FrozenDescriptor
from packages.kernel.decision_hub_kernel.application.research_publication_frozen_io48 import FrozenConsumptionIO

@pytest.fixture
def private_session(tmp_path):
    path=tmp_path/"current-descriptor-private.sqlite3"
    with closing(sqlite3.connect(path)) as native,native:
        native.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY)")
        native.execute("INSERT INTO samples VALUES (1)")
    engine=create_engine("sqlite+pysqlite://",poolclass=NullPool,
        creator=lambda:sqlite3.connect(path.as_uri()+"?mode=ro",uri=True))
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            session.connection().exec_driver_sql("BEGIN")
            yield session
            session.rollback()
    finally:engine.dispose()


@pytest.fixture
def current(monkeypatch,tmp_path,private_session):
    root=tmp_path/"logical-root"
    files={"receipt.json":b"receipt","pointer.json":b"pointer","registry.json":b"registry",
        "code.py":b"# normal code attestation\n","schema.json":b'{"source":"original"}',
        "registration.json":b"registration"}
    ref=lambda path:NS(path=path,sha256=sha(files[path]),model_dump=lambda **kw:{"path":path,"sha256":sha(files[path])})
    manifest=NS(source_files=(NS(path="code.py",payload_hash=sha(files["code.py"])),),
        schema_files=(NS(path="schema.json",payload_hash=sha(files["schema.json"])),),
        registration=ref("registration.json"),model_dump=lambda **kw:{"source_files":[{"path":"code.py","payload_hash":sha(files["code.py"])}]})
    installed=NS(receipt=ref("receipt.json"),pointer=ref("pointer.json"),manifest=manifest,dependency_hash="a"*64)
    receipt=NS(installation_receipt=installed.receipt,installed_pointer=installed.pointer,
        dependency_hash=installed.dependency_hash,manifest=manifest,
        model_dump=lambda **kw:{"installation_receipt":installed.receipt.model_dump(),
            "installed_pointer":installed.pointer.model_dump(),"dependency_hash":installed.dependency_hash})
    selection=NS(receipt=receipt,reference=NS(model_dump=lambda **kw:{"run_id":"private-current"}))
    binding=NS(receipt_hash=installed.receipt.sha256,receipt_path=root/"receipt.json",
        registry_path="registry.json",registry_hash=sha(files["registry.json"]))
    monkeypatch.setattr(module.history,"_CURRENT_ROOT",root)
    monkeypatch.setattr(module.history,"_REGISTRY_BINDING",binding)
    monkeypatch.setattr(module.history,"_managed_read",lambda p:files[p])
    monkeypatch.setattr(module.history,"_registry_registrations",lambda *a,**kw:())
    monkeypatch.setattr(module.history,"_original_recipe_registration",lambda *a:None)
    monkeypatch.setattr(module,"resolve_product_receipt",lambda *a,**kw:NS(value={},transport={}))
    monkeypatch.setattr(module.recipe_v3,"_installation_original",lambda:installed)
    monkeypatch.setattr(module.recipe_v3,"_read_graph_recipe_selection_v3_original",lambda *a:selection)
    monkeypatch.setattr(module.recipe_v3,"graph_recipe_manifest_v3",lambda:manifest)
    monkeypatch.setattr(module.recipe_v2,"graph_recipe_manifest_v2",lambda:manifest)
    monkeypatch.setattr(module._ReadSourceGuard,"capture",lambda:NS(files=(),matches=lambda:True))
    authority=OriginalPublicationResourceAuthority(trusted_root=root,bounds=ResourceBounds(32,2*1024*1024,32*1024*1024))
    return authority,private_session,installed,selection,files


def test_current_descriptor_is_distinct_opaque_bound_originals(current):
    authority,session,installed,_,files=current
    closure=authority.resolve(session,"private-current",receipt_ref="receipt.json",receipt_sha256=installed.receipt.sha256)
    assert closure.mode=="current-installation"
    body=json.loads(closure.active_binding_json)
    assert body["schema_version"]=="publication-current-installation-descriptor.v1"
    assert body["installation_receipt"]==installed.receipt.model_dump()
    assert body["installed_pointer"]==installed.pointer.model_dump()
    assert {entry.namespace for entry in closure.entries}=={"current-root"}
    assert not any(entry.logical_ref=="release.json" for entry in closure.entries)
    copies=[{"relative_ref":entry.physical_ref,"sha256":entry.sha256,"bytes":entry.bytes,
        "base64":base64.b64encode(files[entry.physical_ref]).decode()} for entry in closure.entries]
    resources=authority.authorize_frozen(closure,copies)
    descriptor=FrozenDescriptor(authority,closure,resources)
    assert descriptor.current_bytes("schema.json")==files["schema.json"]
    assert descriptor.current_bytes("code.py")==files["code.py"]
    with pytest.raises(ValueError,match="current_is_not_retained"):
        descriptor.selected_bytes("schema.json")
    with pytest.raises(ValueError,match="resource_missing"):
        descriptor.current_bytes("latest.json")


def test_current_selection_pointer_mismatch_is_rejected_without_issue(current):
    authority,session,installed,selection,_=current
    selection.receipt.installed_pointer=NS(path="pointer.json",sha256="f"*64)
    with pytest.raises(ValueError,match="exact_current_installation"):
        authority.resolve(session,"private-current",receipt_ref="receipt.json",receipt_sha256=installed.receipt.sha256)
    assert not authority._issued


def test_current_original_resource_bound_is_not_relaxed(current):
    authority,session,installed,_,files=current
    files["code.py"]=b"x"*(2*1024*1024+1)
    with pytest.raises(ValueError,match="bytes_or_file_bound"):
        authority.resolve(session,"private-current",receipt_ref="receipt.json",receipt_sha256=installed.receipt.sha256)
    assert not authority._issued


def test_root_data_cannot_be_disguised_as_owned_stage(tmp_path):
    root=tmp_path/"root";root.mkdir()
    data=root/"data.json";data.write_bytes(b"{}")
    value={"sha256":sha(b"{}"),"bytes":2,"present":True}
    with pytest.raises(ValueError,match="owned_outside_root"):
        FrozenConsumptionIO(root=root,sources={},authorization={},staged={str(data):value},
            runtime={},native_database=tmp_path/"private.sqlite3",interpreter=None)
