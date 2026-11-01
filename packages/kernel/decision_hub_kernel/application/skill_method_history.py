"""Read exact old method originals; no bootstrap, execution or approval grant."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.db import RunRoleOutputRecord
from ..ports.market_path import canonical_hash
from .product_release_provenance import resolve_product_receipt
from .research_skill_package import (
    decoded,
    encoded,
    sha,
)

_APP = "packages/kernel/decision_hub_kernel/application/"
_COMPILER = "e605ead0f993c9387bcc751cff117607ce08986c2fea7419c022f5b697875a8e"
_RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "research_skill_versions"
_RETAINED = {
    _COMPILER: "7535766ad8d50660d71ab40cf43c77d33db4ccfbffbd3eae09be92187149832c",
    "1a1c1a58068ff8814edfea36d066ae6d3da802c9e9b84777f1cf37a48d8b6ba4":
        "0b09786521345796d1a91eb418cc470abbfe41025f373fd26914c09c89119f13",
    "dd9adb1a596d3eac0cde570a33619dd4991c3fe5e5395b406b46b9afca23265d":
        "62fb2a83c4477895086a0cdd0f1f5a8e7e1414695f5ac69d6c6ae780e40b7809",
}
# Additional installation authority for an unchanged compiler. Old index and
# root bytes remain immutable; the Graph-authenticated receipt chooses the index.
_RETAINED_INSTALLATIONS = {
    (
        "dd9adb1a596d3eac0cde570a33619dd4991c3fe5e5395b406b46b9afca23265d",
        "artifacts/decision-product-v1/w5w6-release-07/product-validated-builds.json",
        "5a786e06e72aba0ffe400c179d1b254af1a906d992a9f346f8ed1f6ad0dc16a9",
    ): (
        "installations/5a786e06e72aba0ffe400c179d1b254af1a906d992a9f346f8ed1f6ad0dc16a9/index.json",
        "0b6de16a9ee19cb37fe275b37449acf78eecec016badb4ddc90190ab10f1d03a",
    ),
}


def _retained_index(compiler_hash: str, receipt_ref: Mapping[str, Any]) -> tuple[str, str]:
    selected = _RETAINED_INSTALLATIONS.get(
        (compiler_hash, receipt_ref.get("path"), receipt_ref.get("sha256"))
    )
    prefix = f"packages/kernel/research_skill_versions/{compiler_hash}/"
    if selected is not None:
        relative, expected = selected
        return prefix + relative, expected
    expected = _RETAINED.get(compiler_hash)
    if expected is None:
        raise ValueError("skill_history_compiler_not_retained")
    # The original index still requires exact receipt equality before any use.
    return prefix + "index.json", expected


# Finite current read adapters only. The Skill read family below is loaded
# from the authenticated original Graph namespace, including transitive policy
# and provider imports. Shared component readers remain byte-pinned.
_READER_FILES = tuple(_APP + name + ".py" for name in (
    "research_skill_compiler", "research_skill_package", "research_skill_selection",
    "research_skill_use", "research_skill_occurrence", "research_skill_workflow",
    "research_skill_gateway", "research_skill_market_input", "research_skill_node_ledger",
    "research_skill_nodes", "research_skill_consumption", "component_registry",
    "market_component_functions", "market_features", "market_path_store", "market_original_parse",
    "fact_store", "research_evidence", "risk_monitor", "research_policy_checker",
    "provider_input_receipts",
)) + (
    "apps/hub_worker/frozen_skill_component.py", "apps/hub_worker/market_components.py",
    "packages/kernel/decision_hub_kernel/ports/market_path.py",
    "packages/kernel/decision_hub_kernel/ports/component_registry.py",
    "packages/kernel/decision_hub_kernel/ports/research_signal.py",
    "packages/kernel/decision_hub_kernel/ports/research_observation.py",
    "packages/contracts_py/decision_hub_contracts/research_skill_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/research_signal_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/decision_product_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/generated/research_signal.py",
    "packages/contracts_py/decision_hub_contracts/generated/provider_input.py",
    "packages/contracts_py/decision_hub_contracts/generated/agentic_research.py",
    "packages/kernel/decision_hub_kernel/decision/path_risk.py",
)


# These readers use the authenticated original Graph family. The sole missing
# workflow surface is loaded separately from its retained compiler original.
# Never compare these to upgraded current source or import current siblings.
_ARCHIVED_READERS = frozenset(_APP + name + ".py" for name in (
    "research_skill_compiler", "research_skill_package", "research_skill_selection",
    "research_skill_use", "research_skill_occurrence", "research_skill_workflow",
    "research_skill_gateway",
    "research_skill_market_input", "research_skill_node_ledger", "research_skill_nodes",
    "research_skill_consumption", "research_policy_checker", "provider_input_receipts",
    "fact_store", "research_evidence",
)) | frozenset((
    "packages/kernel/decision_hub_kernel/ports/research_signal.py",
    "packages/kernel/decision_hub_kernel/ports/research_observation.py",
    "packages/contracts_py/decision_hub_contracts/research_skill_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/research_signal_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/decision_product_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/generated/research_signal.py",
    "packages/contracts_py/decision_hub_contracts/generated/provider_input.py",
    "packages/contracts_py/decision_hub_contracts/generated/agentic_research.py",
))


class _ReadArchive:
    def __init__(self, archive: Any):
        self.archive = archive
        self.readset: dict[str, str] = {}

    def read(self, ref: str) -> bytes:
        value = self.archive.read(ref)
        digest = sha(value)
        if self.readset.setdefault(ref, digest) != digest:
            raise ValueError("skill_history_raw_changed")
        return value

    def put(self, *_args: Any, **_kwargs: Any):
        raise PermissionError("skill_history_raw_write_forbidden")

    def verify(self) -> None:
        for ref, digest in self.readset.items():
            if sha(self.archive.read(ref)) != digest:
                raise ValueError("skill_history_raw_changed")


def _forbidden(*_args: Any, **_kwargs: Any):
    raise PermissionError("skill_history_execution_forbidden")


def _retained_installation(
    compiler_hash: str, *, receipt_ref: Mapping[str, Any], root: Path,
    payloads: Mapping[str, bytes], read: Callable[[str], bytes],
    graph_index: Mapping[str, Any], private: Any,
) -> tuple[dict[str, Any], Mapping[str, str]]:
    """Bind reviewed immutable compiler bytes to this Graph's original release.

    The pinned index remains an immutable finite compiler-source template.
    A later receipt grants only read authority when its complete authenticated
    source map contains every identical template byte. No index is rewritten,
    unknown compiler accepted, or execution permission derived here.
    """
    if (
        graph_index.get("installation_binding", {}).get("receipt") != receipt_ref
        or graph_index.get("graph_recipe_dependency_hash") != private.registration.graph_hash
    ):
        raise ValueError("skill_history_original_installation_mismatch")
    index_path, expected = _retained_index(compiler_hash, receipt_ref)
    raw = read(index_path)
    if sha(raw) != expected:
        raise ValueError("skill_history_index_changed")
    index = decoded(raw)
    if (
        set(index) != {"schema_version", "compiler_hash", "original_receipt",
                       "source_files", "auxiliary_sources", "execution_permission"}
        or index["schema_version"] != "retained-skill-source-closure.v1"
        or index["execution_permission"] is not False
        or index["compiler_hash"] != compiler_hash
        or canonical_hash(index["source_files"]) != compiler_hash
        or set(receipt_ref) != {"path", "sha256"}
    ):
        raise ValueError("skill_history_original_installation_mismatch")

    def original_read(name: str) -> bytes:
        # Only the surrounding authenticated Graph's immutable receipt and
        # source-map resources are eligible. Never read a current receipt.
        if name not in payloads:
            raise ValueError("skill_history_original_receipt_resource_missing")
        if private.payloads.get(name) != payloads[name]:
            raise ValueError("skill_history_original_receipt_namespace_mismatch")
        return payloads[name]

    receipt = resolve_product_receipt(
        original_read(receipt_ref["path"]), expected_root=root,
        read_resource=original_read, expected_sha256=receipt_ref["sha256"],
    ).value
    old_sources = receipt["sources"]
    prefix = f"packages/kernel/research_skill_versions/{compiler_hash}/"
    for name, digest in {**index["source_files"], **index["auxiliary_sources"]}.items():
        if old_sources.get(name) != digest or sha(read(prefix + "root/" + name)) != digest:
            raise ValueError("skill_history_retained_source_changed")
    return index, old_sources


def _retained_compiler(
    compiler_hash: str, *, receipt_ref: dict[str, Any], root: Path,
    payloads: Mapping[str, bytes], read: Callable[[str], bytes], private: Any,
    graph_index: Mapping[str, Any],
):
    index, old_sources = _retained_installation(
        compiler_hash, receipt_ref=receipt_ref, root=root, payloads=payloads, read=read,
        graph_index=graph_index, private=private,
    )
    prefix = f"packages/kernel/research_skill_versions/{compiler_hash}/"
    frozen_files = {}
    for name, digest in {**index["source_files"], **index["auxiliary_sources"]}.items():
        # Index hash pins the reviewed finite path set. Each byte also needs its
        # own original release authority, not just a newly written index label.
        if old_sources.get(name) != digest or sha(read(prefix + "root/" + name)) != digest:
            raise ValueError("skill_history_retained_source_changed")
        if name in index["source_files"]:
            path = (_RESOURCE_ROOT / compiler_hash / "root" / name).absolute()
            if str(path).startswith("\\\\") or len(str(path)) < 240:
                frozen_files[name] = path
            else:
                # Native Windows long paths; POSIX absolute paths stay unchanged.
                import os
                frozen_files[name] = Path("\\\\?\\" + str(path)) if os.name == "nt" else path
    for name in _READER_FILES:
        expected_hash = old_sources.get(name)
        if name in _ARCHIVED_READERS:
            # The surrounding bridge pins this exact private namespace and
            # re-verifies its complete archived closure after the read.
            original = payloads.get(name)
            if original is None and name == _APP + "research_skill_workflow.py":
                # This sole supplement is already authenticated above by the
                # exact retained index AND original release source inventory.
                if index["source_files"].get(name) != expected_hash:
                    raise ValueError("skill_history_workflow_not_in_compiler")
                continue
            if (expected_hash is None or original is None
                    or sha(original) != expected_hash
                    or private.payloads.get(name) != original
                    or private.paths.get(name.removesuffix(".py").replace("/", ".")) != name):
                raise ValueError("skill_history_archived_reader_changed:" + name)
            continue
        module = importlib.import_module(name.removesuffix(".py").replace("/", "."))
        if module.__file__ is None:
            raise ValueError("skill_history_reader_source_missing:" + name)
        location = Path(module.__file__).resolve()
        if (
            expected_hash is None
            or location != (root / name).resolve()
            or sha(location.read_bytes()) != expected_hash
            or sha(read(name)) != expected_hash
        ):
            raise ValueError("skill_history_reader_implementation_changed:" + name)
    original_package = private.load(_APP.replace("/", ".") + "research_skill_package")
    installed = original_package.InstalledSkillCompiler(
        tuple(sorted(frozen_files.items())), compiler_hash
    )
    installed.verify()
    return installed


@contextmanager
def _retained_workflow(
    private: Any, compiler_hash: str, read: Callable[[str], bytes],
    *, receipt_ref: Mapping[str, Any], root: Path, payloads: Mapping[str, bytes],
    graph_index: Mapping[str, Any],
):
    """One authenticated supplemental module; shared Graph maps never change.

    Only _retained_compiler-authorized callers enter here. Recheck the finite
    index and bytes before load and after use; the outer caller rechecks the
    original receipt and complete retained/Graph closures. The supplemental
    module imports through the original Graph family's existing loader.
    """
    name = _APP + "research_skill_workflow.py"
    logical = name.removesuffix(".py").replace("/", ".")
    prefix = f"packages/kernel/research_skill_versions/{compiler_hash}/"

    def original() -> bytes:
        index_path, expected = _retained_index(compiler_hash, receipt_ref)
        index_raw = read(index_path)
        if sha(index_raw) != expected:
            raise ValueError("skill_history_index_changed")
        index = decoded(index_raw)
        if index["compiler_hash"] != compiler_hash:
            raise ValueError("skill_history_original_installation_mismatch")
        raw = read(prefix + "root/" + name)
        if sha(raw) != index["source_files"].get(name):
            raise ValueError("skill_history_retained_workflow_changed")
        # Recheck the complete same-compiler installation both before load
        # and after use, including the original receipt/source-map bytes.
        _retained_installation(
            compiler_hash, receipt_ref=receipt_ref, root=root, payloads=payloads, read=read,
            graph_index=graph_index, private=private,
        )
        return raw

    raw = original()
    if name in private.payloads:
        if private.payloads[name] != raw or private.paths.get(logical) != name:
            raise ValueError("skill_history_archived_workflow_changed")
        yield private.load(logical)
        if original() != raw:
            raise ValueError("skill_history_retained_workflow_changed")
        return
    # The original Graph omits only this read surface. Do not enlarge its
    # signed all_files map, mutate its shared loader, or load current helpers.
    supplement = type(private)(
        private.registration, {name: raw}, _RESOURCE_ROOT / compiler_hash / "root", v3=True,
    )
    supplement.prefix = private.prefix + "_workflow_" + uuid4().hex
    supplement._import = private._import
    try:
        module = supplement.load(logical)
        old_package = private.load(_APP.replace("/", ".") + "research_skill_package")
        old_use = private.load(_APP.replace("/", ".") + "research_skill_use")
        if module.decoded is not old_package.decoded or module.event_ref is not old_use.event_ref:
            raise ValueError("skill_history_workflow_import_mismatch")
        yield module
        if original() != raw or supplement.payloads != {name: raw}:
            raise ValueError("skill_history_retained_workflow_changed")
    finally:
        supplement.discard()


def _plain_method(
    session: Session, run_id: str, packages: Any, selections: Any, workflow: Any,
) -> dict[str, Any]:
    selection = selections.read_in_session(session, run_id)
    generation = selection["generation"]
    execution = selections.execution_reader(session, run_id, generation)
    identity = decoded(execution.scope_identity_json)
    listing = workflow.list_in_session(
        session, run_id=run_id, generation=generation,
        execution_session_id=identity["dsh_session_id"],
    )
    package = packages.read_in_session(session, selection["skill_version_id"])
    resources = [r for r in package.resources if r.path == "workflow.json"]
    if len(resources) != 1:
        raise ValueError("method_workflow_original_missing")
    return decoded(encoded(dict(
        selection=selection, execution_identity=identity, listing=listing,
        version=package.version, workflow=decoded(resources[0].body),
    )))


def read_current_method_in_session(
    session: Session, run_id: str, service: Any,
) -> dict[str, Any]:
    return _plain_method(
        session, run_id, service.packages, service.use.selections, service.workflow,
    )


def read_retained_method_originals(
    session: Session, *, run_id: str, service: Any, private: Any,
    index: dict[str, Any], payloads: Mapping[str, bytes], root: Path,
    read: Callable[[str], bytes],
) -> dict[str, Any]:
    """Only invoked inside the exact-run, original Graph private read scope."""
    row = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id,
        RunRoleOutputRecord.role == "research_skill_selection",
    ))
    if row is None:
        raise ValueError("method_selection_missing")
    declared = decoded(row.payload_json)
    # This version lookup is only a dispatch hint. Original package and selection
    # verification below authenticate every field and their exact Run binding.
    from ..persistence.market_case_records import MarketComponentVersionRecord
    from ..ports.component_registry import ComponentManifest
    from .component_registry import _read
    manifest = _read(
        session.get(MarketComponentVersionRecord, declared["skill_version_id"]), ComponentManifest,
    )
    receipt_ref = index["installation_binding"]["receipt"]

    def compiler():
        return _retained_compiler(manifest.code_hash, receipt_ref=receipt_ref, root=root,
                                  payloads=payloads, read=read, private=private, graph_index=index)

    installed = compiler()
    archive = _ReadArchive(service.packages.archive)
    original_package = private.load(_APP.replace("/", ".") + "research_skill_package")
    packages = original_package.SkillPackageService(service.packages.registry, archive, installed,
                                   original_package.SkillContracts(), clock=service.packages.clock)
    package = packages.read_in_session(session, manifest.version_id)
    original_selection = private.load(_APP.replace("/", ".") + "research_skill_selection")
    selections = original_selection.SkillSelectionService(
        packages, baseline_manifests={manifest.version_id: package.manifest.payload_hash},
        context_reader=_forbidden, execution_reader=original_selection.read_skill_execution,
        originals_preparer=_forbidden, clock=service.packages.clock,
    )
    provider = private.load(_APP.replace("/", ".") + "provider_input_receipts")

    original_use = private.load(_APP.replace("/", ".") + "research_skill_use")
    use = original_use.SkillUseService(
        selections, provider_reader=provider.read_provider_input_in_session,
        emission_reader=provider.read_provider_emission_in_session, clock=service.use.clock,
    )
    original_market = private.load(_APP.replace("/", ".") + "market_path_store")
    market_archive = _ReadArchive(service.nodes.market.archive)
    market = original_market.MarketPathStore(service.packages.registry.database, market_archive)
    original_gateway = private.load(_APP.replace("/", ".") + "research_skill_gateway")
    original_execution = private.load(_APP.replace("/", ".") + "research_execution_binding")
    def deadline(s: Session, r: str, g: int) -> datetime:
        return original_execution.read_prepared_execution_in_session(
            s, r, g).execution.request.deadline_at

    original_nodes = private.load(_APP.replace("/", ".") + "research_skill_nodes")
    nodes = original_nodes.SkillNodeService(
        use, market, service.nodes.component,
        gateway_prepared_reader=original_gateway._prepared_gateway,
        deadline_reader=deadline,
    )
    original_consumption = private.load(_APP.replace("/", ".") + "research_skill_consumption")
    with _retained_workflow(
        private, manifest.code_hash, read, receipt_ref=receipt_ref, root=root, payloads=payloads,
        graph_index=index,
    ) as original_workflow:
        workflow = original_workflow.SkillWorkflowService(
            original_consumption.SkillConsumptionService(nodes),
        )
        result = _plain_method(session, run_id, packages, selections, workflow)
    archive.verify()
    market_archive.verify()
    compiler()
    # Reject any escaped private object, service, datetime, NaN, or implicit DTO.
    return json.loads(json.dumps(result, allow_nan=False))
