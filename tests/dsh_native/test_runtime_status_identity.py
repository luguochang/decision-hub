"""Offline physical Host reply identity before any durable Link mutation."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshSessionAccepted,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, DshSessionLinkRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    FakeHostClient,
    _database,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


def _links(database: Database) -> list[dict[str, Any]]:
    with database.session() as session:
        return [
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            for row in session.query(DshSessionLinkRecord).order_by(DshSessionLinkRecord.run_id)
        ]


async def test_foreign_cancel_reply_never_confirms_original_link(tmp_path: Path) -> None:
    """The real accepted original Run must not inherit another Run's terminal ack."""
    database = _database(tmp_path / "identity.sqlite3")
    original_run, _ = RunService(database).create("event-host-identity-original")
    foreign_run, _ = RunService(database).create("event-host-identity-foreign")
    original_request = _request(original_run, deadline_seconds=1.5)
    calls: list[str] = []
    observation: dict[str, Any] = {"original_run": original_run, "foreign_run": foreign_run}

    class ForeignCancelHost(FakeHostClient):
        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            calls.append("status")
            # Real submit/accepted SQL has already happened. Cross the original
            # caller deadline without changing any product clock or deadline.
            delay = max(0.0, (payload.deadline_at - datetime.now(UTC)).total_seconds())
            await asyncio.sleep(delay + 0.002)
            raise AgentExecutionError(
                "provider_timeout", "Controlled status reached the original deadline",
                provider_id="dsh-web", origin="dsh", cause_code="controlled_status_timeout",
            )

        async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
            calls.append("cancel")
            self.cancelled.append(payload.run_id)
            observation["before_cancel"] = _links(database)
            observation["cancel_submit"] = payload.model_dump(mode="json")
            observation["cancel_reason"] = reason
            status = self.status_value(payload, "cancelled", 7).model_copy(
                update={"run_id": foreign_run, "dsh_session_id": "dsh_foreign-session"}
            )
            # Use a real canonical construction, rather than returning an
            # invalid model_copy that the transport decoder would reject.
            reply = DshSessionStatus.model_validate(status.model_dump(mode="json"))
            observation["cancel_reply"] = reply.model_dump(mode="json")
            return reply

        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            del payload
            calls.append("result")
            raise AgentExecutionError(
                "dsh_host_conflict", "Controlled Host has no partial transcript",
                provider_id="dsh-web", origin="dsh", cause_code="http_409",
            )

    host = ForeignCancelHost()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
        poll_interval_seconds=0.001, terminal_reconciliation_seconds=1)
    failure: AgentExecutionError | None = None
    try:
        try:
            await runtime.execute(original_request)
        except AgentExecutionError as exc:
            failure = exc
        observation["after_execute"] = _links(database)
        observation["calls"] = calls
        observation["error"] = None if failure is None else {
            "error_code": failure.error_code, "cause_code": failure.cause_code,
        }
        before = observation["before_cancel"]
        after = observation["after_execute"]
        assert before[0]["accepted_at"] is not None
        assert host.cancelled == [original_run]
        assert after == before, "foreign terminal reply changed original accepted SQL Link"
        assert calls == ["status", "cancel"], "foreign reply permitted another physical Host call"
        assert failure is not None and failure.error_code == "dsh_protocol_invalid"
    finally:
        await runtime.close()
        database.engine.dispose()
        observation["cleanup"] = {
            "context_anchor": runtime._task_anchor.get() is None,  # pyright: ignore[reportPrivateUsage]
            "database_connections": database.engine.pool.checkedout(),  # type: ignore[attr-defined]
        }
        (tmp_path / "observation.json").write_text(
            json.dumps(observation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )


@pytest.mark.parametrize("field", ["run_id", "dsh_session_id", "generation"])
@pytest.mark.parametrize("phase", [
    "accepted", "status_running", "status_completed", "status_failed", "status_cancelled",
    "cancel_terminal", "cancel_running", "cancel_poll",
])
async def test_physical_reply_identity_precedes_all_link_routing(
    tmp_path: Path, phase: str, field: str,
) -> None:
    await _identity_case(tmp_path, phase=phase, field=field)


async def test_foreign_accepted_for_real_other_link_cannot_pollute_it(tmp_path: Path) -> None:
    await _identity_case(tmp_path, phase="accepted", field="foreign_binding")


@pytest.mark.parametrize("phase", ["completed", "valid_cancel"])
async def test_original_identity_control_uses_real_link(tmp_path: Path, phase: str) -> None:
    await _identity_case(tmp_path, phase=phase, field=None)


async def _identity_case(tmp_path: Path, *, phase: str, field: str | None) -> None:
    database = _database(tmp_path / "identity-matrix.sqlite3")
    service = DshSessionLinkService(database)
    original_run, _ = RunService(database).create("event-identity-matrix-original")
    other_run, _ = RunService(database).create("event-identity-matrix-other")
    request = _request(original_run, deadline_seconds=1.5 if phase.startswith("cancel")
                       or phase == "valid_cancel" else 5)
    other_request = _request(other_run)
    other_submit = DshWebResearchRuntime._submission(other_request)  # pyright: ignore[reportPrivateUsage]
    host = FakeHostClient()
    readiness = await host.readiness()
    service.reserve(other_submit, readiness.upstream_identity)
    calls: list[str] = []
    observation: dict[str, Any] = {"phase": phase, "field": field, "original_run": original_run,
        "other_run": other_run, "initial": _links(database)}

    def changed(reply: DshSessionAccepted | DshSessionStatus):
        observation["before_reply"] = _links(database)
        body = reply.model_dump(mode="json")
        if field == "run_id":
            body["run_id"] = other_run
        elif field == "dsh_session_id":
            body["dsh_session_id"] = other_submit.deterministic_session_id
        elif field == "generation":
            body["generation"] = reply.generation + 1
        elif field == "foreign_binding":
            body.update(run_id=other_run, dsh_session_id=other_submit.deterministic_session_id,
                        generation=other_submit.generation)
        typed = type(reply).model_validate(body)
        observation["physical_reply"] = typed.model_dump(mode="json")
        observation["calls_at_reply"] = list(calls)
        return typed

    class MatrixHost(FakeHostClient):
        async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
            calls.append("submit")
            accepted = await super().submit(payload)
            if phase == "accepted":
                result = changed(accepted)
                assert isinstance(result, DshSessionAccepted)
                return result
            return accepted

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            calls.append("status")
            if phase.startswith("status_"):
                result = changed(self.status_value(payload, phase.removeprefix("status_"), 4))
                assert isinstance(result, DshSessionStatus)
                return result
            if phase == "cancel_poll" and "cancel" in calls:
                result = changed(self.status_value(payload, "cancelled", 6))
                assert isinstance(result, DshSessionStatus)
                return result
            if phase.startswith("cancel") or phase == "valid_cancel":
                await asyncio.sleep(
                    max(0.0, (payload.deadline_at - datetime.now(UTC)).total_seconds()) + 0.002)
                raise AgentExecutionError("provider_timeout", "Original caller deadline elapsed",
                    provider_id="dsh-web", origin="dsh", cause_code="controlled_status_timeout")
            return self.status_value(payload, "completed", 3)

        async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
            calls.append("cancel")
            self.cancelled.append(payload.run_id)
            observation["cancel_submit"] = payload.model_dump(mode="json")
            observation["cancel_reason"] = reason
            state = "running" if phase in {"cancel_running", "cancel_poll"} else "cancelled"
            status = self.status_value(payload, state, 5)
            if phase in {"cancel_terminal", "cancel_running"}:
                result = changed(status)
                assert isinstance(result, DshSessionStatus)
                return result
            return status

        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            calls.append("result")
            if phase.startswith("cancel") or phase == "valid_cancel" or phase.endswith("failed"):
                raise AgentExecutionError("dsh_host_conflict", "No partial transcript",
                    provider_id="dsh-web", origin="dsh", cause_code="http_409")
            return await super().result(payload)

    client = MatrixHost()
    runtime = DshWebResearchRuntime(service, client, poll_interval_seconds=0.001,
                                   terminal_reconciliation_seconds=1)
    failure: BaseException | None = None
    try:
        try:
            await runtime.execute(request)
        except (AgentExecutionError, ValueError, KeyError) as exc:
            failure = exc
        after = _links(database)
        observation.update(after_execute=after, calls=calls,
            error=None if failure is None else {
                "type": type(failure).__name__, "error_code": getattr(failure, "error_code", None),
                "cause_code": getattr(failure, "cause_code", None), "message": str(failure),
            })
        other_before = next(row for row in observation["initial"] if row["run_id"] == other_run)
        other_after = next(row for row in after if row["run_id"] == other_run)
        assert other_after == other_before, "physical reply polluted another real Run's Link"
        if field is not None:
            assert after == observation["before_reply"], "unbound reply mutated durable Link"
            assert calls == observation["calls_at_reply"], "unbound reply enabled a subsequent RPC"
            assert isinstance(failure, AgentExecutionError)
            assert failure.error_code == ("dsh_generation_conflict" if field == "generation"
                                          else "dsh_protocol_invalid")
        else:
            original = next(row for row in after if row["run_id"] == original_run)
            assert original["accepted_at"] is not None
            assert original["state"] == ("cancelled" if phase == "valid_cancel" else "completed")
            assert calls == (["submit", "status", "cancel", "result"] if phase == "valid_cancel"
                             else ["submit", "status", "result"])
    finally:
        await runtime.close()
        database.engine.dispose()
        observation["cleanup"] = {
            "context_anchor": runtime._task_anchor.get() is None,  # pyright: ignore[reportPrivateUsage]
            "database_connections": database.engine.pool.checkedout(),  # type: ignore[attr-defined]
        }
        (tmp_path / "observation.json").write_text(
            json.dumps(observation, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
