"""Full synthetic file topology, not a claim of any real build or research."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest
from retained_completed_chain_fixture import (
    archive_fixture,
    invoke,
    load,
    make_chain,
    old,
    reseal,
    seal,
    v2_transport,
)


def test_two_completed_priors_reach_the_real_runtime_without_relabelling_it(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    m = load()
    before = (nodes[0].root / "data/product-local/installed-release-receipt.json").read_bytes()
    try:
        w = invoke(m, nodes)
    except ValueError as exc:
        pytest.fail("valid completed chain rejected: " + str(exc))
    assert w.hop_count == 2
    assert [r["path"] for r in w.completed_refs] == [
        str(nodes[1].root / nodes[1].prior_name),
        str(nodes[0].root / nodes[0].prior_name),
    ]
    assert w.runtime_ref["sha256"] == nodes[0].pointer["receipt_sha256"]
    assert w.report_lineage()["single_hop"] is False
    assert w.report_lineage()["hop_count"] == 2
    assert w.report_lineage()["runtime_installed_receipt"] == w.runtime_ref
    m.verify_unstarted_maintenance_prior(nodes[0].root, w)
    assert (
        nodes[0].root / "data/product-local/installed-release-receipt.json"
    ).read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("passed", False),
        ("passed", 1),
        ("ui_build_attempts", 2),
        ("host_build_exit_code", 1),
        ("model_submissions", 1),
        ("migrations", 1),
        ("process_starts", 1),
        ("process_stops", 1),
        ("pointer_writes", 1),
        ("pointer_writes", True),
        ("ui_build_exit_code", False),
        ("ui_build_attempts", True),
        ("native_execution_qualified", True),
        ("native_execution_qualified", 0),
        ("whole_product_accepted", True),
        ("whole_product_accepted", 0),
    ],
)
def test_every_intermediate_build_must_be_closed_and_non_side_effecting(
    tmp_path: Path, field: str, value: object
):
    nodes = make_chain(tmp_path, 2)
    nodes[0].execution[field] = value
    reseal(nodes)
    with pytest.raises(ValueError, match="build_execution_invalid"):
        invoke(load(), nodes)


def test_cycle_is_rejected_before_following_a_self_path(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    top = nodes[-1]
    top.retention["prior_receipt"] = {"path": str(top.root / top.prior_name), "sha256": "e" * 64}
    seal(top)
    with pytest.raises(ValueError, match="chain_cycle"):
        invoke(load(), nodes)


def test_foreign_root_ref_is_not_an_authorized_ancestor(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    top = nodes[-1]
    top.retention["prior_receipt"] = {"path": str(tmp_path / "foreign.json"), "sha256": "e" * 64}
    seal(top)
    with pytest.raises(ValueError, match="reference_scope_invalid"):
        invoke(load(), nodes)


def test_intermediate_receipt_hash_is_not_accepted_from_its_name(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    nodes[-1].retention["prior_receipt"]["sha256"] = "f" * 64
    seal(nodes[-1])
    with pytest.raises(ValueError, match="reference_hash_mismatch"):
        invoke(load(), nodes)


def test_33_hops_are_rejected_by_the_closed_32_hop_budget(tmp_path: Path):
    nodes = make_chain(tmp_path, 33)
    with pytest.raises(ValueError, match="chain_hop_limit"):
        invoke(load(), nodes)


def test_low_metadata_budget_rejects_the_same_real_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    nodes = make_chain(tmp_path, 2)
    m = load()
    # Baseline has no chain helper; its public entry must still produce the RED rejection.
    helper = sys.modules.get("maintenance_prior_chain")
    if helper is None:
        try:
            __import__("maintenance_prior_chain")
        except ModuleNotFoundError:
            pass
        helper = sys.modules.get("maintenance_prior_chain")
    if helper is not None:
        monkeypatch.setattr(helper, "MAX_METADATA_BYTES", 1024)
    with pytest.raises(ValueError, match="chain_metadata_limit"):
        invoke(m, nodes)


def test_intermediate_transport_must_remain_hash_bound(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    name = v2_transport(nodes[0])
    reseal(nodes)
    path = nodes[0].root / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="product_provenance"):
        invoke(load(), nodes)


def test_witness_rechecks_intermediate_execution_bytes(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    m = load()
    w = invoke(m, nodes)
    (nodes[0].root / "artifacts/prior/build-execution.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="evidence_changed_during_prepare"):
        m.verify_unstarted_maintenance_prior(nodes[0].root, w)


def test_original_archive_validator_and_resource_witness_are_used(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    archive = archive_fixture(nodes)
    m = load()
    w = invoke(m, nodes)
    (nodes[0].root / archive["stored"]).write_bytes(b"changed after full original auth")
    with pytest.raises(ValueError, match="research_recipe_archive_existing_changed"):
        m.verify_unstarted_maintenance_prior(nodes[0].root, w)


def test_intermediate_archive_source_membership_cannot_be_self_rehashed(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    archive = archive_fixture(nodes)
    nodes[0].prior["sources"][archive["stored"]] = "f" * 64
    reseal(nodes)
    with pytest.raises(ValueError, match="research_recipe_archive_existing_changed"):
        invoke(load(), nodes)


def test_every_edge_retains_the_previous_registry_rows(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    archive_fixture(nodes)
    first = nodes[0]
    empty = {"schema_version": "research-retained-recipe-registry.v1", "registrations": []}
    encoded = old.raw(empty)
    digest = old.digest(encoded)
    name = "packages/kernel/research_recipe_versions/registries/" + digest + ".json"
    old.write(first.root, name, encoded)
    ref = {"path": name, "sha256": digest}
    first.prior["sources"][name] = digest
    first.prior["evidence"]["retained_recipe_archives_v1"]["registry"] = ref
    first.retention["retained_recipe_archives_v1"]["registry"] = ref
    first.execution["validated_receipt"]["source_count"] = len(first.prior["sources"])
    reseal(nodes)
    # The next node's original parent registry must be the changed immediate parent.
    nodes[-1].retention["prior_registry_validation"]["registry"] = ref
    reseal(nodes)
    with pytest.raises(ValueError, match="registry_rows_lost"):
        invoke(load(), nodes)


def test_same_installed_completed_optin_and_legacy_default_still_reject(tmp_path: Path):
    nodes = make_chain(tmp_path, 1)
    node = nodes[0]
    m = load()
    args = seal(node)
    args.prior_receipt_path = node.root / node.pointer["receipt_path"]
    args.prior_receipt_sha256 = node.pointer["receipt_sha256"]
    with pytest.raises(ValueError, match="hop_identity_invalid"):
        m.authenticate_unstarted_maintenance_prior(node.root, args, old.raw(node.pointer))
    args = seal(node)
    args.completed_maintenance_prior = False
    with pytest.raises(ValueError, match="producer_changed"):
        m.authenticate_unstarted_maintenance_prior(node.root, args, old.raw(node.pointer))


def test_current_producer_bytes_cannot_drift(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    (nodes[0].root / old.PRODUCER).write_bytes(b"unapproved producer")
    with pytest.raises(ValueError, match="producer_changed"):
        invoke(load(), nodes)


@pytest.mark.parametrize(
    "kind",
    [
        "receipt_root",
        "producer_epoch",
        "retention_unfinished",
        "retention_prior_producer",
        "retention_substitution",
        "retention_ref",
        "clock",
        "registry_source",
    ],
)
def test_each_intermediate_original_identity_is_required(tmp_path: Path, kind: str):
    nodes = make_chain(tmp_path, 2)
    first = nodes[0]
    expected = ""
    if kind == "receipt_root":
        first.prior["repository_root"] = str(tmp_path / "foreign")
        expected = "product_provenance"
    elif kind == "producer_epoch":
        first.prior["sources"][old.CODEC] = "f" * 64
        expected = "producer_receipt_binding"
    elif kind == "retention_unfinished":
        first.retention["preparation_only"] = True
        expected = "retention_binding"
    elif kind == "retention_prior_producer":
        first.retention["authenticated_current_producer_sources"][old.CODEC] = "f" * 64
        expected = "retention_binding"
    elif kind == "retention_substitution":
        first.retention["unavailable_prior_sources_not_substituted"] = ["packages/missing.py"]
        expected = "retention_binding"
    elif kind == "retention_ref":
        reseal(nodes)
        raw = json.loads((first.root / "artifacts/prior/build-execution.json").read_bytes())
        raw["retention_preparation"]["sha256"] = "f" * 64
        old.write(first.root, "artifacts/prior/build-execution.json", old.raw(raw))
        with pytest.raises(ValueError, match="retention_ref_mismatch"):
            # invoke would reseal the top only, leaving the intermediate execution intact.
            invoke(load(), nodes)
        return
    elif kind == "clock":
        first.execution["completed_at"] = "2000-01-01T00:00:00Z"
        expected = "completed_build_clock"
    elif kind == "registry_source":
        reference = first.prior["evidence"]["retained_recipe_archives_v1"]["registry"]
        first.prior["sources"][reference["path"]] = "f" * 64
        expected = "registry_source"
    reseal(nodes)
    with pytest.raises(ValueError, match=expected):
        invoke(load(), nodes)


def test_output_range_is_rejected_before_auth_or_any_producer(tmp_path: Path):
    nodes = make_chain(tmp_path, 2)
    module = load()
    args = seal(nodes[-1])
    args.output_directory = tmp_path / "foreign-output"
    with pytest.raises(ValueError, match="fresh_artifact_directory_required"):
        module.prepare(args)


def test_original_legacy_auth_and_relocation_ast_are_preserved():
    # True ae9 source from 66bad Git blob 8ff1367079b649ff9b31ee84838dd82b613ff3e6.
    # Frozen AST hashes avoid letting a later Main adoption redefine the baseline.
    candidate = Path(__file__).resolve().parents[2] / "infra/dsh/prepare-product-retention.py"
    after = {
        node.name: node
        for node in ast.parse(candidate.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    name = "authenticate_unstarted_maintenance_prior"
    body = after[name].body[1:2] + after[name].body[3:]
    actual = ast.dump(ast.Module(body=body, type_ignores=[])).encode()
    assert (
        hashlib.sha256(actual).hexdigest()
        == "40479d5b76e560ffbe1c617c6506b6f2fec2e33567ed3e95aa55e8fd25c58dad"
    )
    for name, expected in {
        "relocate_preparation_receipt": (
            "aa738b69f35b046d3c623c52b00ee74b622e2151e1451fb9f625989b60129c54"
        ),
        "_maintenance_reference": (
            "214f65efa2e3f01964daaa784042b35e5b52c449534a0a9e3254fbd3fe5f498a"
        ),
    }.items():
        assert hashlib.sha256(ast.dump(after[name]).encode()).hexdigest() == expected
