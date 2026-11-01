"""Bounded file-only completed-prior lineage; never an installed-release writer."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

MAX_HOPS = 32
MAX_METADATA_BYTES = 512 * 1024 * 1024
CODEC = "packages/kernel/decision_hub_kernel/application/product_release_provenance.py"
PRODUCER = "packages/kernel/decision_hub_kernel/application/research_recipe_archive.py"
CURRENT = {
    CODEC: "180c726a987d3b4deacb940b04113c3f1940bae6870cd47df3690a34b0e68691",
    PRODUCER: "b47d8050652b24ac7406146960658bae82e74cc508db6bcb4d657f640f3990e7",
}
RUNTIME = {
    CODEC: "b0b8c17c1c8eda55a81920d8b7e4fb4baef2c12fe9770078601d0fde1fc8652d",
    PRODUCER: CURRENT[PRODUCER],
}


@dataclass(frozen=True)
class ChainWitness:
    records: tuple[tuple[str, bytes], ...]
    completed_refs: tuple[dict[str, str], ...]
    runtime_ref: dict[str, str]
    registry_receipts: tuple[dict[str, Any], ...]
    root: Path
    api: Any
    metadata_bytes: int
    hop_limit: int
    metadata_limit: int

    @property
    def hop_count(self) -> int:
        return len(self.completed_refs)

    def __iter__(self) -> Iterator[tuple[str, bytes]]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def verify_registry_resources(self) -> None:
        for receipt in self.registry_receipts:
            self.api.verify_registry(self.root, receipt)

    def report_lineage(self) -> dict[str, Any]:
        return {
            "single_hop": self.hop_count == 1,
            "hop_count": self.hop_count,
            "completed_receipts": list(self.completed_refs),
            "runtime_installed_receipt": self.runtime_ref,
            "metadata_bytes": self.metadata_bytes,
            "max_hops": self.hop_limit,
            "max_metadata_bytes": self.metadata_limit,
        }


class Evidence:
    def __init__(self, root: Path, api: Any) -> None:
        self.root, self.api = root, api
        self.files: dict[str, bytes] = {}
        self.total = 0
        self.registries: list[dict[str, Any]] = []

    def remember(self, path: Path, raw: bytes) -> None:
        name = path.relative_to(self.root).as_posix()
        if name in self.files:
            self.api.require(self.files[name] == raw, "evidence_changed_during_prepare")
            return
        self.api.require(self.total + len(raw) <= MAX_METADATA_BYTES, "chain_metadata_limit")
        self.files[name] = raw
        self.total += len(raw)

    def read(self, path: Path) -> bytes:
        raw = self.api.read(path)
        self.remember(path, raw)
        return raw

    def reference(self, value: Any, expected: Path | None = None) -> tuple[Path, bytes]:
        path, raw = self.api.reference(self.root, value, expected=expected)
        self.remember(path, raw)
        return path, raw

    def resolved(self, path: Path, raw: bytes, digest: str) -> dict[str, Any]:
        def read_resource(name: str) -> bytes:
            return self.read(self.api.scoped(self.root, name))

        result = self.api.resolve(
            raw,
            expected_root=self.root,
            expected_sha256=digest,
            read_resource=read_resource,
        )
        for name, expected in result.transport.items():
            self.api.require(
                self.api.digest(self.read(self.api.scoped(self.root, name))) == expected,
                "transport_hash_mismatch",
            )
        return dict(result.value)


def normal_path(root: Path, reference: Any, api: Any) -> Path:
    api.require(
        type(reference) is dict
        and set(reference) == {"path", "sha256"}
        and type(reference["path"]) is str
        and type(reference["sha256"]) is str
        and re.fullmatch("[a-f0-9]{64}", reference["sha256"]) is not None,
        "reference_invalid",
    )
    checked_reference = cast(dict[str, str], reference)
    declared = Path(checked_reference["path"])
    path = declared.absolute() if declared.is_absolute() else root / declared
    api.require(path.is_relative_to(root / "artifacts"), "reference_scope_invalid")
    api.require(
        api.scoped(root, path.relative_to(root).as_posix()) == path, "reference_path_invalid"
    )
    return path


def registry(e: Evidence, value: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    api = e.api
    declaration = value["evidence"].get("retained_recipe_archives_v1")
    api.require(
        type(declaration) is dict and set(declaration) == {"registry"}, "registry_evidence_invalid"
    )
    reference = declaration["registry"]
    path, raw = e.reference(reference)
    reference = cast(dict[str, str], reference)
    name = path.relative_to(e.root).as_posix()
    api.require(
        name
        == "packages/kernel/research_recipe_versions/registries/" + reference["sha256"] + ".json"
        and value["sources"].get(name) == reference["sha256"],
        "registry_source_invalid",
    )
    parsed = json.loads(raw)
    api.require(api.canonical(parsed) == raw, "registry_canonical_invalid")
    rows = api.registry_rows(parsed)
    # The original read-only validator checks all index/resources and source membership.
    api.require(
        api.verify_registry(e.root, value) == parsed["registrations"],
        "registry_original_validation_changed",
    )
    e.registries.append(value)
    return reference, rows


def anchor(
    e: Evidence, pointer_raw: bytes
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    api = e.api
    root = e.root
    pointer: dict[str, Any] = json.loads(pointer_raw)
    api.require(
        type(pointer) is dict
        and set(pointer) == {"schema_version", "repository_root", "receipt_path", "receipt_sha256"},
        "runtime_locator_invalid",
    )
    api.require(
        pointer["schema_version"] == "product-installed-release-pointer.v1"
        and type(pointer["repository_root"]) is str
        and Path(pointer["repository_root"]).resolve() == root.resolve()
        and type(pointer["receipt_path"]) is str,
        "runtime_locator_invalid",
    )
    location = root / "data/product-local/installed-release-receipt.json"
    api.require(e.read(location) == pointer_raw, "runtime_locator_invalid")
    reference = {
        "path": str(api.scoped(root, pointer["receipt_path"])),
        "sha256": pointer["receipt_sha256"],
    }
    path = normal_path(root, reference, api)
    _, raw = e.reference(reference, expected=path)
    value = e.resolved(path, raw, reference["sha256"])
    api.require(
        all(value["sources"].get(name) == digest for name, digest in RUNTIME.items()),
        "producer_receipt_binding_invalid",
    )
    reg, rows = registry(e, value)
    return reference, reg, rows


def completed_clock(api: Any, evidence: dict[str, Any], execution: dict[str, Any]) -> None:
    try:
        values = [execution["started_at"], evidence["observed_at"], execution["completed_at"]]
        if any(not isinstance(value, str) for value in values):
            raise ValueError("clock type")
        clocks = [datetime.fromisoformat(value) for value in values]
        valid = all(value.tzinfo is not None and value.utcoffset() is not None for value in clocks)
        valid = valid and clocks[0] <= clocks[1] <= clocks[2]
    except (KeyError, TypeError, ValueError):
        valid = False
    api.require(valid, "completed_build_clock_invalid")


def completed_execution(
    e: Evidence, path: Path, digest: str, value: dict[str, Any]
) -> dict[str, Any]:
    api = e.api
    evidence = value["evidence"]
    api.require(
        evidence.get("origin") == "standard_product_build_only"
        and all(
            type(evidence.get(name)) is int and evidence[name] == 1
            for name in ["ui_build_count", "host_build_count"]
        )
        and evidence.get("source_stable_except_standard_generated_identity") is True
        and type(evidence.get("launch_id")) is str
        and re.fullmatch("[0-9]{8}T[0-9]{9}Z", evidence["launch_id"]) is not None
        and evidence.get("native_execution_qualified") is False
        and evidence.get("eligible_for_deployment") is False,
        "prior_origin_invalid",
    )
    execution = json.loads(e.read(path.parent / "build-execution.json"))
    counts = {
        "ui_build_attempts": 1,
        "host_build_attempts": 1,
        "ui_build_exit_code": 0,
        "host_build_exit_code": 0,
        "builds": 2,
        "migrations": 0,
        "process_starts": 0,
        "process_stops": 0,
        "model_submissions": 0,
        "pointer_writes": 0,
    }
    api.require(
        type(execution) is dict
        and execution.get("schema_version") == "product-build-execution.v1"
        and execution.get("passed") is True
        and all(type(execution.get(k)) is int and execution[k] == v for k, v in counts.items())
        and execution.get("error") is None
        and execution.get("native_execution_qualified") is False
        and execution.get("whole_product_accepted") is False
        and execution.get("launch_id") == evidence["launch_id"],
        "build_execution_invalid",
    )
    completed_clock(api, evidence, execution)
    validated = execution.get("validated_receipt")
    api.require(
        type(validated) is dict
        and set(validated)
        == {"path", "sha256", "source_count", "artifact_count", "plugin_build_hash"}
        and type(validated["path"]) is str
        and Path(validated["path"]).absolute() == path
        and validated["sha256"] == digest
        and type(validated["source_count"]) is int
        and validated["source_count"] == len(value["sources"])
        and type(validated["artifact_count"]) is int
        and validated["artifact_count"] == len(value["artifacts"])
        and validated["plugin_build_hash"] == value["plugin_build_hash"],
        "validated_receipt_invalid",
    )
    return execution


def completed_node(e: Evidence, reference: dict[str, str]) -> dict[str, Any]:
    api = e.api
    path = normal_path(e.root, reference, api)
    _, raw = e.reference(reference, expected=path)
    value = e.resolved(path, raw, reference["sha256"])
    api.require(
        all(value["sources"].get(name) == digest for name, digest in CURRENT.items()),
        "producer_receipt_binding_invalid",
    )
    execution = completed_execution(e, path, reference["sha256"], value)
    retention_ref = value["evidence"].get("retention_preparation")
    api.require(execution.get("retention_preparation") == retention_ref, "retention_ref_mismatch")
    _, raw = e.reference(
        retention_ref, expected=path.parent / "retention/retained-recipe-preparation.json"
    )
    retention = json.loads(raw)
    api.require(
        type(retention) is dict
        and retention.get("schema_version") == "product-retention-preparation.v1"
        and retention.get("passed") is True
        and retention.get("preparation_only") is False
        and all(
            type(retention.get(k)) is int and retention[k] == 0
            for k in [
                "current_source_overwrites",
                "pointer_writes",
                "builds",
                "migrations",
                "model_submissions",
            ]
        )
        and retention.get("unavailable_prior_sources_not_substituted") == []
        and retention.get("authenticated_current_producer_sources") == CURRENT,
        "retention_binding_invalid",
    )
    validation = retention.get("prior_registry_validation")
    api.require(
        type(validation) is dict
        and validation.get("mode") == "original_producer_actual_installation_root.v1"
        and all(
            validation.get(k) is True
            for k in [
                "every_original_reverified",
                "source_and_artifact_maps_unchanged",
                "all_original_registrations_preserved",
            ]
        ),
        "registry_evidence_invalid",
    )
    reg, rows = registry(e, value)
    api.require(
        retention.get("retained_recipe_archives_v1", {}).get("registry") == reg,
        "registry_evidence_invalid",
    )
    parent = retention.get("prior_receipt")
    normal_path(e.root, parent, api)
    return {
        "reference": reference,
        "parent": parent,
        "registry": reg,
        "rows": rows,
        "validation": validation,
    }


def authenticate_completed_chain(
    root: Path, args: Any, pointer_raw: bytes, api: Any
) -> ChainWitness:
    e = Evidence(root, api)
    for name, digest in CURRENT.items():
        api.require(api.digest(e.read(api.scoped(root, name))) == digest, "producer_changed")
    runtime, runtime_reg, runtime_rows = anchor(e, pointer_raw)
    start = {"path": str(args.prior_receipt_path.absolute()), "sha256": args.prior_receipt_sha256}
    path = normal_path(root, start, api)
    api.require(
        path != Path(runtime["path"]) and start["sha256"] != runtime["sha256"],
        "hop_identity_invalid",
    )
    seen: set[Path] = set()
    nodes: list[dict[str, Any]] = []
    current = start
    while current != runtime:
        path = normal_path(root, current, api)
        api.require(path not in seen, "chain_cycle")
        api.require(len(nodes) < MAX_HOPS, "chain_hop_limit")
        seen.add(path)
        node = completed_node(e, current)
        nodes.append(node)
        current = node["parent"]
    previous_ref, previous_rows = runtime_reg, runtime_rows
    for node in reversed(nodes):
        api.require(node["validation"].get("registry") == previous_ref, "registry_evidence_invalid")
        api.require(
            all(node["rows"].get(k) == v for k, v in previous_rows.items()), "registry_rows_lost"
        )
        previous_ref, previous_rows = node["registry"], node["rows"]
    witness = ChainWitness(
        tuple(e.files.items()),
        tuple(node["reference"] for node in nodes),
        runtime,
        tuple(e.registries),
        root,
        api,
        e.total,
        MAX_HOPS,
        MAX_METADATA_BYTES,
    )
    for name, raw in witness:
        api.require(api.read(api.scoped(root, name)) == raw, "evidence_changed_during_prepare")
    witness.verify_registry_resources()
    return witness
