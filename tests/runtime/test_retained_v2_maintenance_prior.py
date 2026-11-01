"""File-only prior authentication; synthetic negatives are not real build evidence."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

MAIN = Path("E:/project/study/codex/dsh/decision-hub")
CANDIDATE = Path(__file__).resolve().parents[2] / "infra/dsh/prepare-product-retention.py"
NORMAL = (
    "artifacts/incremental-delivery-20261024/contract-compatibility-build/"
    "product-validated-builds.json"
)
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


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("candidate_retained_maintenance", CANDIDATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def raw(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def write(root: Path, name: str, content: bytes) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return digest(content)


def fixture(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "repo"
    root.mkdir()
    for name, expected in CURRENT.items():
        content = (MAIN / name).read_bytes()
        assert digest(content) == expected
        write(root, name, content)
    old_registry: dict[str, Any] = {
        "schema_version": "research-retained-recipe-registry.v1",
        "registrations": [],
    }
    registry_hash = digest(raw(old_registry))
    registry_name = "packages/kernel/research_recipe_versions/registries/" + registry_hash + ".json"
    write(root, registry_name, raw(old_registry))
    registry_ref = {"path": registry_name, "sha256": registry_hash}
    runtime_name = "artifacts/runtime/product-validated-builds.json"
    runtime = {
        "schema": "product-validated-builds.v1",
        "repository_root": str(root),
        "sources": RUNTIME | {registry_name: registry_hash},
        "artifacts": {"fixture-not-a-real-build": "a" * 64},
        "plugin_build_hash": "b" * 64,
        "evidence": {"retained_recipe_archives_v1": {"registry": registry_ref}},
    }
    runtime_hash = write(root, runtime_name, raw(runtime))
    pointer = {
        "schema_version": "product-installed-release-pointer.v1",
        "repository_root": str(root),
        "receipt_path": runtime_name,
        "receipt_sha256": runtime_hash,
    }
    write(root, "data/product-local/installed-release-receipt.json", raw(pointer))
    prior_name = "artifacts/prior/product-validated-builds.json"
    retention_name = "artifacts/prior/retention/retained-recipe-preparation.json"
    retention: dict[str, Any] = {
        "schema_version": "product-retention-preparation.v1",
        "passed": True,
        "preparation_only": False,
        "prior_receipt": {"path": str(root / runtime_name), "sha256": runtime_hash},
        "current_source_overwrites": 0,
        "pointer_writes": 0,
        "builds": 0,
        "migrations": 0,
        "model_submissions": 0,
        "unavailable_prior_sources_not_substituted": [],
        "authenticated_current_producer_sources": dict(CURRENT),
        "prior_registry_validation": {
            "mode": "original_producer_actual_installation_root.v1",
            "every_original_reverified": True,
            "source_and_artifact_maps_unchanged": True,
            "all_original_registrations_preserved": True,
            "registry": registry_ref,
        },
        "retained_recipe_archives_v1": {"registry": registry_ref},
    }
    retention_ref = {
        "path": str(root / retention_name),
        "sha256": write(root, retention_name, raw(retention)),
    }
    prior: dict[str, Any] = {
        "schema": "product-validated-builds.v1",
        "repository_root": str(root),
        "sources": CURRENT | {registry_name: registry_hash},
        "artifacts": {"fixture-not-a-real-build": "c" * 64},
        "plugin_build_hash": "d" * 64,
        "evidence": {
            "origin": "standard_product_build_only",
            "ui_build_count": 1,
            "host_build_count": 1,
            "source_stable_except_standard_generated_identity": True,
            "launch_id": "20261024T165610074Z",
            "observed_at": "2026-10-24T17:18:07+00:00",
            "native_execution_qualified": False,
            "eligible_for_deployment": False,
            "retention_preparation": retention_ref,
            "retained_recipe_archives_v1": {"registry": registry_ref},
        },
    }
    prior_hash = write(root, prior_name, raw(prior))
    execution = {
        "schema_version": "product-build-execution.v1",
        "passed": True,
        "started_at": "2026-10-24T16:56:10+00:00",
        "completed_at": "2026-10-24T17:18:08+00:00",
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
        "error": None,
        "native_execution_qualified": False,
        "whole_product_accepted": False,
        "launch_id": prior["evidence"]["launch_id"],
        "retention_preparation": retention_ref,
        "validated_receipt": {
            "path": str(root / prior_name),
            "sha256": prior_hash,
            "source_count": len(prior["sources"]),
            "artifact_count": len(prior["artifacts"]),
            "plugin_build_hash": prior["plugin_build_hash"],
        },
    }
    write(root, "artifacts/prior/build-execution.json", raw(execution))
    return SimpleNamespace(
        root=root,
        pointer=pointer,
        prior=prior,
        execution=execution,
        retention=retention,
        prior_name=prior_name,
        retention_name=retention_name,
    )


def seal(case: SimpleNamespace) -> SimpleNamespace:
    ref = {
        "path": str(case.root / case.retention_name),
        "sha256": write(case.root, case.retention_name, raw(case.retention)),
    }
    case.prior["evidence"]["retention_preparation"] = ref
    case.execution["retention_preparation"] = ref
    prior_hash = write(case.root, case.prior_name, raw(case.prior))
    case.execution["validated_receipt"]["sha256"] = prior_hash
    write(case.root, "artifacts/prior/build-execution.json", raw(case.execution))
    return SimpleNamespace(
        prior_receipt_path=case.root / case.prior_name,
        prior_receipt_sha256=prior_hash,
        completed_maintenance_prior=True,
        repository_root=case.root,
    )


def test_actual_completed_6dd_prior_is_authenticated_without_changing_installed_49c() -> None:
    module = load()
    pointer = MAIN / "data/product-local/installed-release-receipt.json"
    before = pointer.read_bytes()
    args = SimpleNamespace(
        prior_receipt_path=MAIN / NORMAL,
        prior_receipt_sha256="6dd435853656710aa85748dad31def411c2ea94ba8e53c7cee407e2b02a02513",
        completed_maintenance_prior=True,
    )
    try:
        witness = module.authenticate_unstarted_maintenance_prior(MAIN, args, before)
    except ValueError as exc:
        pytest.fail(f"real completed standard build was rejected: {exc}")
    assert len(witness) >= 8
    module.verify_unstarted_maintenance_prior(MAIN, witness)
    assert pointer.read_bytes() == before
    assert (
        json.loads(before)["receipt_sha256"]
        == "2bc144fc4bdc3dcaa11766aea1927d71e9d8dfe32753baaad9a71d7138d567e6"
    )


def test_completed_build_requires_explicit_opt_in(tmp_path: Path) -> None:
    case = fixture(tmp_path)
    args = seal(case)
    args.completed_maintenance_prior = False
    with pytest.raises(ValueError, match="retention_maintenance_producer_changed"):
        load().authenticate_unstarted_maintenance_prior(case.root, args, raw(case.pointer))


def test_closed_completed_fixture_is_accepted_and_witness_is_rechecked(tmp_path: Path) -> None:
    case = fixture(tmp_path)
    module = load()
    witness = module.authenticate_unstarted_maintenance_prior(
        case.root, seal(case), raw(case.pointer)
    )
    (case.root / CODEC).write_bytes(b"# changed after authentication\n")
    with pytest.raises(ValueError, match="evidence_changed_during_prepare"):
        module.verify_unstarted_maintenance_prior(case.root, witness)


@pytest.mark.parametrize(
    "field,value",
    [
        ("passed", False),
        ("ui_build_exit_code", 1),
        ("host_build_attempts", 2),
        ("process_starts", 1),
        ("process_stops", 1),
        ("migrations", 1),
        ("model_submissions", 1),
        ("pointer_writes", 1),
        ("whole_product_accepted", True),
        ("completed_at", None),
        ("completed_at", "2026-10-24T15:00:00+00:00"),
    ],
)
def test_incomplete_or_side_effecting_build_is_rejected(
    tmp_path: Path, field: str, value: Any
) -> None:
    case = fixture(tmp_path)
    case.execution[field] = value
    with pytest.raises(ValueError):
        load().authenticate_unstarted_maintenance_prior(case.root, seal(case), raw(case.pointer))


@pytest.mark.parametrize(
    "kind", ["foreign_prior", "producer", "pointer_root", "retention_runtime", "registry_claim"]
)
def test_foreign_or_changed_chain_is_rejected(tmp_path: Path, kind: str) -> None:
    case = fixture(tmp_path)
    if kind == "foreign_prior":
        case.prior["evidence"]["origin"] = "untrusted_fixture"
    elif kind == "producer":
        (case.root / PRODUCER).write_bytes(b"# untrusted producer\n")
    elif kind == "pointer_root":
        case.pointer["repository_root"] = str(tmp_path / "foreign")
    elif kind == "retention_runtime":
        case.retention["prior_receipt"]["sha256"] = "f" * 64
    elif kind == "registry_claim":
        case.retention["prior_registry_validation"]["all_original_registrations_preserved"] = False
    with pytest.raises(ValueError):
        load().authenticate_unstarted_maintenance_prior(case.root, seal(case), raw(case.pointer))


def test_out_of_repository_output_is_rejected_before_any_capture(tmp_path: Path) -> None:
    module = load()
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "forbidden-output"
    with pytest.raises(ValueError, match="retention_fresh_artifact_directory_required"):
        module.prepare(SimpleNamespace(repository_root=root, output_directory=outside))
    assert not outside.exists()


def test_completed_opt_in_cannot_skip_auth_when_prior_is_installed(tmp_path: Path) -> None:
    case = fixture(tmp_path)
    args = seal(case)
    args.prior_receipt_path = case.root / case.pointer["receipt_path"]
    args.prior_receipt_sha256 = case.pointer["receipt_sha256"]
    args.output_directory = case.root / "artifacts/refused-completed"
    args.source_inventory = case.root / "inventory.json"
    args.source_inventory.write_bytes(raw(case.prior["sources"]))
    args.original_source_root = []
    args.captured_v3_preparation = None
    args.captured_v3_preparation_sha256 = None
    args.captured_v3_source_root = None
    args.selection_database = None
    args.install_candidates = False
    with pytest.raises(ValueError, match="retention_maintenance_hop_identity_invalid"):
        load().prepare(args)
    assert not args.output_directory.exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_bound_v2_transport_change_is_rejected(tmp_path: Path, when: str) -> None:
    from packages.kernel.decision_hub_kernel.application import product_release_provenance as codec

    assert Path(codec.__file__).resolve() == MAIN / CODEC
    case = fixture(tmp_path)
    encoded, payloads = codec.encode_historical_sources(
        case.prior["sources"], read_resource=lambda name: (case.root / name).read_bytes()
    )
    for name, content in payloads.items():
        write(case.root, name, content)
    case.prior.update(encoded)
    case.prior["schema"] = "product-validated-builds.v2"
    args = seal(case)
    module = load()
    witness = None
    if when == "after":
        witness = module.authenticate_unstarted_maintenance_prior(
            case.root, args, raw(case.pointer)
        )
    path = case.root / next(iter(payloads))
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        if witness is None:
            module.authenticate_unstarted_maintenance_prior(case.root, args, raw(case.pointer))
        else:
            module.verify_unstarted_maintenance_prior(case.root, witness)


def test_rehashed_receipt_cannot_change_producer_epoch_binding(tmp_path: Path) -> None:
    case = fixture(tmp_path)
    case.prior["sources"][CODEC] = RUNTIME[CODEC]
    with pytest.raises(ValueError, match="producer_receipt_binding_invalid"):
        load().authenticate_unstarted_maintenance_prior(case.root, seal(case), raw(case.pointer))


def test_prior_bytes_are_rehashed_before_authentication(tmp_path: Path) -> None:
    case = fixture(tmp_path)
    args = seal(case)
    args.prior_receipt_path.write_bytes(args.prior_receipt_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="reference_hash_mismatch"):
        load().authenticate_unstarted_maintenance_prior(case.root, args, raw(case.pointer))


def test_completed_relocation_preserves_runtime_identity_and_all_maps(tmp_path: Path) -> None:
    case = fixture(tmp_path)
    module = load()
    args = seal(case)
    try:
        relocated = module.relocate_preparation_receipt(
            args.prior_receipt_path.read_bytes(), case.prior, tmp_path / "view", args, case.pointer
        )
    except AttributeError as exc:
        pytest.fail(f"completed prior has no truthful preparation relocation: {exc}")
    evidence = relocated["evidence"]
    assert "original_installed_receipt" not in evidence
    assert evidence["original_completed_build_receipt"]["sha256"] == args.prior_receipt_sha256
    assert evidence["original_runtime_installed_receipt"] == {
        "path": str(case.root / case.pointer["receipt_path"]),
        "sha256": case.pointer["receipt_sha256"],
    }
    assert relocated["sources"] == case.prior["sources"]
    assert relocated["artifacts"] == case.prior["artifacts"]
    args.completed_maintenance_prior = False
    legacy = module.relocate_preparation_receipt(
        args.prior_receipt_path.read_bytes(), case.prior, tmp_path / "view", args, case.pointer
    )
    assert "original_installed_receipt" in legacy["evidence"]
    assert "original_completed_build_receipt" not in legacy["evidence"]


@pytest.mark.parametrize("enabled", [False, True])
def test_standard_build_forwards_completed_prior_only_on_opt_in(enabled: bool) -> None:
    # Execute the real parameter block and argument-construction AST only: no build/start.
    build = CANDIDATE.with_name("build-product.ps1").as_posix()
    script = f"""
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{build}',[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) {{ throw 'parse failed' }}
$assignment=$ast.FindAll({{param($n)
  $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
  $n.Left.Extent.Text -eq '$retentionArgs' -and $n.Operator -eq 'Equals'
}},$true)[0]
$option=$ast.FindAll({{param($n)
  $n -is [System.Management.Automation.Language.IfStatementAst] -and
  $n.Clauses[0].Item1.Extent.Text -eq '$CompletedMaintenancePrior'
}},$true)
$body=($ast.ParamBlock.Extent.Text+"`n"+$assignment.Extent.Text+"`n"+
  ($option | ForEach-Object {{ $_.Extent.Text }})+
  "`nConvertTo-Json -InputObject @(`$retentionArgs) -Compress")
$repoRoot='repo';$priorPath='prior';$inventoryPath='inventory';$retentionRoot='retention'
$block=[scriptblock]::Create($body)
& $block -OutputDirectory 'fixture' -PriorValidatedBuildReceipt 'fixture' `
  -PriorValidatedBuildReceiptSha256 ('a'*64) `
  -CompletedMaintenancePrior:${str(enabled).lower()}
if (-not $?) {{ exit 1 }}
"""
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    arguments = json.loads(result.stdout)
    assert arguments.count("--completed-maintenance-prior") == int(enabled)
    assert arguments[:4] == ["--repository-root", "repo", "--prior-receipt-path", "prior"]
