"""Private runtime filesystem leaves, not normal issuer or Windows proof.

The explicit alias/module leaves let these controls cover cold cache absence
and scope drift without importing another interpreter or touching Root.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from packages.kernel.decision_hub_kernel.application import research_publication_runtime48 as runtime
from test_publication_current_descriptor48 import current, private_session


@pytest.fixture
def private_runtime(tmp_path,monkeypatch):
    root=tmp_path/'logical-root'
    logical=tmp_path/'uv-python/cpython-3.12-windows-x86_64-none'
    logical.mkdir(parents=True);(logical/'python.exe').write_bytes(b'private-interpreter-leaf')
    class Alias:
        def __init__(self,*args):
            self.logical_root=self.resolved_root=logical
            self.executable_sha256=args[2];self.exact_version=args[3]
        def require_unchanged(self):pass
        def require_path(self,path,**kwargs):return Path(path)
    monkeypatch.setattr(runtime,'InterpreterAlias',Alias)
    monkeypatch.setattr(runtime.sys,'_base_executable',str(logical/'python.exe'))
    versions={name:'1' for name in ['pydantic','pydantic-core','jsonschema','referencing','SQLAlchemy','PyYAML']}
    versions.update(python=runtime.sys.version,sqlite=runtime.sqlite3.sqlite_version)
    for name,directory in {'pydantic':'pydantic','pydantic-core':'pydantic_core','jsonschema':'jsonschema',
            'referencing':'referencing','SQLAlchemy':'sqlalchemy','PyYAML':'pyyaml'}.items():
        meta=root/'.venv/Lib/site-packages'/(directory+'-1.dist-info')/'METADATA'
        meta.parent.mkdir(parents=True);meta.write_text(f'Name: {name}\nVersion: 1\n',encoding='utf-8')
    paths=[root/'.venv/Lib/site-packages/pydantic/_internal/_dataclasses.py',
        logical/'Lib/encodings/utf_16_be.py',logical/'Lib/concurrent/futures/thread.py',logical/'Lib/statistics.py']
    modules={}
    for name,path in zip(runtime._RUNTIME_MODULES,paths,strict=True):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'# private known runtime source\n')
        modules[name]=NS(__file__=str(path))
    original_import=runtime.importlib.import_module
    monkeypatch.setattr(runtime.importlib,'import_module',lambda name,*a,**kw:modules[name] if name in modules else original_import(name,*a,**kw))
    return root,versions,paths


def test_cold_known_caches_absent_are_declared_not_required(private_runtime):
    root,versions,paths=private_runtime
    attestation=runtime.ServerRuntimeAttestation(root,versions)
    assert len(attestation.runtime_code)==8
    assert sum(item['present'] for item in attestation.runtime_code.values())==4
    assert all(attestation.runtime_code[str(path)]['present'] for path in paths)
    assert all(not attestation.runtime_code[str(path.parent/'__pycache__'/(path.stem+'.cpython-312.pyc'))]['present'] for path in paths)
    attestation.require_unchanged()


@pytest.mark.parametrize('cache_change',[False,True])
def test_source_or_cache_presence_drift_is_rejected(private_runtime,cache_change):
    root,versions,paths=private_runtime
    attestation=runtime.ServerRuntimeAttestation(root,versions)
    if cache_change:
        cache=paths[0].parent/'__pycache__/_dataclasses.cpython-312.pyc'
        cache.parent.mkdir();cache.write_bytes(b'new-cache-after-attestation')
    else:paths[0].write_bytes(b'changed-source-after-attestation')
    with pytest.raises(ValueError,match='publication_runtime_(presence|whole_bytes)_changed'):
        attestation.require_unchanged()


def test_aggregate_resource_bound_rejects_without_relaxing_per_file(current):
    authority,session,installed,_,files=current
    # Every member remains well below 2 MiB; exceeding the independently
    # configured aggregate still refuses issuance rather than dropping rows.
    authority.bounds=runtime.ResourceBounds(32,2*1024*1024,sum(map(len,files.values()))-1)
    with pytest.raises(ValueError,match='publication_resource_total_bound'):
        authority.resolve(session,'private-current',receipt_ref='receipt.json',receipt_sha256=installed.receipt.sha256)


def test_only_explicit_absent_runtime_path_may_have_no_cache_parent(tmp_path):
    from packages.kernel.decision_hub_kernel.application.research_publication_frozen_io48 import InterpreterAlias
    alias=InterpreterAlias(tmp_path/'alias',tmp_path/'target','0'*64,'private-test')
    missing=tmp_path/'Lib/statistics/__pycache__/statistics.cpython-312.pyc'
    alias.require_real(missing,allow_absent=True)
    with pytest.raises(FileNotFoundError):
        alias.require_real(missing)
