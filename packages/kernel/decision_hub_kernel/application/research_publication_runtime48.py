"""Managed original capture, frozen replay, issuer, and durable report worker.

Only normal registered installed sources may execute. Material contains native
rows and resource copies, never an executable source authority. Current and
retained installations have distinct descriptors with the same strict bounds.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime
import email
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from types import ModuleType
from uuid import uuid4

from sqlalchemy import inspect

from ..persistence.db import Database, utcnow
from ..persistence.publication_dependency_native import Limits, NativeCollector, canonical, sha
from ..persistence.frozen_publication_material import authenticated_material_session, verify_material_file
from ..persistence.research_source_snapshot import publication_source_snapshot_read_scope
from ..ports.research_source_snapshot import SOURCE_SNAPSHOT_READ_ABI
from ..ports.research_publication_bundle import FrozenPublicationBundleV1
from . import research_graph_history_versions as normal_history
from . import research_graph_recipe_v3 as normal_recipe
from .research_graph_binding import _ReadSourceGuard
from .research_publication_resources import OriginalPublicationResourceAuthority, ResourceBounds
from .research_publication_resource_stage import OwnedPublicationResourceStage, _owned_parent
from .research_publication_frozen_io48 import (
    FrozenConsumptionIO, InterpreterAlias, derive_normal_identity_attestations,
)
from .research_publication_frozen_reader import (
    OriginalPublicationRead, PreparedFrozenPublication, read_registered_original,
    read_registered_original_v2, frozen_bundle_from_verified_read,
)
from .research_publication_bundle_lifecycle import BundleArchive
from .research_publication_jobs48 import DurablePublicationLifecycle, PublicationBundleWorker
from .research_publication_execution import (
    PublicationExecutionCoordinator, PublicationExecutionStopped, active_execution, checkpoint, execution_stage,
)

RESOURCE_BOUNDS = ResourceBounds(2048, 2*1024*1024, 32*1024*1024)
# Native SQL accounting and serialized frozen material are separate from
# the existing 2 MiB file / 32 MiB resource authority limits. The measured
# original has 81,381,598 native bytes and 130,983,594 serialized bytes.
# These new server-owned bounds reject excess; they never truncate evidence.
NATIVE_LIMITS = Limits(4000,4000,128*1024*1024,2*1024*1024,32*1024*1024)
MATERIAL_BOUND = 192*1024*1024
BUNDLE_BOUND = 16*1024*1024
_HISTORY = "packages.kernel.decision_hub_kernel.application.research_graph_history_versions"
_QUALITY = "packages.kernel.decision_hub_kernel.application.research_quality_view"
_CONTEXT = "packages.kernel.decision_hub_kernel.application.research_context_selection_versions"
_RECIPE = "packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3"
_DB = "packages.kernel.decision_hub_kernel.persistence.db"
_SOURCE_SCOPE = "packages.kernel.decision_hub_kernel.persistence.research_source_snapshot"
_SOURCE_PORT = "packages.kernel.decision_hub_kernel.ports.research_source_snapshot"
_BINDING = "packages.kernel.decision_hub_kernel.application.research_graph_binding"
_RUNTIME_MODULES = (
    "pydantic._internal._dataclasses", "encodings.utf_16_be",
    "concurrent.futures.thread", "statistics",
)


_PUBLICATION_SUBPHASES71 = frozenset({
    "capture.resolve", "capture.abi", "capture.collector", "capture.selection",
    "capture.release", "capture.source_lease", "capture.reader", "capture.collect_sources",
    "capture.snapshot", "capture.authorize_frozen", "replay.source_guard", "replay.stage",
    "replay.loader", "replay.material", "replay.retained_verification", "replay.release",
    "replay.abi", "replay.source_modules", "replay.source_lease", "replay.reader",
    "replay.compare", "replay.runtime_exit", "replay.io", "replay.collector",
})
_PUBLICATION_SUBPHASE_CODES71 = frozenset({
    "publication_normal_source_guard_failed", "publication_normal_source_exit_changed",
    "publication_source_snapshot_abi_unsupported", "publication_full_frozen_original_changed",
    "publication_frozen_selected_installation_changed", "publication_current_descriptor_loader_missing",
    "publication_capture_selected_installation_changed", "publication_original_dependency_unresolved",
    "publication_normal_registry_not_configured", "publication_explicit_material_bound_exceeded",
})

def _publication_subphase_error71(error):
    classes = {PublicationExecutionStopped: "PublicationExecutionStopped", ValueError: "ValueError",
        TypeError: "TypeError", PermissionError: "PermissionError", TimeoutError: "TimeoutError",
        OSError: "OSError", RuntimeError: "RuntimeError", MemoryError: "MemoryError"}
    kind, code = classes.get(type(error), "unknown"), "unknown"
    if type(error) is PublicationExecutionStopped:
        reason = error.reason
        if reason in {"stop_requested", "compute_budget_exceeded", "save_budget_exceeded",
                "total_budget_exceeded", "renew_failed", "renew_limit_exceeded"}:
            code = "publication_execution_" + reason
    elif type(error) is ValueError and len(error.args) == 1:
        if type(error.args[0]) is str and error.args[0] in _PUBLICATION_SUBPHASE_CODES71:
            code = error.args[0]
    return {"exception_class": kind, "exception_code": code}

class _PublicationSubphaseTimings71:
    """Bounded metadata only; never a checkpoint, read, retry or authority."""
    def __init__(self, run_id, *, clock=time.monotonic):
        control = active_execution()
        if control is None:
            raise ValueError("publication_subphase_no_execution")
        self.clock, self.started = clock, clock()
        self.run_hash, self.generation = sha(run_id.encode()), control.generation
        self.stages, self.first_failure, self.terminal_failure = {}, None, None
        self.timing_unavailable, self.emitted = False, False

    def begin(self, label, kind):
        if label not in _PUBLICATION_SUBPHASES71 or kind not in {"call", "enter", "exit"}:
            raise ValueError("publication_subphase_unknown")
        row = self.stages.setdefault(label, {}).setdefault(kind,
            {"calls": 0, "failures": 0, "elapsed_ms": 0.0})
        row["calls"] += 1
        return self.clock()

    def end(self, label, kind, started, error):
        if label not in _PUBLICATION_SUBPHASES71 or kind not in {"call", "enter", "exit"}:
            self.timing_unavailable = True
            return
        if error is not None and self.first_failure is None:
            self.first_failure = {"stage": label, "operation": kind,
                **_publication_subphase_error71(error)}
        row = self.stages.get(label, {}).get(kind)
        if row is not None:
            row["failures"] += int(error is not None)
            if started is not None:
                duration = (self.clock()-started)*1000
                if not -float("inf") < duration < float("inf"):
                    raise ValueError("publication_subphase_clock_unavailable")
                row["elapsed_ms"] += max(0.0, duration)

    def emit(self):
        if self.emitted:
            return
        self.emitted = True
        control = active_execution()
        with control.lock:
            cancel_reason = control.reason
            if cancel_reason not in {None, "stop_requested", "compute_budget_exceeded",
                    "save_budget_exceeded", "total_budget_exceeded", "renew_failed", "renew_limit_exceeded"}:
                cancel_reason = "unknown"
            budgets = {"compute_seconds": control.compute_seconds,
                "save_seconds": control.save_seconds, "total_seconds": control.total_seconds}
            try:
                duration = (control.clock()-control.started)*1000
                if not -float("inf") < duration < float("inf"):
                    raise ValueError("publication_subphase_clock_unavailable")
                execution_elapsed_ms = max(0.0, duration)
            except BaseException:
                execution_elapsed_ms, self.timing_unavailable = None, True
        try:
            duration = (self.clock()-self.started)*1000
            if not -float("inf") < duration < float("inf"):
                raise ValueError("publication_subphase_clock_unavailable")
            producer_elapsed_ms = max(0.0, duration)
        except BaseException:
            producer_elapsed_ms, self.timing_unavailable = None, True
        value = {"schema_version": "publication-subphase-timings.v1", "run_hash": self.run_hash,
            "generation": self.generation, "outcome": "failed" if self.terminal_failure else "complete",
            "first_failure": self.first_failure, "terminal_failure": self.terminal_failure,
            "cancel_reason": cancel_reason, "original_budgets": budgets,
            "execution_elapsed_ms": execution_elapsed_ms,
            "producer_elapsed_ms": producer_elapsed_ms,
            "timing_unavailable": self.timing_unavailable, "stages": self.stages,
            "stage_times_may_overlap": True, "grants_permission": False}
        logging.getLogger(__name__).warning("publication_subphase_terminal %s",
            json.dumps(value, sort_keys=True, separators=(",", ":")))

def _publication_subphase_observer71(run_id):
    try:
        return _PublicationSubphaseTimings71(run_id)
    except BaseException:
        return None

@contextmanager
def _publication_subphase71(observer, label, kind="call"):
    started, failure = None, None
    if observer is not None:
        try:
            started = observer.begin(label, kind)
        except BaseException:
            observer.timing_unavailable = True
    try:
        yield
    except BaseException as error:
        failure = error
        raise
    finally:
        if observer is not None:
            try:
                observer.end(label, kind, started, failure)
            except BaseException:
                observer.timing_unavailable = True

class _PublicationSubphaseScope71:
    def __init__(self, observer, label, original):
        self.observer, self.label, self.original = observer, label, original

    def __enter__(self):
        with _publication_subphase71(self.observer, self.label, "enter"):
            return type(self.original).__enter__(self.original)

    def __exit__(self, kind, error, traceback):
        with _publication_subphase71(self.observer, self.label, "exit"):
            return type(self.original).__exit__(self.original, kind, error, traceback)

def _publication_subphase_scope71(observer, label, original):
    try:
        return _PublicationSubphaseScope71(observer, label, original) if observer is not None else original
    except BaseException:
        return original

def _publication_subphase_terminal71(observer, error=None, *, emit=False):
    try:
        if observer is not None:
            if error is not None:
                observer.terminal_failure = _publication_subphase_error71(error)
            if emit:
                observer.emit()
    except BaseException:
        pass  # Observation cannot replace the producer result or original exception.

class _ExecutionNativeCollector(NativeCollector):
    """Pure checkpoints on the original thread; never renew from a read."""
    def _before(self, *args):
        checkpoint("normal_capture")
        return super()._before(*args)

    def _charge(self, value):
        checkpoint("native_material")
        return super()._charge(value)

    def collect_sources(self, root, files):
        for spec in files:
            checkpoint("source_collection")
            super().collect_sources(root, (spec,))


def _current_source_snapshot_abi(authority, closure, *, frozen_descriptor=None):
    """Issued current or exact V3 retained V2; an ABI label alone grants nothing."""
    if authority._issued.get(closure.nonce) is not closure:
        raise ValueError("publication_resource_issuer_identity_changed")
    if frozen_descriptor is not None and (
            frozen_descriptor.authority is not authority
            or frozen_descriptor.closure is not closure
            or frozen_descriptor.resources.closure is not closure
            or frozen_descriptor.loader is None):
        raise ValueError("publication_resource_frozen_descriptor_changed")
    retained = None
    if closure.mode == "current-installation":
        manifest = json.loads(closure.active_binding_json)["manifest"]
    elif closure.mode == "retained-installation":
        # Resolve already authenticated this exact selected V3 installation.
        # Re-read its authority-bound index, never an arbitrary archive/name.
        selected_index = [entry for entry in closure.entries
            if entry.namespace == "selected-installation" and entry.logical_ref == "release.json"]
        if (len(selected_index) != 1
                or selected_index[0].physical_ref != "packages/kernel/research_recipe_versions/"
                    + closure.selected_directory + "/release.json"
                or selected_index[0].sha256 != closure.selected_index_sha256):
            return None
        index_raw = (normal_history._managed_read(selected_index[0].physical_ref)
            if frozen_descriptor is None else frozen_descriptor.resources.read_data(
                "selected-installation", "release.json"))
        if (len(index_raw) != selected_index[0].bytes
                or sha(index_raw) != selected_index[0].sha256):
            raise ValueError("publication_resource_normal_source_bytes_changed")
        index = json.loads(index_raw)
        if (index.get("schema_version") != "research-retained-recipe-release.v3"
                or index.get("graph_recipe_manifest", {}).get("schema_version")
                    != "research-graph-recipe-manifest.v3"):
            return None
        retained = {file["path"]: file["payload_hash"]
            for file in index["graph_recipe_manifest"]["source_files"]}
        # The current managed builder still verifies its complete AST/schema
        # closure and canonical registration. During replay its exact owned
        # current-root stage supplies non-code resources, never Root fallback
        # or an alleged-compatible selected manifest. Normal entry stays fresh.
        current_recipe = (normal_recipe if frozen_descriptor is None
            else frozen_descriptor.loader.load(_RECIPE))
        manifest = current_recipe.graph_recipe_manifest_v3().model_dump(mode="json")
    else:
        return None
    declared = {file["path"]: file["payload_hash"] for file in manifest["source_files"]}
    for name in (_SOURCE_SCOPE, _SOURCE_PORT, _BINDING):
        path = name.replace(".", "/") + ".py"
        current = [entry for entry in closure.entries
            if entry.namespace == "current-root" and entry.logical_ref == path]
        if (len(current) != 1 or path not in declared
                or current[0].sha256 != declared[path]):
            return None
        current_raw = authority.read_normal_source(closure, "current-root", path)
        if retained is not None:
            selected = [entry for entry in closure.entries
                if entry.namespace == "selected-installation" and entry.logical_ref == path]
            if (len(selected) != 1 or path not in retained
                    or selected[0].sha256 != retained[path]
                    or index["all_files"].get(path) != retained[path]):
                return None
            selected_raw = authority.read_normal_source(closure, "selected-installation", path)
            if selected_raw != current_raw:
                return None
    installed_abi=getattr(importlib.import_module(_SOURCE_SCOPE), "SOURCE_SNAPSHOT_READ_ABI", None)
    if (type(SOURCE_SNAPSHOT_READ_ABI) is not str
            or SOURCE_SNAPSHOT_READ_ABI != "publication-source-snapshot-read.v2"
            or type(installed_abi) is not str or installed_abi != SOURCE_SNAPSHOT_READ_ABI):
        return None
    return SOURCE_SNAPSHOT_READ_ABI


def exact_file(path, expected_sha=None):
    raw=Path(path).read_bytes()
    if expected_sha is not None and sha(raw)!=expected_sha:
        raise ValueError("publication_normal_resource_changed")
    return {"sha256":sha(raw),"bytes":len(raw),"present":True}


class ServerRuntimeAttestation:
    """Explicit Windows uv runtime contract; no request path or version input."""
    def __init__(self,root: Path, versions: dict):
        base=Path(sys._base_executable).absolute()
        logical=base.parent
        target=logical.resolve(strict=True)
        if (base.name!="python.exe" or logical.name!="cpython-3.12-windows-x86_64-none"
                or logical.parent.name!="uv-python" or versions.get("python")!=sys.version
                or versions.get("sqlite")!=sqlite3.sqlite_version):
            raise ValueError("publication_supported_server_runtime_required")
        self.interpreter=InterpreterAlias(logical,target,sha((target/"python.exe").read_bytes()),sys.version)
        self.entries={}
        packages={"pydantic":"pydantic","pydantic-core":"pydantic_core",
            "jsonschema":"jsonschema","referencing":"referencing",
            "SQLAlchemy":"sqlalchemy","PyYAML":"pyyaml"}
        if set(versions)!=set(packages)|{"python","sqlite"}:
            raise ValueError("publication_exact_runtime_version_inventory_required")
        for name,directory in packages.items():
            version=versions[name]
            if type(version) is not str or not re.fullmatch(r"[a-zA-Z0-9_.+!-]+",version):
                raise ValueError("publication_runtime_version_invalid")
            path=root/".venv/Lib/site-packages"/(directory+"-"+version+".dist-info")/"METADATA"
            self.interpreter.require_path(path)
            raw=path.read_bytes(); metadata=email.message_from_bytes(raw)
            if (metadata["Name"].lower().replace("-","_")!=name.lower().replace("-","_")
                    or metadata["Version"]!=version):
                raise ValueError("publication_exact_runtime_metadata_changed")
            self.entries[str(path)]={**exact_file(path),"kind":"distribution_metadata",
                "name":name,"version":version}
        path=logical/"python312.zip"
        self.interpreter.require_path(path,allow_absent=True)
        self.entries[str(path)]={"present":path.exists(),"bytes":path.stat().st_size if path.exists() else 0,
            "sha256":sha(path.read_bytes()) if path.exists() else None,"kind":"interpreter_search_zip"}
        self.runtime_code={}
        sources=[root/".venv/Lib/site-packages/pydantic/_internal/_dataclasses.py",
            logical/"Lib/encodings/utf_16_be.py",logical/"Lib/concurrent/futures/thread.py",
            logical/"Lib/statistics.py"]
        for name,path in zip(_RUNTIME_MODULES,sources,strict=True):
            actual=self.interpreter.require_path(path)
            module=importlib.import_module(name)
            if Path(module.__file__).resolve(strict=True)!=actual.resolve(strict=True):
                raise ValueError("publication_exact_normal_runtime_module_changed")
            self.runtime_code[str(path)]=exact_file(actual)
            cache=path.parent/"__pycache__"/(path.stem+".cpython-312.pyc")
            actual_cache=self.interpreter.require_path(cache,allow_absent=True)
            # A bytecode cache is not a business original. Cold servers may
            # have no cache. Presence and whole bytes are fixed for this scope,
            # while the normal runtime's source is always authenticated.
            self.runtime_code[str(cache)]=(exact_file(actual_cache) if actual_cache.exists()
                else {"sha256":None,"bytes":0,"present":False})
        self.require_unchanged()

    def require_unchanged(self):
        self.interpreter.require_unchanged()
        for path,item in {**self.entries,**self.runtime_code}.items():
            actual=self.interpreter.require_path(path,allow_absent=item["present"] is False)
            if actual.exists()!=item["present"]:
                raise ValueError("publication_runtime_presence_changed")
            if item["present"] and exact_file(actual)!= {k:item[k] for k in ("sha256","bytes","present")}:
                raise ValueError("publication_runtime_whole_bytes_changed")

    def io_files(self):
        result={p:{k:v[k] for k in ("sha256","bytes","present")} for p,v in self.entries.items()}
        result.update(self.runtime_code)
        exe=self.interpreter.resolved_root/"python.exe"
        result[str(exe)]=exact_file(exe,self.interpreter.executable_sha256)
        for path,item in list(result.items()):
            actual=str(self.interpreter.require_path(path,allow_absent=item["present"] is False))
            if actual!=path:
                result[actual]=dict(item)
        return result


class FrozenDescriptor:
    def __init__(self,authority,closure,resources):
        self.authority,self.closure,self.resources=authority,closure,resources
        self.logical_root=authority.root
        self.scope_nonce=uuid4().hex
        self.mode=closure.mode
        self.selected_root=None; self.io=None; self.loader=None; self.shared={}
        self.authorization_diagnostic_sources78={}
        self.first_authorization_rejection78=None
        self.authorization_rejection_emitted78=False

    def bind_authorization_diagnostic_sources78(self,stage_manager):
        """Map existing authenticated source payloads to their compile names.

        This is diagnostic metadata only. It reads no new files and changes no
        source, I/O, issuer or permission description. Ambiguity stays unknown.
        """
        if (self.authority._issued.get(self.closure.nonce) is not self.closure
                or self.resources.closure is not self.closure
                or stage_manager.authority is not self.authority
                or stage_manager.resources is not self.resources
                or stage_manager.description is None
                or stage_manager.description.closure_sha256!=self.closure.closure_sha256
                or len(self.closure.entries)>RESOURCE_BOUNDS.max_resources):
            return
        result={};ambiguous=set()
        for item in self.closure.entries:
            if item.purpose!="source_attestation":continue
            pair=(item.namespace,item.logical_ref)
            raw=stage_manager.codes.get(pair)
            staged=stage_manager.files.get(pair)
            if (type(raw) is not bytes or len(raw)!=item.bytes or staged is None
                    or staged[1:]!=(item.sha256,item.bytes)
                    or sha(raw)!=item.sha256):
                continue
            description={"namespace":item.namespace,"logical_ref":item.logical_ref,
                "source_sha256":item.sha256}
            for path in (staged[0],self.authority.source_path(
                    self.closure,item.namespace,item.logical_ref)):
                filename=_authorization_diagnostic_path78(path)
                if filename is None:continue
                prior=result.setdefault(filename,description)
                if prior!=description:ambiguous.add(filename)
        self.authorization_diagnostic_sources78={k:v for k,v in result.items() if k not in ambiguous}

    def record_authorization_rejection78(self,relative,error):
        if (self.first_authorization_rejection78 is not None or type(error) is not ValueError
                or error.args!=("frozen_io_undeclared_authorization_resource",)
                or self.io is None or self.io.active is not True):
            return
        path=_authorization_diagnostic_path78(self.logical_root/relative) if type(relative) is str and len(relative)<=2048 else None
        if path is not None and (path in self.io.sources or path in self.io.authorization_files):
            return
        reference=_authorization_diagnostic_text78(relative,2048)
        valid=(type(relative) is str and 0<len(relative)<=2048
            and "\\" not in relative and ":" not in relative
            and not relative.startswith("/")
            and all(ord(char)>=32 and ord(char)!=127 for char in relative)
            and all(part not in {"",".",".."} for part in relative.split("/")))
        reference["missing_ref"]=relative if valid else None
        reference["identity"]="exact_normal_relative" if valid else "UNKNOWN"
        callers=[];frame=None;examined=0;truncated=False
        try:
            # record -> current_authorization_read -> the actual direct caller.
            frame=sys._getframe(2)
            while frame is not None and examined<32:
                examined+=1
                if not callers:
                    callers.append(_authorization_diagnostic_caller78(
                        self,frame,"direct"))
                if frame.f_code.co_name not in {
                    "current_authorization_read","record_authorization_rejection78",
                    "_managed_read","_managed_current_authorization_read",
                    "read_authorization","authorization","read_source_bytes","read_normal_source",
                }:
                    callers.append(_authorization_diagnostic_caller78(self,frame,"business"))
                    break
                frame=frame.f_back
            truncated=frame is not None and examined==32 and len(callers)<2
        finally:
            del frame
        control=active_execution()
        run_hash=sha(self.closure.run_id.encode("utf-8"))
        generation=(control.generation if control is not None
            and control.run_sha256==run_hash and type(control.generation) is int
            and control.generation>=1 else None)
        self.first_authorization_rejection78={
            "schema_version":"publication-frozen-authorization-rejection.v1",
            "run_hash":run_hash,"generation":generation,
            "execution_context":"normal_publication" if generation is not None else "UNKNOWN",
            "mode":self.mode if self.mode in {"current-installation","retained-installation"} else "UNKNOWN",
            "selected_index_sha256":self.closure.selected_index_sha256,
            "exception_code":"frozen_io_undeclared_authorization_resource",
            "first_reference":reference,"callers":callers,
            "frames_examined":examined,"caller_search_truncated":truncated,
            "grants_permission":False,"missing_resource_read":False,
        }

    def current_authorization_read(self,relative,original_reader):
        index="packages/kernel/research_recipe_versions/"+self.closure.selected_directory+"/release.json"
        if self.mode=="retained-installation" and relative==index:
            raw=self.resources.read_data("selected-installation","release.json")
            if sha(raw)!=self.closure.selected_index_sha256:
                raise ValueError("publication_selected_frozen_index_changed")
            return raw
        lookup = tuple(item for item in self.closure.registration_lookup_indexes
            if item.relative_ref == relative)
        if lookup:
            entries = tuple(item for item in self.closure.entries
                if item.namespace == "current-root" and item.logical_ref == relative
                and item.physical_ref == relative and item.purpose == "data_resource")
            if (len(lookup) != 1 or len(entries) != 1
                    or entries[0].sha256 != lookup[0].expected_sha256):
                raise ValueError("publication_frozen_lookup_index_description_changed")
            raw = self.resources.read_data("current-root", relative)
            if len(raw) != entries[0].bytes or sha(raw) != lookup[0].expected_sha256:
                raise ValueError("publication_frozen_lookup_index_bytes_changed")
            return raw
        try:
            return original_reader() if self.io is None else self.io.read_authorization(
                self.logical_root/relative,original_reader)
        except BaseException as error:
            try:self.record_authorization_rejection78(relative,error)
            except BaseException:pass  # Diagnostics never replace the original refusal.
            raise

    def release_bytes(self,registration):
        if (self.mode!="retained-installation" or registration.directory!=self.closure.selected_directory
                or registration.index_hash!=self.closure.selected_index_sha256):
            raise ValueError("publication_exact_selected_installation_changed")
        return self.resources.read_data("selected-installation","release.json")

    def selected_bytes(self,name):
        if self.mode!="retained-installation":
            raise ValueError("publication_current_is_not_retained_index")
        return (self.authority.read_normal_source(self.closure,"selected-installation",name)
            if name.endswith(".py") else self.resources.read_data("selected-installation",name))

    def current_bytes(self,name):
        if self.mode!="current-installation":
            raise ValueError("publication_current_descriptor_not_selected")
        return (self.authority.read_normal_source(self.closure,"current-root",name)
            if name.endswith(".py") else self.resources.read_data("current-root",name))

    def shared_original(self,name):
        if name==_DB:
            return importlib.import_module(name)
        if name!=_CONTEXT:
            raise ValueError("publication_unregistered_shared_module")
        if name not in self.shared:
            self.shared[name]=self.loader.load_shared_context(name)
        return self.shared[name]


def registered_host(authority,closure,descriptor):
    if getattr(normal_history,"FROZEN_PUBLICATION_HOST_CONTRACT",None)!="publication-frozen-host.v1":
        raise ValueError("publication_registered_frozen_host_not_installed")
    if (closure.mode=="current-installation" and
            getattr(normal_recipe,"FROZEN_PUBLICATION_CURRENT_CONTRACT",None)!="publication-current-installation.v1"):
        raise ValueError("publication_registered_current_descriptor_not_installed")
    raw=authority.read_normal_source(closure,"current-root",_HISTORY.replace(".","/")+".py")
    alias="_decision_hub_frozen_host_"+descriptor.scope_nonce
    if alias in sys.modules:
        raise ValueError("publication_fresh_host_scope_required")
    host=ModuleType(alias); host.__file__=str(authority.root/(_HISTORY.replace(".","/")+".py"))
    host.__package__="packages.kernel.decision_hub_kernel.application"
    host.__dict__["_FROZEN_DESCRIPTOR"]=descriptor
    sys.modules[alias]=host
    try:
        exec(compile(raw,host.__file__,"exec"),host.__dict__)
        host.configure_retained_recipe_registry_from_validated_release(
            receipt_path=authority.root/closure.receipt_ref,receipt_sha256=closure.receipt_sha256)
    except BaseException:
        sys.modules.pop(alias,None)
        raise
    return host


def _current_loader_type(host,descriptor, *, current_policy_source_snapshot=False):
    """Scope current authorization-dependent leaves to the registered host.

    Bytes remain current managed source authority. Persisted original code is
    still loaded only by the host's exact registered retained installation.
    ORM/contract leaves retain normal shared identity, avoiding duplicate Base.
    """
    prefix="packages.kernel.decision_hub_kernel.application."
    private_names={_QUALITY,_CONTEXT,_RECIPE,_SOURCE_SCOPE,_SOURCE_PORT}|{
        prefix+name for name in (
            "research_graph_binding","research_graph_recipe","research_graph_dispatch","commit",
            "research_recipe_read_scope","research_provider_usage","research_quality_sidecar",
            "research_context_selection","research_context_selection_v4",
        )}
    # Only authenticated V2 frozen material uses the same private guard/lease.
    # Old retained V1 and default callers keep their normal shared policy reader.
    if current_policy_source_snapshot:
        private_names.add(prefix+"research_policy_checker")
    namespaces={"packages.kernel.decision_hub_kernel",
        "packages.kernel.decision_hub_kernel.application","packages.kernel.decision_hub_kernel.persistence",
        "packages.kernel.decision_hub_kernel.decision","packages.kernel.decision_hub_kernel.ports"}
    contracts="packages.contracts_py.decision_hub_contracts"
    # Only current DTO/validator modules crossing normal original-reader ports.
    # The host's inner retained release keeps its own original private contracts.
    shared_contracts={contracts}|{contracts+"."+name for name in (
        "models","generated","binding_contract_base","policy_contract_base",
        "generated.provider_input","generated.research_context_execution",
        "generated.research_context_selection_v3","generated.research_context_selection_v4",
        "generated.research_policy",
    )}
    class CurrentLoader(host._PrivateRelease):
        def load(self,name):
            if name==_HISTORY:
                parent,_,child=name.rpartition(".")
                setattr(super().load(parent),child,host)
                return host
            if descriptor.mode=="retained-installation" and name in shared_contracts:
                relative=self.paths.get(name)
                if relative is None or relative not in self.payloads:
                    raise ValueError("publication_current_contract_source_missing")
                module=importlib.import_module(name)
                if Path(module.__file__).read_bytes()!=self.payloads[relative]:
                    raise ValueError("publication_current_contract_source_changed")
                parent,_,child=name.rpartition(".")
                parent_module=(self.load(parent) if parent in shared_contracts else super().load(parent))
                setattr(parent_module,child,module)
                return module
            if (descriptor.mode=="retained-installation"
                    and name.startswith("packages.kernel.decision_hub_kernel.")
                    and name not in private_names|namespaces):
                module=importlib.import_module(name)
                parent,_,child=name.rpartition(".")
                setattr(super().load(parent),child,module)
                return module
            return super().load(name)
        def load_shared_context(self,name):
            return super().load(name)
    return CurrentLoader


def require_current_loader_type_implementation80():
    """Return the normal module's original loader builder, sealed before use."""
    original, original_code, guard, guard_code = _CURRENT_LOADER_IMPLEMENTATION80
    if (_current_loader_type is not original
            or getattr(_current_loader_type,"__code__",None) is not original_code
            or require_current_loader_type_implementation80 is not guard
            or getattr(require_current_loader_type_implementation80,"__code__",None) is not guard_code):
        raise ValueError("publication_current_loader_implementation_changed")
    return original


_CURRENT_LOADER_IMPLEMENTATION80 = (
    _current_loader_type, _current_loader_type.__code__,
    require_current_loader_type_implementation80,
    require_current_loader_type_implementation80.__code__,
)


def _publication_compare_value77(value):
    """Bounded primitive whole digest; containers retain no body or parameters."""
    kind=type(value)
    if kind not in {str,int,float,bool,type(None),dict,list,tuple}:
        return {"type":"unsupported","digest_unavailable":True}
    result={"type":kind.__name__}
    limit=2*1024*1024
    if kind is str:
        result.update(size=len(value),size_unit="characters")
        if len(value)>limit//4:
            return {**result,"digest_unavailable":True}
        raw=value.encode("utf-8")
    elif kind in {dict,list,tuple}:
        result.update(size=len(value),size_unit="items")
        # This is the existing native columns/rows digest, not a whole dict SHA.
        if (kind is dict and len(value)==4 and set(value)=={"columns","rows","row_count","sha256"}
                and type(value["sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}",value["sha256"])):
            result["native_columns_rows_sha256"]=value["sha256"]
        return {**result,"digest_unavailable":True}
    else:
        if kind is int and value.bit_length()>1024:
            return {**result,"digest_unavailable":True}
        raw=json.dumps(value,allow_nan=False).encode("ascii")
    if len(raw)>limit:return {**result,"digest_unavailable":True}
    return {**result,"bytes":len(raw),"sha256":sha(raw)}


def _publication_compare_failure77(closure,original,observed,actual,replayed,expected):
    """One bounded stderr observation after strict IO; grants no permission."""
    control=active_execution()
    metadata={"schema_version":"publication-frozen-compare-firstdiff.v1",
        "run_hash":sha(closure.run_id.encode()),"generation":None if control is None else control.generation,
        "process_id":os.getpid(),"mode":closure.mode,"selected_index_sha256":closure.selected_index_sha256,
        "closure_sha256":closure.closure_sha256,"grants_permission":False,
        "query_map_failed":original!=observed,"unresolved_failed":bool(actual["unresolved"]),
        "native_changes_failed":bool(actual["native_changes"]),"original_read_failed":replayed!=expected,
        "original_queries":len(original),"observed_queries":len(observed),
        "missing_queries":sum(key not in observed for key in original),
        "extra_queries":sum(key not in original for key in observed),
        "unresolved_count":len(actual["unresolved"]),"native_changes":actual["native_changes"],
        "dto_type_same":type(replayed) is type(expected),
        "dto_field_equal":{key:getattr(replayed,key)==getattr(expected,key) for key in (
            "quality_json","event_id","artifact_id","reader_source_sha256")}}
    core=dict(metadata)
    try:
        missing=next((key for key in original if key not in observed),None)
        extra=next((key for key in observed if key not in original),None)
        if missing is not None or extra is not None:
            metadata["first_query"]={"missing_query_id":missing if type(missing) is str and re.fullmatch(r"[a-f0-9]{64}",missing) else None,
                "extra_query_id":extra if type(extra) is str and re.fullmatch(r"[a-f0-9]{64}",extra) else None}
        elif metadata["query_map_failed"]:
            fields=("statement","typed_parameters","result","table","range_statement",
                "range_typed_parameters","range_result","member_identities","empty_range")
            for key,left in original.items():
                field=next((name for name in fields if left[name]!=observed[key][name]),None)
                if field is not None:
                    metadata["first_query"]={"query_id":key if type(key) is str and re.fullmatch(r"[a-f0-9]{64}",key) else None,"field":field,
                        "expected":_publication_compare_value77(left[field]),
                        "observed":_publication_compare_value77(observed[key][field])}
                    break
        for field,equal in metadata["dto_field_equal"].items():
            if not equal:
                metadata["first_dto"]={"field":field,
                    "expected":_publication_compare_value77(getattr(expected,field)),
                    "observed":_publication_compare_value77(getattr(replayed,field))}
                break
    except BaseException:
        metadata={**core,"first_diff_unavailable":True}
    try:
        raw=json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
        if len(raw)>8064:
            metadata={**core,"first_diff_unavailable":True}
            raw=json.dumps(metadata,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
        if len(raw)<=8064:
            logging.getLogger(__name__).warning("publication_frozen_compare_failed %s",raw.decode("utf-8"))
    except BaseException:
        pass  # A diagnostic failure never replaces the original comparison.


def _authorization_diagnostic_path78(path):
    value=os.fspath(path)
    if type(value) is not str or len(value)>4096:return None
    if value.startswith("\\\\?\\"):value=value[4:]
    return os.path.normcase(os.path.abspath(value))


def _authorization_diagnostic_text78(value,limit):
    if type(value) is not str:
        return {"characters":None,"sha256":None,"digest_unavailable":True}
    if len(value)>limit:
        return {"characters":len(value),"sha256":None,"digest_unavailable":True}
    return {"characters":len(value),"sha256":sha(value.encode("utf-8")),"digest_unavailable":False}


def _authorization_diagnostic_caller78(descriptor,frame,role):
    code=frame.f_code
    filename=_authorization_diagnostic_text78(code.co_filename,4096)
    normalized=_authorization_diagnostic_path78(code.co_filename)
    known=(descriptor.authorization_diagnostic_sources78.get(normalized)
        if os.path.isabs(code.co_filename) else None)
    name=_authorization_diagnostic_text78(code.co_name,256)
    result={"role":role,"source_identity":"authenticated_compile_filename" if known else "UNKNOWN",
        "filename_sha256":filename["sha256"],"filename_characters":filename["characters"],
        "function":code.co_name if len(code.co_name)<=256 else None,
        "function_sha256":name["sha256"],"line":frame.f_lineno}
    if known:result.update(known)
    return result


def _emit_authorization_rejection78(descriptor):
    """Emit once only after strict I/O exit; no new diagnostic file or grant."""
    if (descriptor.io is None or descriptor.io.active is not False
            or descriptor.first_authorization_rejection78 is None
            or descriptor.authorization_rejection_emitted78):
        return False
    descriptor.authorization_rejection_emitted78=True
    try:
        metadata=descriptor.first_authorization_rejection78
        raw=json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
        if len(raw)>8*1024:
            metadata={k:metadata[k] for k in ("schema_version","run_hash","generation","execution_context",
                "mode","selected_index_sha256","exception_code","frames_examined","caller_search_truncated",
                "grants_permission","missing_resource_read")}
            first=descriptor.first_authorization_rejection78["first_reference"]
            metadata["first_reference"]={k:first[k] for k in ("characters","sha256","digest_unavailable")}
            metadata["first_reference"].update(missing_ref=None,identity="UNKNOWN_output_bound")
            metadata["callers"]=[{k:caller[k] for k in ("role","filename_sha256","filename_characters","function_sha256","line")}
                | {"source_identity":"UNKNOWN_output_bound"}
                for caller in descriptor.first_authorization_rejection78["callers"]]
            metadata["full_metadata_unavailable"]="output_bound"
            raw=json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
        if len(raw)>8*1024:return False
        logging.getLogger(__name__).warning("publication_frozen_authorization_rejected %s",raw.decode("utf-8"))
        return True
    except BaseException:
        return False  # Logging failure never replaces the original rejection.


def frozen_replay(authority,closure,resources,capture,expected,stage_parent,*,subphase_observer=None):
    """One complete frozen consume; no live SQL or business data supplement."""
    if "retained_source_snapshot_v2" in capture:
        if (closure.mode != "retained-installation"
                or type(capture.get("source_snapshot_v2")) is not dict
                or type(capture["retained_source_snapshot_v2"]) is not dict):
            raise ValueError("publication_retained_source_snapshot_declaration_invalid")
        # Declared private V2 never becomes an undeclared or legacy read.
        # The original immutable DTO only checks shape; the original private
        # factory still checks this exact snapshot against fresh held bytes.
        snapshot_type = importlib.import_module(_SOURCE_PORT).PublicationSourceSnapshotV1
        snapshot_type.model_validate(capture["source_snapshot_v2"])
        snapshot_type.model_validate(capture["retained_source_snapshot_v2"])
    with _publication_subphase71(subphase_observer, "replay.source_guard"):
        guard=_ReadSourceGuard.capture()
        if guard is None or not guard.matches():
            raise ValueError("publication_normal_source_guard_failed")
    owned_history_requested=(closure.mode=="retained-installation"
        and "source_snapshot_v2" in capture and "retained_source_snapshot_v2" in capture)
    descriptor=None if owned_history_requested else FrozenDescriptor(authority,closure,resources)
    with _publication_subphase71(subphase_observer, "replay.loader"):
        manifest=(json.loads(closure.active_binding_json)["manifest"] if closure.mode=="current-installation"
            else json.loads(resources.read_data("selected-installation","release.json"))["graph_recipe_manifest"])
        runtime=ServerRuntimeAttestation(authority.root,manifest["runtime_versions"])
    host=None;loader=None;io=None;history_owner=None
    try:
        stage_manager=OwnedPublicationResourceStage(parent=stage_parent,authority=authority,resources=resources,
                bounds=RESOURCE_BOUNDS)
        with _publication_subphase_scope71(subphase_observer,"replay.stage",stage_manager) as staged:
            with _publication_subphase71(subphase_observer, "replay.loader"):
                if owned_history_requested:
                    # Only the normal history factory decides whether these
                    # authenticated Source bytes implement its Owner contract.
                    # A historical Source keeps its complete original path.
                    history_owner=normal_history.create_publication_frozen_host_owner(
                        authority,closure,resources,stage_manager)
                if history_owner is not None:
                    descriptor=history_owner.descriptor
                    host=history_owner.host
                    loader=history_owner.create_current_loader(capture=capture)
                else:
                    if descriptor is None:
                        descriptor=FrozenDescriptor(authority,closure,resources)
                    descriptor.selected_root=staged.selected_root
                    try:descriptor.bind_authorization_diagnostic_sources78(stage_manager)
                    except BaseException:pass  # Unknown caller identity is not permission.
                    host=registered_host(authority,closure,descriptor)
                    payloads={item.logical_ref:authority.read_normal_source(closure,item.namespace,item.logical_ref)
                        for item in closure.entries if item.namespace=="current-root" and item.purpose=="source_attestation"}
                    registration=host._Registration("frozen_current_"+descriptor.scope_nonce,"0"*64,"0"*64,"0"*64)
                    CurrentLoader=_current_loader_type(host,descriptor,
                        current_policy_source_snapshot=(descriptor.mode=="retained-installation"
                            and "source_snapshot_v2" in capture))
                    loader=CurrentLoader(registration,payloads,staged.current_root,v3=True)
            # Current model modules use their own normal db.py / Base. A
            # shared current Base would register the same auxiliary tables
            # twice when private quality imports its complete ORM closure.
            if history_owner is None:
                loader.shared_modules=frozenset()
                descriptor.loader=loader
            with _publication_subphase71(subphase_observer, "replay.loader"):
                if descriptor.mode=="current-installation":
                    current_recipe=loader.load(_RECIPE)
                    if getattr(current_recipe,"_FROZEN_PUBLICATION_CURRENT",None) is not descriptor:
                        raise ValueError("publication_current_descriptor_loader_missing")
                    current_recipe.configure_graph_recipe_v3_from_validated_release(
                        receipt_path=staged.current_root/closure.receipt_ref,receipt_sha256=closure.receipt_sha256)
                quality=loader.load(_QUALITY)
            root=authority.root;binding=normal_history._REGISTRY_BINDING
            sources={str(authority.source_path(closure,item.namespace,item.logical_ref)):
                    {"sha256":item.sha256,"bytes":item.bytes,"present":True}
                for item in closure.entries if item.purpose=="source_attestation"}
            authorization={str(root/ref):exact_file(root/ref,digest)
                for ref,digest in authority.authorization_sources(closure)}
            # Exactly the owned stage is admitted. Every source was obtained
            # from the current normal registration before stage construction.
            stage_files={str(p):{"sha256":digest,"bytes":size,"present":True}
                for p,digest,size in stage_manager.files.values()}
            with _publication_subphase_scope71(subphase_observer,"replay.material",authenticated_material_session(stage_parent,capture)) as session:
                with _publication_subphase71(subphase_observer, "replay.material"):
                    native=session.connection().connection.driver_connection
                    db=Path(native.execute("PRAGMA database_list").fetchone()[2])
                collector=_ExecutionNativeCollector(session,limits=NATIVE_LIMITS,trace_slot_owned=True)
                with ExitStack() as read_boundary:
                    normal_identity=None
                    if descriptor.mode=="retained-installation" and "source_snapshot_v2" in capture:
                        read_boundary.enter_context(_publication_subphase_scope71(
                            subphase_observer,"replay.collector",collector))
                        normal_source_lease=read_boundary.enter_context(_publication_subphase_scope71(
                            subphase_observer,"replay.source_lease",publication_source_snapshot_read_scope(
                                session,run_id=closure.run_id,guard_type=_ReadSourceGuard,
                                collector=collector,
                                declared_sources=tuple((item.logical_ref,item.sha256)
                                    for item in closure.entries if item.namespace=="current-root"),
                                expected_snapshot=capture["source_snapshot_v2"],
                                checkpoint=lambda:checkpoint("frozen_replay"))))
                        normal_identity=derive_normal_identity_attestations(
                            authority,closure,normal_source_lease)
                    io=FrozenConsumptionIO(root=root,sources=sources,authorization=authorization,
                        staged=stage_files,runtime=runtime.io_files(),native_database=db,
                        interpreter=runtime.interpreter,normal_identity=normal_identity,native_connection=native)
                    if history_owner is None:
                        descriptor.io=io
                    else:
                        history_owner.bind_io(io)
                    read_boundary.enter_context(_publication_subphase_scope71(
                        subphase_observer,"replay.io",io))
                    if descriptor.mode!="retained-installation" or "source_snapshot_v2" not in capture:
                        # Preserve oldcurrent / undeclared V1 IO-before-collector order.
                        read_boundary.enter_context(_publication_subphase_scope71(
                            subphase_observer,"replay.collector",collector))
                    with _publication_subphase71(subphase_observer, "replay.retained_verification"):
                        release_scope=nullcontext()
                        if descriptor.mode=="retained-installation":
                            selected=host._original_recipe_registration(session,closure.run_id)
                            if (selected is None or selected.directory!=closure.selected_directory
                                    or selected.index_hash!=closure.selected_index_sha256):
                                raise ValueError("publication_frozen_selected_installation_changed")
                            verified=host._verified_release_uncached(selected)
                            release_scope=host._verified_release_read_scope(
                                session,closure.run_id,selected,verified)
                    # Reuse only the host's immutable originals within this
                    # physical read. Its uncached entry/exit and fresh native
                    # selection checks remain mandatory; active keeps its path.
                    with _publication_subphase_scope71(subphase_observer,"replay.release",release_scope):
                        at=datetime.fromisoformat(json.loads(expected.quality_json)["as_of"].replace("Z","+00:00"))
                        if (descriptor.mode=="current-installation"
                                or "source_snapshot_v2" in capture):
                            with _publication_subphase71(subphase_observer, "replay.abi"):
                                if (_current_source_snapshot_abi(authority,closure,
                                        frozen_descriptor=descriptor)!=SOURCE_SNAPSHOT_READ_ABI
                                        or "source_snapshot_v2" not in capture):
                                    raise ValueError("publication_source_snapshot_abi_unsupported")
                            with _publication_subphase71(subphase_observer, "replay.source_modules"):
                                source_scope=loader.load(_SOURCE_SCOPE)
                                binding_reader=loader.load(_BINDING)
                                port=loader.load(_SOURCE_PORT)
                                if source_scope.SOURCE_SNAPSHOT_READ_ABI!=port.SOURCE_SNAPSHOT_READ_ABI:
                                    raise ValueError("publication_source_snapshot_abi_unsupported")
                            with _publication_subphase_scope71(subphase_observer,"replay.source_lease",source_scope.publication_source_snapshot_read_scope(
                                    session,run_id=closure.run_id,guard_type=binding_reader._ReadSourceGuard,
                                    collector=collector,
                                    declared_sources=tuple((item.logical_ref,item.sha256) for item in closure.entries
                                        if item.namespace=="current-root"),
                                    expected_snapshot=capture["source_snapshot_v2"],
                                    checkpoint=lambda:checkpoint("frozen_replay"))) as source_lease:
                                with _publication_subphase71(subphase_observer, "replay.reader"):
                                    private_source_scope=(host.publication_retained_source_snapshot_read_scope(
                                        session,run_id=closure.run_id,collector=collector,current_source_lease=source_lease,
                                        expected_snapshot=capture["retained_source_snapshot_v2"],
                                        checkpoint=lambda:checkpoint("frozen_replay"))
                                        if "retained_source_snapshot_v2" in capture else nullcontext())
                                    with private_source_scope as private_source_lease:
                                        shared_original_scope=(history_owner.shared_original_read_scope(
                                            session,run_id=closure.run_id,collector=collector,
                                            root_lease=normal_source_lease,current_private_lease=source_lease,
                                            retained_private_lease=private_source_lease)
                                            if history_owner is not None else nullcontext())
                                        with shared_original_scope:
                                            replayed=read_registered_original_v2(session,closure.run_id,
                                                as_of=at,reader_module=quality,source_lease=source_lease)
                        else:
                            # Existing retained V1 material has no V2 lease declaration.
                            # Preserve its original full reader; declared V2 never falls back.
                            with _publication_subphase71(subphase_observer, "replay.reader"):
                                replayed=read_registered_original(session,closure.run_id,
                                    as_of=at,reader_module=quality)
                with _publication_subphase71(subphase_observer, "replay.collector"):
                    actual=collector.snapshot()
            with _publication_subphase71(subphase_observer, "replay.compare"):
                fields=("statement","typed_parameters","result","table","range_statement",
                    "range_typed_parameters","range_result","member_identities","empty_range")
                original={q["query_id"]:{k:q[k] for k in fields} for q in capture["queries"]}
                observed={q["query_id"]:{k:q[k] for k in fields} for q in actual["queries"]}
                if original!=observed or actual["unresolved"] or actual["native_changes"] or replayed!=expected:
                    try:
                        _publication_compare_failure77(closure,original,observed,actual,replayed,expected)
                    except BaseException:
                        pass  # Preserve the original failure and strict comparison order.
                    raise ValueError("publication_full_frozen_original_changed")
            with _publication_subphase71(subphase_observer, "replay.runtime_exit"):
                runtime.require_unchanged()
                if not guard.matches():
                    raise ValueError("publication_normal_source_exit_changed")
            audit={"schema_version":"publication-frozen-consumption-audit.v1",
                "python_open_events":io.opens,"denied":io.denied,
                "resource_requests":resources.requests,"runtime":{
                    "entries":runtime.entries,"code":runtime.runtime_code,
                    "interpreter":{k:str(v) if isinstance(v,Path) else v for k,v in vars(runtime.interpreter).items()}},
                "complete_distinct_queries":len(observed),"native_rows":len(actual["rows"]),
                "scope":"python-open/sqlite-connect/native-sql", "os_sandbox":False}
            return replayed,audit,runtime
    except BaseException:
        # The strict I/O scope has exited. Diagnose only its first rejection
        # from already authenticated in-memory descriptions, never file bytes.
        try:
            _emit_authorization_rejection78(descriptor)
            if io is not None and not io.active and io.denied:
                first=io.denied[0]
                categories={"root_data_fallback","undeclared_file","unknown_sqlite_descriptor",
                    "native_database_must_be_readonly","native_database_uri_required",
                    "unknown_native_database","unknown_numeric_descriptor",
                    "unknown_file_descriptor","consumption_write_forbidden"}
                category=first.get("category")
                path=first.get("path")
                metadata={"schema_version":"publication-frozen-io-rejection.v1",
                    "run_hash":sha(closure.run_id.encode()),
                    "category":category if category in categories else "unknown",
                    "captured_resource":False}
                if type(path) is str:
                    metadata["path_sha256"]=sha(path.encode())
                    root=os.path.normcase(os.path.abspath(os.fspath(authority.root)))
                    family="non_root_or_unknown"
                    if path.startswith(root+os.sep):
                        first_part=path[len(root)+1:].split(os.sep,1)[0]
                        family=first_part if first_part in {"packages","contracts","infra","data","apps","artifacts"} else "other_root"
                    metadata["resource_family"]=family
                    if (authority._issued.get(closure.nonce) is closure
                            and resources.closure is closure):
                        for item in closure.entries:
                            physical=os.path.normcase(os.path.abspath(os.fspath(authority.root/item.physical_ref)))
                            if physical==path:
                                metadata.update(captured_resource=True,logical_ref=item.logical_ref,
                                    purpose=item.purpose,namespace=item.namespace)
                                break
                logging.getLogger(__name__).warning("publication_frozen_io_rejected %s",
                    json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(",",":")))
        except BaseException:
            pass  # Diagnostics never replace the original failure.
        raise
    finally:
        if history_owner is not None:
            history_owner.close()
        else:
            if host is not None:
                for private in host._LOADED.values():
                    private.discard()
                sys.modules.pop(host.__name__,None)
            if loader is not None:
                loader.discard()


class ManagedFrozenPublicationPreparer:
    """Opaque issuer owned by this normal server; no diagnostic reconstruction."""
    def __init__(self,root: Path,stage_parent: Path):
        self.root=root;self.stage_parent=_owned_parent(stage_parent)
        self.authority=OriginalPublicationResourceAuthority(trusted_root=root,bounds=RESOURCE_BOUNDS)
        self._issued={}
        self._retry_permits49={}

    def reader_abi_qualification(self,session,*,run_id: str):
        """RO current source qualification; no retry, model, save or ACK grant."""
        binding=normal_history._REGISTRY_BINDING
        if binding is None:
            return None
        closure=self.authority.resolve(session,run_id,
            receipt_ref=binding.receipt_path.relative_to(self.root).as_posix(),receipt_sha256=binding.receipt_hash)
        try:
            return _current_source_snapshot_abi(self.authority,closure)
        finally:
            self.authority.discard(closure)

    def consume_reader_abi_qualification(self,session,*,run_id: str,consume):
        """Read-only qualification spanning both independent metadata heads.

        The server callback receives an ABI value and one-use exit check only;
        no closure or retry/publication permit enters the second Session.
        Existing normal resource resolution and ABI qualification own entry.
        This method owns the fresh post-second-head exit and revocation.
        """
        from .research_publication_jobs48 import exact_selection
        if not callable(consume):
            raise TypeError("publication_reader_qualification_callback_required")
        entry=self.authority._snapshot(session)
        engine=session.get_bind()
        binding=normal_history._REGISTRY_BINDING
        selection=exact_selection(session,run_id)
        closure=None
        original_guard=None
        finished=False
        finish_calls=0
        try:
            if binding is not None:
                closure=self.authority.resolve(session,run_id,
                    receipt_ref=binding.receipt_path.relative_to(self.root).as_posix(),
                    receipt_sha256=binding.receipt_hash)
                original_guard=_ReadSourceGuard.capture()
                if original_guard is None or not original_guard.matches():
                    raise ValueError("publication_resource_normal_source_guard_failed")
                installed=normal_recipe._installation_original()
                abi=_current_source_snapshot_abi(self.authority,closure)
            else:
                installed=None
                abi=None
            def finish(second):
                nonlocal finished,finish_calls
                finish_calls+=1
                if (finish_calls!=1 or second is session or second.get_bind() is not engine):
                    raise ValueError("publication_reader_qualification_independent_exit_required")
                second_entry=self.authority._snapshot(second)
                if second_entry[1] is entry[1]:
                    raise ValueError("publication_reader_qualification_independent_exit_required")
                if (normal_history._REGISTRY_BINDING is not binding
                        or exact_selection(second,run_id)!=selection):
                    raise ValueError("publication_reader_qualification_native_exit_changed")
                if closure is not None:
                    if self.authority._issued.get(closure.nonce) is not closure:
                        raise ValueError("publication_resource_issuer_identity_changed")
                    # Reuse the normal managed byte/path reader and source guard,
                    # not a second resolver, cached permission or material API.
                    for item in closure.entries:
                        raw=self.authority.read_source_bytes(closure,item.namespace,item.logical_ref)
                        if len(raw)!=item.bytes or sha(raw)!=item.sha256:
                            raise ValueError("publication_reader_qualification_source_exit_changed")
                    normal_history._registry_registrations(binding,force_full=True)
                    selected=normal_history._original_recipe_registration(second,run_id)
                    if (closure.mode=="retained-installation" and (selected is None
                            or selected.directory!=closure.selected_directory
                            or selected.index_hash!=closure.selected_index_sha256)
                            or closure.mode=="current-installation" and selected is not None):
                        raise ValueError("publication_reader_qualification_selection_exit_changed")
                    if (original_guard is None or not original_guard.matches()
                            or normal_recipe._installation_original()!=installed
                            or _current_source_snapshot_abi(self.authority,closure)!=abi):
                        raise ValueError("publication_reader_qualification_source_exit_changed")
                if (self.authority._snapshot(session)!=entry
                        or self.authority._snapshot(second)!=second_entry
                        or normal_history._REGISTRY_BINDING is not binding):
                    raise ValueError("publication_reader_qualification_snapshot_exit_changed")
                finished=True
            result=consume(abi,finish)
            if not finished or finish_calls!=1:
                raise ValueError("publication_reader_qualification_exit_required")
            return result
        finally:
            if closure is not None:
                self.authority.discard(closure)

    @contextmanager
    def retry_authority49(self,session,*,run_id: str):
        """Resolve normal complete source authority, without capture or a model.

        This opaque per-call permit never reconstructs a preparation issuer
        and grants no save/ACK/serving right. Source IO stays outside writers.
        """
        from .research_publication_jobs48 import exact_selection
        binding=normal_history._REGISTRY_BINDING
        if binding is None:
            raise ValueError("publication_normal_registry_not_configured")
        closure=self.authority.resolve(session,run_id,
            receipt_ref=binding.receipt_path.relative_to(self.root).as_posix(),receipt_sha256=binding.receipt_hash)
        permit=object()
        self._retry_permits49[permit]=(run_id,binding,closure,exact_selection(session,run_id))
        try:
            self.verify_retry_sources49(permit)
            yield permit
        finally:
            self._retry_permits49.pop(permit,None)
            self.authority.discard(closure)

    def verify_retry_native49(self,session,*,run_id: str,permit):
        from .research_publication_jobs48 import exact_selection
        entry=self._retry_permits49.get(permit)
        if (entry is None or entry[0]!=run_id or normal_history._REGISTRY_BINDING is not entry[1]
                or self.authority._issued.get(entry[2].nonce) is not entry[2]
                or exact_selection(session,run_id)!=entry[3]):
            raise ValueError("publication_retry_normal_authority_changed")

    def verify_retry_sources49(self,permit):
        entry=self._retry_permits49.get(permit)
        if entry is None or normal_history._REGISTRY_BINDING is not entry[1]:
            raise ValueError("publication_retry_normal_authority_changed")
        closure=entry[2]
        if self.authority._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_retry_normal_authority_changed")
        for item in closure.entries:
            raw=self.authority.read_source_bytes(closure,item.namespace,item.logical_ref)
            if len(raw)!=item.bytes or sha(raw)!=item.sha256:
                raise ValueError("publication_retry_normal_source_changed")
        guard=_ReadSourceGuard.capture()
        if guard is None or not guard.matches():
            raise ValueError("publication_retry_normal_source_changed")

    def capture_and_prepare(self,session,*,run_id: str,as_of: datetime):
        subphase_observer=_publication_subphase_observer71(run_id)
        try:
            checkpoint("resource_resolve")
            # Normalize these four known runtime imports before the frozen IO
            # scope. This uses installed normal modules, never material code;
            # optional cache creation cannot occur during frozen consumption.
            for name in _RUNTIME_MODULES:
                importlib.import_module(name)
            binding=normal_history._REGISTRY_BINDING
            if binding is None:
                raise ValueError("publication_normal_registry_not_configured")
            with _publication_subphase71(subphase_observer, "capture.resolve"):
                closure=self.authority.resolve(session,run_id,
                    receipt_ref=binding.receipt_path.relative_to(self.root).as_posix(),receipt_sha256=binding.receipt_hash)
            with _publication_subphase71(subphase_observer, "capture.abi"):
                if _current_source_snapshot_abi(self.authority,closure)!=SOURCE_SNAPSHOT_READ_ABI:
                    self.authority.discard(closure)
                    raise ValueError("publication_source_snapshot_abi_unsupported")
            owned=Path(tempfile.mkdtemp(prefix="publication-job-",dir=self.stage_parent))
            try:
                with execution_stage("capture"):
                    checkpoint("normal_capture")
                    with _publication_subphase_scope71(subphase_observer,"capture.collector",_ExecutionNativeCollector(session,limits=NATIVE_LIMITS,trace_slot_owned=True)) as collector:
                        with _publication_subphase71(subphase_observer, "capture.selection"):
                            selected=normal_history._original_recipe_registration(session,run_id)
                            release_scope=nullcontext()
                            if closure.mode=="retained-installation":
                                if (selected is None or selected.directory!=closure.selected_directory
                                        or selected.index_hash!=closure.selected_index_sha256):
                                    raise ValueError("publication_capture_selected_installation_changed")
                                verified=normal_history._verified_release_uncached(selected)
                                release_scope=normal_history._verified_release_read_scope(
                                    session,run_id,selected,verified)
                            elif selected is not None:
                                raise ValueError("publication_capture_selected_installation_changed")
                        # Only immutable originals are reused in this physical read.
                        # The strict fallback can reuse them; supported fast consumers
                        # retain their own full verification and all exit checks.
                        with _publication_subphase_scope71(subphase_observer,"capture.release",release_scope):
                            with _publication_subphase_scope71(subphase_observer,"capture.source_lease",publication_source_snapshot_read_scope(session,run_id=run_id,
                                    guard_type=_ReadSourceGuard,collector=collector,
                                    declared_sources=tuple((item.logical_ref,item.sha256) for item in closure.entries
                                        if item.namespace=="current-root"),
                                    checkpoint=lambda:checkpoint("normal_capture"))) as source_lease:
                                retained_source_snapshot=None
                                with _publication_subphase71(subphase_observer, "capture.reader"):
                                    with normal_history.publication_retained_source_snapshot_read_scope(
                                            session,run_id=run_id,collector=collector,current_source_lease=source_lease,
                                            checkpoint=lambda:checkpoint("normal_capture")) as private_source_lease:
                                        original=read_registered_original_v2(session,run_id,as_of=as_of,
                                            source_lease=source_lease)
                                        if private_source_lease is not None:
                                            retained_source_snapshot=private_source_lease.snapshot.model_dump(mode="json")
                                with _publication_subphase71(subphase_observer, "capture.collect_sources"):
                                    collector.collect_sources(self.root,closure.source_files())
                    with _publication_subphase71(subphase_observer, "capture.snapshot"):
                        capture=collector.snapshot()
                        capture["source_snapshot_v2"]=source_lease.snapshot.model_dump(mode="json")
                        if retained_source_snapshot is not None:
                            capture["retained_source_snapshot_v2"]=retained_source_snapshot
                    if capture["observed_queries_status"]!="complete" or capture["unresolved"] or capture["native_changes"]:
                        raise ValueError("publication_original_dependency_unresolved")
                    with _publication_subphase71(subphase_observer, "capture.authorize_frozen"):
                        resources=self.authority.authorize_frozen(closure,capture["sources"])
                with execution_stage("replay"):
                    checkpoint("frozen_replay")
                    replayed,audit,runtime=frozen_replay(self.authority,closure,resources,capture,original,self.stage_parent,subphase_observer=subphase_observer)
                checkpoint("material_write")
                capture["resource_authority"]=asdict(closure)
                capture["original_quality_json"]=original.quality_json
                capture["original_reader_source_sha256"]=original.reader_source_sha256
                capture["consumption_audit"]=audit
                raw=canonical(capture)
                if len(raw)>MATERIAL_BOUND:
                    raise ValueError("publication_explicit_material_bound_exceeded")
                path=owned/"material.json"
                with path.open("xb") as stream:
                    stream.write(raw);stream.flush();os.fsync(stream.fileno())
                verify_material_file(path,expected_sha256=sha(raw),expected_bytes=len(raw),max_bytes=MATERIAL_BOUND)
                bundle=frozen_bundle_from_verified_read(original,replayed,material_ref="materials/"+sha(raw)+".json",
                    material_sha256=sha(raw),material_bytes=len(raw))
                proof=PreparedFrozenPublication(uuid4().hex,bundle)
                self._issued[proof.nonce]=(proof,path,closure,runtime)
                return proof
            except BaseException:
                self.authority.discard(closure)
                self._cleanup(owned)
                raise
        except BaseException as error:
            _publication_subphase_terminal71(subphase_observer,error)
            raise
        finally:
            _publication_subphase_terminal71(subphase_observer,emit=True)

    def prepare_saved_confirmation(self,session,*,bundle: FrozenPublicationBundleV1,
                                   material_source: Path):
        """Reissue through normal full frozen replay of the exact saved original.

        The caller owns fresh independent job/Saved/publication heads. The old
        material nonce is evidence only; it never reconstructs issuer identity.
        No live original capture, latest as_of or current data supplement occurs.
        """
        if type(bundle) is not FrozenPublicationBundleV1:
            raise TypeError("publication_saved_confirmation_bundle_required")
        subphase_observer=_publication_subphase_observer71(bundle.run_id)
        closure=None;owned=None
        try:
            checkpoint("resource_resolve")
            entry=self.authority._snapshot(session)
            binding=normal_history._REGISTRY_BINDING
            if binding is None:
                raise ValueError("publication_normal_registry_not_configured")
            guard=_ReadSourceGuard.capture()
            if guard is None or not guard.matches():
                raise ValueError("publication_normal_source_guard_failed")
            installed=normal_recipe._installation_original()
            selected=normal_history._original_recipe_registration(session,bundle.run_id)
            for name in _RUNTIME_MODULES:
                importlib.import_module(name)
            _owned_parent(self.stage_parent)
            owned=Path(tempfile.mkdtemp(prefix="publication-job-",dir=self.stage_parent))
            path=owned/"material.json"
            with path.open("xb") as stream:
                verify_material_file(material_source,expected_sha256=bundle.material_sha256,
                    expected_bytes=bundle.material_bytes,max_bytes=MATERIAL_BOUND,sink=stream)
                stream.flush();os.fsync(stream.fileno())
            verify_material_file(path,expected_sha256=bundle.material_sha256,
                expected_bytes=bundle.material_bytes,max_bytes=MATERIAL_BOUND)
            with path.open("rb") as stream:
                raw=stream.read(bundle.material_bytes+1)
            if (len(raw)!=bundle.material_bytes or sha(raw)!=bundle.material_sha256):
                raise ValueError("publication_bundle_material_bytes_changed")
            capture=json.loads(raw)
            if (type(capture) is not dict or canonical(capture)!=raw
                    or capture.get("schema_version")!="research-publication-dependency-candidate.r1"
                    or capture.get("observed_queries_status")!="complete"
                    or type(capture.get("native_changes")) is not int or capture["native_changes"]!=0
                    or capture.get("unresolved")!=[]
                    or any(type(capture.get(name)) is not list for name in ("queries","schemas","rows","sources"))
                    or capture.get("original_quality_json")!=bundle.quality_json
                    or capture.get("original_reader_source_sha256")!=bundle.trusted_reader_source_sha256):
                raise ValueError("publication_saved_confirmation_material_changed")
            recorded=capture.get("resource_authority")
            with _publication_subphase71(subphase_observer,"capture.resolve"):
                closure=self.authority.resolve_saved_confirmation(session,run_id=bundle.run_id,
                    expected_authority=recorded)
            current=self.authority.saved_confirmation_description(closure)
            if (type(recorded) is not dict or set(recorded)!=set(current)
                    or type(recorded.get("nonce")) is not str
                    or re.fullmatch(r"[a-f0-9]{32}",recorded["nonce"]) is None
                    or canonical({k:v for k,v in recorded.items() if k!="nonce"})
                        !=canonical({k:v for k,v in current.items() if k!="nonce"})):
                raise ValueError("publication_saved_confirmation_resource_authority_changed")
            declared_v2="source_snapshot_v2" in capture or "retained_source_snapshot_v2" in capture
            if closure.mode=="current-installation" and "source_snapshot_v2" not in capture:
                raise ValueError("publication_source_snapshot_abi_unsupported")
            if declared_v2:
                with _publication_subphase71(subphase_observer,"capture.abi"):
                    if (_current_source_snapshot_abi(self.authority,closure)!=SOURCE_SNAPSHOT_READ_ABI
                            or type(capture.get("source_snapshot_v2")) is not dict
                            or "retained_source_snapshot_v2" in capture
                                and type(capture["retained_source_snapshot_v2"]) is not dict):
                        raise ValueError("publication_source_snapshot_abi_unsupported")
            with _publication_subphase71(subphase_observer,"capture.authorize_frozen"):
                resources=self.authority.authorize_frozen(closure,capture["sources"])
            expected=OriginalPublicationRead(bundle.quality_json,bundle.event_id,
                bundle.artifact_id,bundle.trusted_reader_source_sha256)
            with execution_stage("replay"):
                checkpoint("frozen_replay")
                replayed,_audit,runtime=frozen_replay(self.authority,closure,resources,capture,
                    expected,self.stage_parent,subphase_observer=subphase_observer)
            if replayed!=expected:
                raise ValueError("publication_full_frozen_original_changed")
            checkpoint("preparation_verify")
            self.authority.verify_normal_authority(closure,
                checkpoint=lambda: checkpoint("preparation_verify"))
            if self.authority._saved_current.get(closure.nonce) is None:
                normal_history._registry_registrations(binding,force_full=True)
            runtime.require_unchanged()
            if (self.authority._issued.get(closure.nonce) is not closure
                    or normal_history._REGISTRY_BINDING is not binding
                    or normal_history._original_recipe_registration(session,bundle.run_id)!=selected
                    or normal_recipe._installation_original()!=installed
                    or self.authority._snapshot(session)!=entry or not guard.matches()
                    or declared_v2 and _current_source_snapshot_abi(self.authority,closure)!=SOURCE_SNAPSHOT_READ_ABI):
                raise ValueError("publication_saved_confirmation_normal_exit_changed")
            verify_material_file(path,expected_sha256=bundle.material_sha256,
                expected_bytes=bundle.material_bytes,max_bytes=MATERIAL_BOUND)
            proof=PreparedFrozenPublication(uuid4().hex,bundle)
            self._issued[proof.nonce]=(proof,path,closure,runtime)
            return proof
        except BaseException as error:
            _publication_subphase_terminal71(subphase_observer,error)
            if closure is not None:
                self.authority.discard(closure)
            if owned is not None:
                self._cleanup(owned)
            raise
        finally:
            _publication_subphase_terminal71(subphase_observer,emit=True)

    def verify(self,proof):
        checkpoint("preparation_verify")
        entry=self._issued.get(proof.nonce)
        if entry is None or entry[0] is not proof:
            raise ValueError("publication_bundle_preparation_issuer_changed")
        _,path,closure,runtime=entry
        verify_material_file(path,expected_sha256=proof.value.material_sha256,
            expected_bytes=proof.value.material_bytes,max_bytes=MATERIAL_BOUND)
        self.authority.verify_normal_authority(closure,
            checkpoint=lambda: checkpoint("preparation_verify"))
        runtime.require_unchanged()
        guard=_ReadSourceGuard.capture()
        if guard is None or not guard.matches():
            raise ValueError("publication_preparation_source_guard_changed")

    def material_source(self,proof):
        self.verify(proof)
        return self._issued[proof.nonce][1]

    def consume(self,proof):
        self.verify(proof)
        _,path,closure,_=self._issued.pop(proof.nonce)
        self.authority.discard(closure)
        self._cleanup(path.parent)

    def abandon(self,proof):
        entry=self._issued.get(proof.nonce)
        if entry is None or entry[0] is not proof:
            raise ValueError("publication_bundle_preparation_issuer_changed")
        _,path,closure,_=self._issued.pop(proof.nonce)
        self.authority.discard(closure)
        self._cleanup(path.parent)

    def close(self):
        for proof,_,_,_ in tuple(self._issued.values()):
            self.abandon(proof)

    def _cleanup(self,path):
        _owned_parent(self.stage_parent)
        if (path.parent!=self.stage_parent or not path.name.startswith("publication-job-")
                or path.is_symlink() or path.resolve(strict=True).parent!=self.stage_parent):
            raise ValueError("publication_job_cleanup_target_changed")
        shutil.rmtree(path)


@dataclass
class ManagedPublicationBundleService:
    lifecycle: DurablePublicationLifecycle
    worker: PublicationBundleWorker
    execution: PublicationExecutionCoordinator
    def close(self):
        self.execution.close()
        try:
            self.lifecycle.preparer.close()
        finally:
            self.lifecycle.close()


def _publication_instance_paths(root: Path, data_root: Path) -> tuple[Path, Path]:
    if (not isinstance(data_root, Path) or not data_root.is_absolute()
            or ".." in data_root.parts):
        raise ValueError("publication_instance_data_root_invalid")
    if data_root == root or root in data_root.parents:
        raise ValueError("publication_instance_data_root_inside_source")
    stage, archive = data_root / "stage", data_root / "archive"
    # Validate all existing parents and both children before creating either.
    for candidate in (data_root, stage, archive):
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current /= part
            try:
                status = current.lstat()
            except FileNotFoundError:
                continue
            if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                raise ValueError("publication_instance_data_root_link_forbidden")
            if not current.is_dir():
                raise ValueError("publication_instance_data_root_directory_required")
    resolved = data_root.resolve()
    if resolved != data_root or resolved == root.resolve() or root.resolve() in resolved.parents:
        raise ValueError("publication_instance_data_root_alias_forbidden")
    data_root.mkdir(parents=True, exist_ok=True)
    _owned_parent(data_root)
    for child in (stage, archive):
        child.mkdir(exist_ok=True)
        _owned_parent(child)
    return stage, archive


def create_managed_publication_bundle_service(*,database: Database,repository_root: Path,
        publication_data_root: Path | None = None):
    root=repository_root.absolute()
    if root!=normal_history._CURRENT_ROOT.absolute() or normal_history._REGISTRY_BINDING is None:
        raise ValueError("publication_service_normal_registered_root_required")
    if (getattr(normal_history,"FROZEN_PUBLICATION_HOST_CONTRACT",None)!="publication-frozen-host.v1"
            or getattr(normal_recipe,"FROZEN_PUBLICATION_CURRENT_CONTRACT",None)!="publication-current-installation.v1"):
        raise ValueError("publication_service_normal_frozen_dispatch_not_installed")
    required={"research_publication_bundle_jobs","research_publication_bundles",
        "research_publication_bundle_confirmations","research_publication_bundle_state_events"}
    if not required<=set(inspect(database.engine).get_table_names()):
        raise ValueError("publication_service_migration_0091_required")
    if publication_data_root is None:
        # The default stage stays outside Root without changing existing storage.
        stage=root.parent/".decision-hub-publication-stage"
        stage.mkdir(exist_ok=True);_owned_parent(stage)
        archive_root=root/"artifacts/research-publication-bundles"
    else:
        stage,archive_root=_publication_instance_paths(root,publication_data_root)
    preparer=ManagedFrozenPublicationPreparer(root,stage)
    archive=BundleArchive(archive_root,
        max_bundle_bytes=BUNDLE_BOUND,max_material_bytes=MATERIAL_BOUND)
    lifecycle=DurablePublicationLifecycle(database,archive,preparer=preparer)
    def observe_execution(value):
        logging.getLogger(__name__).warning(
            "publication_execution phase=%s claim_sha256=%s generation=%s state=%s reason_code=%s publication_stop_status=%s native_exited=%s elapsed_ms=%s last_progress_ms=%s renewals=%s renew_rejection_code=%s terminal_stage_timings=%s",
            value.get("phase"),value.get("run_sha256"),value.get("generation"),
            value.get("state"),value.get("reason_code"),value.get("publication_stop_status"),value.get("native_exited"),
            value.get("elapsed_ms"),value.get("last_progress_ms"),value.get("renewals"),value.get("renew_rejection_code"),
            (json.dumps({"stages":value.get("stage_timings"),"unattributed_ms":value.get("unattributed_ms"),
                "renew_may_overlap_compute":True},sort_keys=True,separators=(",",":"))
                if value.get("native_exited") is True else None),
        )
        interval=value.get("native_call_interval")
        if interval is not None:
            # Queue-to-observer dispatch time, not a native event or disk fsync.
            emitted=dict(interval)
            emitted["observer_flushed_at_utc"]=utcnow().isoformat()
            emitted["observer_flushed_monotonic_ns"]=time.monotonic_ns()
            logging.getLogger(__name__).warning("publication_native_interval %s",
                json.dumps(emitted,sort_keys=True,separators=(",",":")))
    execution=PublicationExecutionCoordinator(observer=observe_execution)
    def produce(claim):
        if active_execution() is None:
            raise ValueError("publication_execution_scope_not_wired")
        checkpoint("produce")
        with lifecycle._ro() as session:
            lifecycle._owned(session,claim,"queued")
            return preparer.capture_and_prepare(session,run_id=claim.run_id,as_of=utcnow())
    def resume_saved(claim):
        if active_execution() is None:
            raise ValueError("publication_execution_scope_not_wired")
        checkpoint("produce")
        proof=None
        try:
            with lifecycle._ro() as session:
                lifecycle._owned(session,claim,"prepared")
                bundle,path=lifecycle.prepared_confirmation_input(session,claim)
                proof=preparer.prepare_saved_confirmation(session,bundle=bundle,material_source=path)
            return proof
        except BaseException:
            if proof is not None:
                preparer.abandon(proof)
            raise
    worker=PublicationBundleWorker(lifecycle,produce,saved_producer=resume_saved)
    worker.execution=execution
    return ManagedPublicationBundleService(lifecycle,worker,execution)
