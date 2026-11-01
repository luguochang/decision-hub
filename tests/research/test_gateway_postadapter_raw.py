"""Actual offline adapter-return facts survive the following Kernel checks."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityGateway
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs, inputs
from tests.research.test_gateway_execution_v2 import (
    ControlledAdapter,
    _gateway,  # pyright: ignore[reportPrivateUsage]
    _intent,  # pyright: ignore[reportPrivateUsage]
    _live_inputs,  # pyright: ignore[reportPrivateUsage]
    _manifest,  # pyright: ignore[reportPrivateUsage]
    _observe,  # pyright: ignore[reportPrivateUsage]
    _query,  # pyright: ignore[reportPrivateUsage]
    _snapshot,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
    live_inputs,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and live_inputs is not None and forbid_external_connections is not None


class ReturnedAdapter(ControlledAdapter):
    def __init__(self, source: PreparedInputs, *, live: bool = False,
                 edit: Callable[[ResearchCapabilityResult], ResearchCapabilityResult] | None = None,
                 ):
        super().__init__(source)
        if live:
            self.capability_id = "web.search"
            self.supported_modes = frozenset({"live"})
        self.edit = edit
        self.returned: ResearchCapabilityResult | None = None

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        actual = await super().execute(query)
        self.returned = self.edit(actual) if self.edit else actual
        return self.returned


def _actual_inner(source: PreparedInputs, adapter: ReturnedAdapter,
                  clock: Callable[[], datetime]) -> ResearchCapabilityGatewayService:
    manifest = _manifest().model_copy(update={
        "capability_id": adapter.capability_id, "allowed_domains": ["example.test"],
    })
    return ResearchCapabilityGatewayService([manifest], [adapter],
        enabled_capabilities=[adapter.capability_id], clock=clock)


def _raw_sql(source: PreparedInputs) -> dict[str, Any]:
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord

    observed = _snapshot(source.database, source.request.run_id)
    with source.database.session() as session:
        observed["raw_sidecars"] = [{column.name: getattr(row, column.name)
                                      for column in row.__table__.columns}
            for row in session.query(RunRoleOutputRecord).filter(
                RunRoleOutputRecord.run_id == source.request.run_id,
                RunRoleOutputRecord.role.startswith("research_adapter_return:"))]
    return observed


def _assert_raw_denial(observed: dict[str, Any], adapter: ReturnedAdapter) -> None:
    assert adapter.returned is not None and len(adapter.calls) == 1
    assert observed["evidence"] == observed["facts"] == [] and observed["reuse_count"] == 0
    reservation = observed["reservations"][0]
    assert reservation["status"] == "completed", "actual adapter return was recorded as failed"
    assert reservation["result_json"] is not None and reservation["error_json"] is not None
    assert json.loads(reservation["result_json"]) == adapter.returned.model_dump(mode="json")
    assert len(observed["raw_sidecars"]) == 1, "the original typed return has no immutable audit"
    raw = json.loads(observed["raw_sidecars"][0]["payload_json"])
    assert json.loads(raw["adapter_result_json"]) == adapter.returned.model_dump(mode="json")


async def test_actual_adapter_legal_control_reaches_normalized_success(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    adapter = ReturnedAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    gateway.inner = _actual_inner(inputs, adapter, lambda: inputs.now)
    result = await gateway.execute(_query(inputs))
    observed = _raw_sql(inputs)
    _observe(tmp_path, {"actual_return": adapter.returned.model_dump(mode="json")
                       if adapter.returned else None, "result": result.model_dump(mode="json"),
                       "sql": observed, "adapter_calls": len(adapter.calls)})
    assert adapter.returned is not None and result.cost_usd == 0.01 and len(adapter.calls) == 1
    assert observed["reservations"][0]["status"] == "completed"
    assert len(observed["evidence"]) == 1


async def test_postreturn_server_clock_failure_retains_raw_and_reported_cost(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    adapter = ReturnedAdapter(inputs)
    clock_calls: list[str] = []

    def clock() -> datetime:
        clock_calls.append("clock")
        if len(clock_calls) == 1:
            return inputs.now
        raise OSError("controlled_postadapter_server_clock_failed")

    gateway = _gateway(inputs, adapter)
    gateway.inner = _actual_inner(inputs, adapter, clock)
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.execute(_query(inputs))
    observed = _raw_sql(inputs)
    _observe(tmp_path, {"clock_calls": len(clock_calls), "adapter_calls": len(adapter.calls),
        "actual_return": adapter.returned.model_dump(mode="json") if adapter.returned else None,
        "error": error.value.provenance().model_dump(mode="json"),
        "cause_type": type(error.value.__cause__).__name__, "sql": observed})
    assert len(clock_calls) == 2 and adapter.returned is not None
    _assert_raw_denial(observed, adapter)


async def test_postreturn_live_cutoff_failure_retains_raw_and_reported_cost(
    live_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    live_inputs.prepare()
    adapter = ReturnedAdapter(live_inputs, live=True)
    clock_calls: list[datetime] = []

    def clock() -> datetime:
        value = (live_inputs.now if not clock_calls
                 else live_inputs.request.deadline_at + timedelta(seconds=1))
        clock_calls.append(value)
        return value

    gateway = _gateway(live_inputs, adapter)
    gateway.inner = _actual_inner(live_inputs, adapter, clock)
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.invoke_live(_intent(live_inputs))
    observed = _raw_sql(live_inputs)
    _observe(tmp_path, {"clock_calls": [value.isoformat() for value in clock_calls],
        "adapter_calls": len(adapter.calls),
        "actual_return": adapter.returned.model_dump(mode="json") if adapter.returned else None,
        "error": error.value.provenance().model_dump(mode="json"), "sql": observed})
    assert error.value.error_code == "research_pit_violation" and len(clock_calls) == 2
    _assert_raw_denial(observed, adapter)


async def test_postreturn_candidate_validation_retains_raw_and_reported_cost(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()

    def tamper(result: ResearchCapabilityResult) -> ResearchCapabilityResult:
        candidate = result.evidence_candidates[0].model_copy(update={"content_hash": "f" * 64})
        return result.model_copy(update={"evidence_candidates": [candidate]})

    adapter = ReturnedAdapter(inputs, edit=tamper)
    gateway = _gateway(inputs, adapter)
    gateway.inner = _actual_inner(inputs, adapter, lambda: inputs.now)
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.execute(_query(inputs))
    observed = _raw_sql(inputs)
    _observe(tmp_path, {"adapter_calls": len(adapter.calls),
        "actual_return": adapter.returned.model_dump(mode="json") if adapter.returned else None,
        "error": error.value.provenance().model_dump(mode="json"), "sql": observed})
    assert error.value.error_code == "research_content_hash_mismatch"
    _assert_raw_denial(observed, adapter)


@pytest.mark.parametrize("fault", ["lineage", "overbudget", "unknown_cost", "fact"])
async def test_result_validation_denial_preserves_original_values_and_exact_retry(
    inputs: PreparedInputs, tmp_path: Path, fault: str,
) -> None:
    inputs.prepare()

    def edit(result: ResearchCapabilityResult) -> ResearchCapabilityResult:
        if fault == "lineage":
            return result.model_copy(update={"request_id": "foreign-returned-call"})
        if fault in {"overbudget", "unknown_cost"}:
            return result.model_copy(update={"cost_usd": 0.2 if fault == "overbudget" else None})
        candidate = result.evidence_candidates[0]
        fact = FactEnvelope(schema_version="fact-envelope.v1", fact_id="actual-bad-fact",
            evidence_id=candidate.evidence_id, requirement_id=candidate.requirement_id,
            metric_family="macro.event", field="signal", instrument=None, venue=None,
            value="actual typed fact", unit="text", window_start_at=None, window_end_at=None,
            event_offset=None, observed_at=candidate.observed_at, received_at=candidate.received_at,
            published_at=candidate.published_at, source_id=candidate.source_id,
            independence_group=candidate.source_id, quality="candidate", delay_class="realtime",
            payload_schema_ref="test.fact.v1", payload_hash="f" * 64, attributes={})
        return result.model_copy(update={"facts": [fact]})

    adapter = ReturnedAdapter(inputs, edit=edit)
    gateway = _gateway(inputs, adapter)
    expected = {"lineage": "research_lineage_mismatch", "overbudget": "research_budget_exceeded",
                "unknown_cost": "research_cost_unknown",
                "fact": "research_fact_hash_mismatch"}[fault]
    with pytest.raises(ResearchCapabilityError) as first:
        await gateway.execute(_query(inputs))
    first_sql = _raw_sql(inputs)
    with pytest.raises(ResearchCapabilityError) as second:
        await gateway.execute(_query(inputs))
    second_sql = _raw_sql(inputs)
    _observe(tmp_path, {"fault": fault, "actual_return": adapter.returned.model_dump(mode="json")
        if adapter.returned else None, "error": first.value.provenance().model_dump(mode="json"),
        "repeat_error": second.value.provenance().model_dump(mode="json"),
        "adapter_calls": len(adapter.calls), "sql": first_sql, "repeat_sql": second_sql})
    assert first.value.error_code == second.value.error_code == expected
    _assert_raw_denial(first_sql, adapter)
    assert second_sql == first_sql


@pytest.mark.parametrize("fault", ["naive_clock", "future_provider_time", "resolver"])
async def test_postreturn_live_time_and_resolver_errors_do_not_change_raw_time(
    live_inputs: PreparedInputs, tmp_path: Path, fault: str,
) -> None:
    live_inputs.prepare()
    clock_calls = 0

    def edit(result: ResearchCapabilityResult) -> ResearchCapabilityResult:
        return (result.model_copy(update={"completed_at": live_inputs.now + timedelta(seconds=1)})
                if fault == "future_provider_time" else result)

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return (live_inputs.now.replace(tzinfo=None)
                if fault == "naive_clock" and clock_calls == 2 else live_inputs.now)

    def resolver() -> dict[str, datetime]:
        if fault == "resolver":
            raise OSError("controlled_archive_resolver_after_actual_return")
        return {}

    adapter = ReturnedAdapter(live_inputs, live=True, edit=edit)
    gateway = _gateway(live_inputs, adapter)
    inner = _actual_inner(live_inputs, adapter, clock)
    inner._archived_observation_resolver = resolver  # pyright: ignore[reportPrivateUsage]
    gateway.inner = inner
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.invoke_live(_intent(live_inputs))
    observed = _raw_sql(live_inputs)
    _observe(tmp_path, {"fault": fault, "error": error.value.provenance().model_dump(mode="json"),
        "cause_type": type(error.value.__cause__).__name__, "adapter_calls": len(adapter.calls),
        "clock_calls": clock_calls, "actual_return": adapter.returned.model_dump(mode="json")
        if adapter.returned else None, "sql": observed})
    _assert_raw_denial(observed, adapter)
    assert clock_calls == 2
    if fault == "future_provider_time":
        assert error.value.cause_code == "provider_timestamp_future"
    else:
        assert isinstance(error.value.__cause__, ValueError if fault == "naive_clock" else OSError)


@pytest.mark.parametrize("fault", ["provider", "timeout", "owner_cancel"])
async def test_no_adapter_return_does_not_fabricate_raw_sidecar_or_fee(
    inputs: PreparedInputs, tmp_path: Path, fault: str,
) -> None:
    inputs.prepare()

    class NoReturnAdapter(ReturnedAdapter):
        async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
            self.calls.append(query)
            if fault == "owner_cancel":
                raise asyncio.CancelledError("controlled_owner_cancel_before_return")
            if fault == "timeout":
                raise TimeoutError("controlled_adapter_timeout_before_return")
            raise ResearchCapabilityError("controlled_provider_failed", "No returned typed output")

    adapter = NoReturnAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    expected_type = asyncio.CancelledError if fault == "owner_cancel" else ResearchCapabilityError
    with pytest.raises(expected_type) as error:
        await gateway.execute(_query(inputs))
    observed = _raw_sql(inputs)
    _observe(tmp_path, {"fault": fault, "error_type": type(error.value).__name__,
        "adapter_calls": len(adapter.calls), "actual_return": None, "sql": observed})
    assert adapter.returned is None and len(adapter.calls) == 1
    assert observed["raw_sidecars"] == [] and observed["reservations"][0]["result_json"] is None
    assert observed["reservations"][0]["status"] == (
        "cancelled" if fault == "owner_cancel" else "failed")


def _cache_reader(gateway: Any) -> ResearchCapabilityGateway:
    if os.environ.get("GATEWAY_RAW_CACHE_BASELINE") != "1":
        return gateway
    root = Path(__file__).resolve().parents[2]
    old = root / ("data/product-local/remediation/W7/hm04-gateway-postadapter-raw-seam/"
                  "source-before/packages/workbench_adapters/durable_research_gateway.py")
    assert hashlib.sha256(old.read_bytes()).hexdigest() == (
        "574e94fdad1efa5cc9885deacd48c3186859df547be25f954c4cde855ca13000")
    spec = importlib.util.spec_from_file_location("gateway_before_raw_cache", old)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.DurableResearchCapabilityGateway(gateway.inner, gateway.links, gateway.evidence,
        gateway.observability, requirements=gateway.requirements, clock=gateway.clock)


@pytest.mark.parametrize("drift", ["deleted", "resigned_payload", "missing_anchor"])
async def test_actual_raw_sidecar_drift_or_deletion_never_returns_cached_success(
    inputs: PreparedInputs, tmp_path: Path, drift: str,
) -> None:
    from packages.kernel.decision_hub_kernel.persistence.db import ResearchTraceRecord

    inputs.prepare()
    adapter = ReturnedAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    first = await gateway.execute(_query(inputs))
    original_sql = _raw_sql(inputs)
    assert len(original_sql["raw_sidecars"]) == 1 and first.cost_usd == 0.01
    other = Database(str(inputs.database.engine.url))
    try:
        with other.session() as session:
            row = session.query(RunRoleOutputRecord).filter(
                RunRoleOutputRecord.role.startswith("research_adapter_return:")).one()
            if drift == "deleted":
                session.delete(row)
            elif drift == "resigned_payload":
                body = json.loads(row.payload_json)
                raw = json.loads(body["adapter_result_json"])
                raw["provider"] = "different-but-valid-provider"
                body["adapter_result_json"] = json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                                        separators=(",", ":"))
                body["adapter_result_hash"] = hashlib.sha256(
                    body["adapter_result_json"].encode("utf-8")).hexdigest()
                row.payload_json = json.dumps(body, sort_keys=True, ensure_ascii=False,
                                              separators=(",", ":"))
                row.payload_hash = hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
                row.output_id = "adapter_return_" + row.payload_hash
            else:
                trace = session.query(ResearchTraceRecord).filter_by(
                    event_type="tool_completed").one()
                trace.summary = "Changed readable summary without the original fixed-version marker"
        error = None
        try:
            await _cache_reader(gateway).execute(_query(inputs))
        except ResearchCapabilityError as exc:
            error = exc.provenance().model_dump(mode="json")
        observed = _raw_sql(inputs)
        _observe(tmp_path, {"drift": drift, "baseline_cache_reader": os.environ.get(
            "GATEWAY_RAW_CACHE_BASELINE") == "1", "original_sql": original_sql,
            "error": error, "adapter_calls": len(adapter.calls), "sql": observed})
        assert error is not None, "cached normalized success ignored its original raw audit drift"
        assert error["error_code"] == "research_adapter_return_integrity_invalid"
        assert len(adapter.calls) == 1 and observed["reservations"] == original_sql["reservations"]
    finally:
        other.engine.dispose()


@pytest.mark.parametrize("over", [False, True])
async def test_raw_snapshot_utf8_cap_exact_and_one_byte_over_is_not_free_retry(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, over: bool,
) -> None:
    from packages.kernel.decision_hub_kernel.application import research_evidence

    formal_cap = research_evidence.ADAPTER_RETURN_MAX_UTF8_BYTES
    assert formal_cap == 64 * 1024 * 1024
    inputs.prepare()
    measured: dict[str, int] = {}

    def edit(result: ResearchCapabilityResult) -> ResearchCapabilityResult:
        raw = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        measured["fixture_budget"] = len(raw) + 7
        monkeypatch.setattr(research_evidence, "ADAPTER_RETURN_MAX_UTF8_BYTES", len(raw) + 7)
        returned = result.model_copy(update={"provider": result.provider + "x" * (7 + int(over))})
        measured["actual_return_bytes"] = len(json.dumps(returned.model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return returned

    adapter = ReturnedAdapter(inputs, edit=edit)
    gateway = _gateway(inputs, adapter)
    error: dict[str, Any] | None = None
    try:
        await gateway.execute(_query(inputs))
    except ResearchCapabilityError as exc:
        error = {"provenance": exc.provenance().model_dump(mode="json"),
                 "notes": getattr(exc, "__notes__", [])}
    first_sql = _raw_sql(inputs)
    if over:
        with pytest.raises(ResearchCapabilityError) as replay:
            await gateway.execute(_query(inputs))
        assert replay.value.error_code == "research_tool_call_in_progress"
    observed = _raw_sql(inputs)
    _observe(tmp_path, {"formal_cap_unchanged": formal_cap, "fixture_only_over": over,
        "measured": measured, "error": error, "adapter_calls": len(adapter.calls),
        "reported_actual_cost": adapter.returned.cost_usd if adapter.returned else None,
        "sql": first_sql, "repeat_sql": observed})
    assert measured["actual_return_bytes"] == measured["fixture_budget"] + int(over)
    assert len(adapter.calls) == 1
    if over:
        assert error is not None and error["provenance"]["retryable"] is False
        assert observed["reservations"][0]["status"] == "running"
        assert observed["reservations"][0]["result_json"] is None and observed["raw_sidecars"] == []
        assert observed["evidence"] == [] and observed == first_sql
    else:
        assert error is None and observed["reservations"][0]["status"] == "completed"


async def test_legal_live_original_and_normalized_are_distinct_and_cache_is_exact(
    live_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    live_inputs.prepare()
    adapter = ReturnedAdapter(live_inputs, live=True)
    gateway = _gateway(live_inputs, adapter)
    gateway.inner = _actual_inner(live_inputs, adapter, lambda: live_inputs.now)
    result = await gateway.invoke_live(_intent(live_inputs))
    first = _raw_sql(live_inputs)
    repeated = await gateway.invoke_live(_intent(live_inputs))
    after = _raw_sql(live_inputs)
    assert adapter.returned is not None
    original = adapter.returned.model_dump(mode="json")
    normalized = result.model_dump(mode="json")
    body = json.loads(first["raw_sidecars"][0]["payload_json"])
    _observe(tmp_path, {"actual_original": original, "normalized": normalized,
        "repeated": repeated.model_dump(mode="json"), "adapter_calls": len(adapter.calls),
        "sql": first, "repeat_sql": after})
    assert original != normalized and result == repeated and first == after
    assert len(adapter.calls) == 1 and result.cost_usd == adapter.returned.cost_usd == 0.01
    assert json.loads(body["adapter_result_json"]) == original
    assert json.loads(first["reservations"][0]["result_json"]) == normalized
    assert len(first["raw_sidecars"][0]["role"]) == 64


@pytest.mark.parametrize("over", [False, True])
async def test_total_raw_envelope_utf8_exact_and_one_byte_over_stays_unknown(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, over: bool,
) -> None:
    from packages.workbench_adapters import durable_research_gateway as module

    formal_cap = module._ADAPTER_AUDIT_MAX_UTF8_BYTES  # pyright: ignore[reportPrivateUsage]
    assert formal_cap == 64 * 1024 * 1024
    inputs.prepare()
    adapter = ReturnedAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    await gateway.execute(_query(inputs))
    original = _raw_sql(inputs)
    measured = len(original["raw_sidecars"][0]["payload_json"].encode("utf-8"))
    monkeypatch.setattr(module, "_ADAPTER_AUDIT_MAX_UTF8_BYTES", measured - int(over))
    second_query = _query(inputs, request_id="controlled-call-2")
    error = None
    try:
        await gateway.execute(second_query)
    except ResearchCapabilityError as exc:
        error = {"provenance": exc.provenance().model_dump(mode="json"),
                 "notes": getattr(exc, "__notes__", []),
                 "snapshot_hash": (exc.adapter_return_observation.result_hash
                                   if exc.adapter_return_observation else None)}
    observed = _raw_sql(inputs)
    if over:
        with pytest.raises(ResearchCapabilityError) as repeated:
            await gateway.execute(second_query)
        assert repeated.value.error_code == "research_tool_call_in_progress"
    after = _raw_sql(inputs)
    _observe(tmp_path, {"formal_total_cap": formal_cap, "fixture_only_over": over,
        "measured_original_envelope_bytes": measured, "fixture_budget": measured - int(over),
        "error": error, "adapter_calls": len(adapter.calls), "original_sql": original,
        "sql": observed, "repeat_sql": after,
        "reported_actual_cost": adapter.returned.cost_usd if adapter.returned else None})
    assert len(adapter.calls) == 2 and observed == after
    second = next(row for row in observed["reservations"]
                  if row["request_id"] == second_query.request_id)
    if over:
        assert error is not None and error["snapshot_hash"] is not None
        assert second["status"] == "running" and second["result_json"] is None
        assert observed["raw_sidecars"] == original["raw_sidecars"]
        assert observed["evidence"] == original["evidence"]
        assert (observed["facts"] == original["facts"]
                and observed["reuse_count"] == original["reuse_count"])
    else:
        assert error is None and second["status"] == "completed"
        second_raw = next(row for row in observed["raw_sidecars"]
                          if json.loads(row["payload_json"])["request_id"]
                          == second_query.request_id)
        assert len(second_raw["payload_json"].encode("utf-8")) == measured


async def test_real_sidecar_flush_then_storage_failure_rolls_back_to_unknown(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    flushed: list[dict[str, Any]] = []

    class FailingCompletion(ResearchObservabilityService):
        def complete_tool_call_in_session(
            self, session: Session, *, run_id: str, request_id: str,
            result: ResearchCapabilityResult, semantic_error: ErrorProvenance | None = None,
        ) -> None:
            flushed.append({"raw_sidecars_after_actual_flush": session.query(RunRoleOutputRecord)
                .filter(RunRoleOutputRecord.role.startswith("research_adapter_return:")).count(),
                "raw_reported_cost": result.cost_usd,
                "original_error": (semantic_error.model_dump(mode="json")
                                   if semantic_error else None)})
            raise OSError("controlled_after_real_raw_sidecar_flush")

    def edit(result: ResearchCapabilityResult) -> ResearchCapabilityResult:
        candidate = result.evidence_candidates[0].model_copy(update={"content_hash": "f" * 64})
        return result.model_copy(update={"evidence_candidates": [candidate]})

    adapter = ReturnedAdapter(inputs, edit=edit)
    gateway = _gateway(inputs, adapter,
                       FailingCompletion(inputs.database, clock=lambda: inputs.now))
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.execute(_query(inputs))
    first = _raw_sql(inputs)
    with pytest.raises(ResearchCapabilityError) as repeated:
        await gateway.execute(_query(inputs))
    after = _raw_sql(inputs)
    _observe(tmp_path, {"actual_flush": flushed, "adapter_calls": len(adapter.calls),
        "error": error.value.provenance().model_dump(mode="json"),
        "notes": getattr(error.value, "__notes__", []), "sql": first, "repeat_sql": after,
        "in_memory_raw_hash": (error.value.adapter_return_observation.result_hash
                               if error.value.adapter_return_observation else None)})
    assert flushed[0]["raw_sidecars_after_actual_flush"] == 1
    assert error.value.error_code == "research_content_hash_mismatch"
    assert error.value.adapter_return_observation is not None
    assert error.value.adapter_return_observation.result().cost_usd == 0.01
    assert any("persistence_unknown:OSError" in note
               for note in getattr(error.value, "__notes__", []))
    assert first == after and len(adapter.calls) == 1
    assert first["reservations"][0]["status"] == "running" and first["raw_sidecars"] == []
    assert first["evidence"] == first["facts"] == [] and first["reuse_count"] == 0
    assert repeated.value.error_code == "research_tool_call_in_progress"


async def test_zero_serialization_failure_cannot_turn_returned_cost_into_free_retry(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    adapter = ReturnedAdapter(inputs)
    original_dump = ResearchCapabilityResult.model_dump

    def controlled_dump(
        self: ResearchCapabilityResult, *args: Any, **kwargs: Any,
    ) -> dict[str, Any]:
        if self is adapter.returned:
            raise TypeError("controlled_zero_typed_snapshot_serialization")
        return original_dump(self, *args, **kwargs)

    monkeypatch.setattr(ResearchCapabilityResult, "model_dump", controlled_dump)
    gateway = _gateway(inputs, adapter)
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.execute(_query(inputs))
    first = _raw_sql(inputs)
    with pytest.raises(ResearchCapabilityError) as repeated:
        await gateway.execute(_query(inputs))
    after = _raw_sql(inputs)
    _observe(tmp_path, {"adapter_calls": len(adapter.calls),
        "actual_in_memory_reported_cost": adapter.returned.cost_usd if adapter.returned else None,
        "error": error.value.provenance().model_dump(mode="json"),
        "cause_type": type(error.value.__cause__).__name__,
        "notes": getattr(error.value, "__notes__", []), "sql": first, "repeat_sql": after})
    assert (len(adapter.calls) == 1 and adapter.returned is not None
            and adapter.returned.cost_usd == 0.01)
    assert error.value.error_code == "research_adapter_return_snapshot_unavailable"
    assert isinstance(error.value.__cause__, TypeError) and error.value.retryable is False
    assert error.value.adapter_return_observation is None and first == after
    assert (first["reservations"][0]["status"] == "running"
            and first["reservations"][0]["result_json"] is None)
    assert first["raw_sidecars"] == first["evidence"] == first["facts"] == []
    assert repeated.value.error_code == "research_tool_call_in_progress"


async def test_archive_resolver_cannot_mutate_already_snapshotted_adapter_object(
    live_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    live_inputs.prepare()
    original: list[dict[str, Any]] = []

    def record(result: ResearchCapabilityResult) -> ResearchCapabilityResult:
        original.append(result.model_dump(mode="json"))
        return result

    adapter = ReturnedAdapter(live_inputs, live=True, edit=record)

    def mutate() -> dict[str, datetime]:
        assert adapter.returned is not None
        adapter.returned.provider = "mutated-after-real-capture"
        adapter.returned.evidence_candidates[0].excerpt = "unsourced replacement after capture"
        return {}

    gateway = _gateway(live_inputs, adapter)
    inner = _actual_inner(live_inputs, adapter, lambda: live_inputs.now)
    inner._archived_observation_resolver = mutate  # pyright: ignore[reportPrivateUsage]
    gateway.inner = inner
    result = await gateway.invoke_live(_intent(live_inputs))
    observed = _raw_sql(live_inputs)
    raw = json.loads(observed["raw_sidecars"][0]["payload_json"])
    _observe(tmp_path, {"original_before_mutation": original[0],
        "adapter_owned_after_mutation": (adapter.returned.model_dump(mode="json")
                                         if adapter.returned else None),
        "normalized": result.model_dump(mode="json"), "sql": observed})
    assert json.loads(raw["adapter_result_json"]) == original[0]
    assert result.provider == original[0]["provider"]
    assert result.evidence_candidates[0].excerpt == original[0]["evidence_candidates"][0]["excerpt"]


async def test_owner_cancel_after_actual_return_retains_raw_without_semantic_admission(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    adapter = ReturnedAdapter(inputs)
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError("controlled_owner_cancel_after_typed_return")
        return inputs.now

    gateway = _gateway(inputs, adapter)
    gateway.inner = _actual_inner(inputs, adapter, clock)
    with pytest.raises(asyncio.CancelledError):
        await gateway.execute(_query(inputs))
    observed = _raw_sql(inputs)
    _observe(tmp_path, {"clock_calls": calls, "adapter_calls": len(adapter.calls),
        "actual_return": adapter.returned.model_dump(mode="json") if adapter.returned else None,
        "sql": observed})
    assert calls == 2
    _assert_raw_denial(observed, adapter)


async def test_two_actual_runs_interleaved_returns_have_context_local_raw_ownership(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    other_path = tmp_path / "other-run"
    other_path.mkdir()
    generator = _live_inputs(other_path)
    other = next(generator)
    tasks: list[asyncio.Task[ResearchCapabilityResult]] = []
    try:
        inputs.prepare()
        other.prepare()
        assert inputs.request.run_id != other.request.run_id
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]

        class HeldReturn(ReturnedAdapter):
            def __init__(self, source: PreparedInputs, index: int, *, live: bool = False):
                super().__init__(source, live=live)
                self.index = index

            async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
                entered[self.index].set()
                await release[self.index].wait()
                return await super().execute(query)

        first_adapter, second_adapter = HeldReturn(inputs, 0), HeldReturn(other, 1, live=True)
        first_gateway, second_gateway = (
            _gateway(inputs, first_adapter), _gateway(other, second_adapter))
        tasks = [asyncio.create_task(first_gateway.execute(_query(inputs))),
                 asyncio.create_task(second_gateway.invoke_live(_intent(other)))]
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=10)
        release[1].set()
        await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=10)
        release[0].set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        observations = [_raw_sql(inputs), _raw_sql(other)]
        bodies = [json.loads(observed["raw_sidecars"][0]["payload_json"])
                  for observed in observations]
        _observe(tmp_path, {"raw_bodies": bodies, "sql": observations,
            "adapter_calls": [len(first_adapter.calls), len(second_adapter.calls)]})
        for source, adapter, body in zip((inputs, other), (first_adapter, second_adapter),
                                        bodies, strict=True):
            assert adapter.returned is not None and len(adapter.calls) == 1
            assert body["run_id"] == source.request.run_id
            assert (json.loads(body["adapter_result_json"])
                    == adapter.returned.model_dump(mode="json"))
        # A new ambient scope starts empty after both coroutine finally resets.
        from packages.kernel.decision_hub_kernel.application.research_evidence import (
            observe_capability_adapter_return,
        )
        with observe_capability_adapter_return() as collector:
            assert collector.returned is False and collector.observation is None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        generator.close()
