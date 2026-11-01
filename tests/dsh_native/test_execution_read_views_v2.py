"""Read-only views of real prepared V2 rows; no Host or model execution."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application.research_binding import (
    COMPILED_ROLE,
    PACK_ROLE,
    TASK_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    canonical_recipe,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionPromptRecord,
    RunRoleOutputRecord,
)
from packages.query_views.evolution_readiness import evolution_readiness
from tests.dsh_native.test_execution_preparation_v2 import (
    PreparedInputs,
    _advance_inputs,  # pyright: ignore[reportPrivateUsage]
    _snapshot,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tools.ops.audit_research_method import audit

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


@pytest.mark.parametrize("consumer", ["audit", "readiness"])
def test_unsubmitted_complete_v2_preparation_is_readable_without_execution_claim(
    inputs: PreparedInputs, consumer: str, tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    before = _snapshot(inputs.database, inputs.request.run_id)
    observed: dict[str, object] = {}
    try:
        if consumer == "audit":
            value = audit(inputs.database, inputs.request.run_id)
            observed["view"] = value
            assert value["receipt"]["schema_version"] == "research-execution-method.v2"
            assert value["status"] == "submission_not_recorded"
            assert value["checks"]["complete_execution_preparation"] is True
            assert value["research_policy_status"] == "verified"
            assert value["research_policy_execution_status"] == "not_available"
            assert value["result_saved"] is False
            assert value["deployment_connected"] is False
            assert value["provider_configuration_attested"] is False
        else:
            value = evolution_readiness(inputs.database, inputs.now).research_method
            observed["view"] = value
            assert value["latest_receipt_status"] == "verified"
            assert value["latest_receipt"] is not None
            assert value["latest_receipt"]["schema_version"] == "research-execution-method.v2"
            assert value["request_pack_status"] == "verified"
            assert value["research_policy_status"] == "verified"
            assert value["research_policy_execution_status"] == "not_available"
            assert value["model_dispatch"]["status"] == "not_recorded"
            assert value["child_model_dispatch"]["status"] == "not_recorded"
            assert value["deployment_connected"] is False
    except Exception as exc:
        observed["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        after = _snapshot(inputs.database, inputs.request.run_id)
        (tmp_path / "read-view-observation.json").write_text(json.dumps({
            "consumer": consumer, "prepared": prepared.method.model_dump(mode="json"),
            "before": before, "after": after, **observed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert after == before


@pytest.mark.parametrize("consumer", ["audit", "readiness"])
@pytest.mark.parametrize("damage", [
    "method_missing", "execution_missing", "integrity_missing", "prompt_missing",
    "bundle_raw_drift", "execution_raw_drift", "unknown_method", "mixed_v1_method",
])
def test_partial_unknown_or_mixed_v2_rows_are_not_verified_or_backfilled(
    inputs: PreparedInputs, consumer: str, damage: str, tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    original = _snapshot(inputs.database, inputs.request.run_id)
    with inputs.database.session() as session:
        if damage == "prompt_missing":
            prompt = session.get(DshSessionPromptRecord, (inputs.request.run_id, 1))
            assert prompt is not None
            session.delete(prompt)
        else:
            role = {
                "method_missing": "research_execution_method",
                "execution_missing": "dsh_execution_request:1",
                "integrity_missing": "dsh_prompt_integrity:1",
                "bundle_raw_drift": "research_request_pack",
                "execution_raw_drift": "dsh_execution_request:1",
                "unknown_method": "research_execution_method",
                "mixed_v1_method": "research_execution_method",
            }[damage]
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role=role,
            ).one()
            if damage.endswith("_missing"):
                session.delete(row)
            elif damage.endswith("_raw_drift"):
                row.payload_json += " "
            elif damage == "unknown_method":
                row.schema_version = "research-execution-method.unknown"
            else:
                definition = prepared.method.definition.model_dump(mode="json")
                body = {"schema_version": "research-execution-method.v1",
                        "run_id": inputs.request.run_id,
                        "request_hash": prepared.method.request_hash,
                        "definition": definition,
                        "method_hash": hashlib.sha256(
                            canonical_recipe(definition).encode()).hexdigest()}
                row.schema_version = "research-execution-method.v1"
                row.payload_json = canonical_recipe(body)
                row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    before_read = _snapshot(inputs.database, inputs.request.run_id)
    observed: dict[str, object] = {}
    try:
        if consumer == "audit":
            with pytest.raises(ValueError) as rejected:
                audit(inputs.database, inputs.request.run_id)
            observed["rejection"] = str(rejected.value)
        else:
            value = evolution_readiness(inputs.database, inputs.now).research_method
            observed["view"] = value
            assert value["latest_receipt_status"] == "invalid"
            assert value["latest_receipt"] is None
            assert value["deployment_connected"] is False
    finally:
        after_read = _snapshot(inputs.database, inputs.request.run_id)
        (tmp_path / "read-view-observation.json").write_text(json.dumps({
            "consumer": consumer, "damage": damage, "original": original,
            "before_read": before_read, "after_read": after_read, **observed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert before_read != original and after_read == before_read


@pytest.mark.parametrize("consumer", ["audit", "readiness"])
def test_bundle_without_any_execution_is_unprepared_and_is_not_backfilled(
    inputs: PreparedInputs, consumer: str, tmp_path: Path,
) -> None:
    before = _snapshot(inputs.database, inputs.request.run_id)
    if consumer == "audit":
        value = audit(inputs.database, inputs.request.run_id)
        assert value["status"] == "method_not_recorded" and value["verified"] is False
    else:
        readiness = evolution_readiness(inputs.database, inputs.now).research_method
        value = dict(readiness)
        assert readiness["latest_receipt_status"] == "not_recorded"
        assert readiness["latest_receipt"] is None
        assert readiness["deployment_connected"] is False
    after = _snapshot(inputs.database, inputs.request.run_id)
    (tmp_path / "read-view-observation.json").write_text(json.dumps({
        "consumer": consumer, "before": before, "after": after, "view": value,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert after == before


@pytest.mark.parametrize("consumer", ["audit", "readiness"])
@pytest.mark.parametrize("missing_current", [False, True])
def test_current_generation_two_is_validated_even_when_first_generation_is_intact(
    inputs: PreparedInputs, consumer: str, missing_current: bool, tmp_path: Path,
) -> None:
    inputs.prepare()
    # This fixture uses a controlled Kernel completion to advance, not a Host
    # or Provider execution. It cannot attest the model or publication.
    next_inputs = _advance_inputs(inputs)
    prepared = next_inputs.prepare()
    assert prepared.execution.generation == 2
    if missing_current:
        with inputs.database.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role="dsh_execution_request:2",
            ).one()
            session.delete(row)
    before = _snapshot(inputs.database, inputs.request.run_id)
    observed: dict[str, object] = {}
    try:
        if consumer == "audit" and missing_current:
            with pytest.raises(ValueError) as rejected:
                audit(inputs.database, inputs.request.run_id)
            observed["rejection"] = str(rejected.value)
        elif consumer == "audit":
            value = audit(inputs.database, inputs.request.run_id)
            observed["view"] = value
            assert value["receipt"]["request_hash"] == inputs.submit.request_hash
            assert value["research_policy_execution_status"] == "not_available"
            assert value["deployment_connected"] is False
            assert value["prompt_generations"] == 2
            assert value["checks"]["complete_execution_preparation"] is True
        else:
            readiness = evolution_readiness(inputs.database, inputs.now).research_method
            observed["view"] = readiness
            if missing_current:
                assert readiness["latest_receipt_status"] == "invalid"
            else:
                assert readiness["latest_receipt"] is not None
                assert readiness["latest_receipt"]["request_hash"] == inputs.submit.request_hash
                assert readiness["research_policy_execution_status"] == "not_available"
                assert readiness["deployment_connected"] is False
                assert readiness["latest_receipt_status"] == "verified"
    finally:
        after = _snapshot(inputs.database, inputs.request.run_id)
        (tmp_path / "read-view-observation.json").write_text(json.dumps({
            "consumer": consumer, "missing_current": missing_current,
            "before": before, "after": after, **observed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert before == after


@pytest.mark.parametrize("consumer", ["audit", "readiness"])
@pytest.mark.parametrize("damage", ["method_and_compiled", "all_bundle_and_method"])
def test_orphan_execution_is_not_hidden_by_an_older_valid_method(
    inputs: PreparedInputs, consumer: str, damage: str, tmp_path: Path,
) -> None:
    old_run, _ = RunService(inputs.database).create("older-valid-method")
    old_receipt = ResearchMethodService(
        inputs.database, clock=lambda: inputs.now - timedelta(days=1),
    ).freeze(
        old_run, {"upstream_identity": inputs.upstream.model_dump(mode="json")},
        request_hash="e" * 64,
    )
    assert old_receipt is not None
    prepared = inputs.prepare()
    original = _snapshot(inputs.database, inputs.request.run_id)
    old_before = _snapshot(inputs.database, old_run)
    deleted = {"research_execution_method", COMPILED_ROLE}
    if damage == "all_bundle_and_method":
        deleted.update({PACK_ROLE, TASK_ROLE})
    with inputs.database.session() as session:
        rows = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == inputs.request.run_id,
            RunRoleOutputRecord.role.in_(deleted),
        ).all()
        assert {row.role for row in rows} == deleted
        for row in rows:
            session.delete(row)
    before = _snapshot(inputs.database, inputs.request.run_id)
    observed: dict[str, object] = {}
    try:
        if consumer == "audit":
            with pytest.raises(ValueError, match="research_execution_method_missing"):
                observed["view"] = audit(inputs.database, inputs.request.run_id)
        else:
            value = evolution_readiness(inputs.database, inputs.now).research_method
            observed["view"] = value
            assert value["latest_receipt_status"] == "invalid"
            assert value["latest_receipt"] is None
            assert value["incomplete_execution_run_id"] == inputs.request.run_id
            assert value["deployment_connected"] is False
    finally:
        after = _snapshot(inputs.database, inputs.request.run_id)
        old_after = _snapshot(inputs.database, old_run)
        (tmp_path / "read-view-observation.json").write_text(json.dumps({
            "consumer": consumer, "damage": damage, "prepared": prepared.method.model_dump(
                mode="json"), "original": original, "before": before, "after": after,
            "old_receipt": old_receipt, "old_before": old_before, "old_after": old_after,
            **observed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert before == after and old_before == old_after
