"""One successor control: default WARNING visibility and real executor ordering."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from test_transport_pending_confirmation import private_database, private_runtime, settled

from packages.contracts_py.decision_hub_contracts import DshSessionStatus
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import web_runtime


@pytest.mark.asyncio
async def test_warning_confirmation_visibility_and_queued_before_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    request: pytest.FixtureRequest,
) -> None:
    database, run_id, projector = private_database(tmp_path / "warning.sqlite3")
    runtime = private_runtime(database, run_id, projector)
    release = threading.Event()
    original_submit = projector._executor.submit
    stages: list[str] = []
    stores: list[str] = []
    original_store = web_runtime.store_transport_observation

    def submit(function):
        assert stages == ["queued"]  # Proven before entering the real SDK executor seam.
        return original_submit(function)

    def save(_session: Session) -> str:
        return "private-ready"

    def delayed_store(
        session: Session, value: web_runtime.ResearchTransportObservation,
    ) -> ObservedExecutionRef:
        stores.append("store")
        reference = original_store(session, value)
        assert release.wait(2)
        return reference

    def closed_submit(_function):
        raise RuntimeError("private executor refused submission")

    caplog.set_level(logging.WARNING, logger=web_runtime.__name__)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(projector._executor, "submit", submit)
            ready = await projector.write(
                save, timeout_seconds=1,
                stage_observer=lambda _id, stage, _code: stages.append(stage),
            )
        assert ready.state == "ready"
        assert stages == ["queued", "begin_acquired", "operation_completed", "commit_done"]

        monkeypatch.setattr(web_runtime, "store_transport_observation", delayed_store)
        reply = DshSessionStatus(
            schema_version="dsh-session-status.v1", run_id=run_id,
            dsh_session_id="private-native", state="failed", generation=1, last_seq=12,
            observed_at=datetime.now(UTC), error_code="private_unverified_native_failure",
        )
        with pytest.raises(AgentExecutionError):
            await runtime._record_transport(reply)
        records = [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records]
        assert len(records) == 1 and records[0]["storage_state"] == "pending"
        assert records[0]["latest_stage"] == "begin_acquired"
        assert records[0]["native_error_authentication"] == "unverified"
        assert records[0]["expected_output_id"].startswith("research_transport_")
        assert records[0]["output_id"] is None and records[0]["reference"] is None
        observations = runtime._observations.get()
        assert observations is not None and observations.latest is None
        handle = next(iter(observations.pending_transports.values()))
        observed = threading.Event()
        handle.observe(lambda _outcome: observed.set())
        release.set()
        await settled(handle)
        assert await asyncio.to_thread(observed.wait, 1)
        records = [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records]
        late = next(item for item in records if item["storage_state"] == "confirmed")
        assert late["latest_stage"] == "commit_done"
        assert late["operation_id"] == records[0]["operation_id"]
        assert late["reference"]["output_id"] == late["expected_output_id"]
        assert late["reference"]["payload_hash"] == late["payload_hash"]
        reference = ObservedExecutionRef.model_validate_json(json.dumps(late["reference"]))
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            saved = read_transport_observation(session, reference)
            row = session.get(RunRoleOutputRecord, reference.output_id)
            assert row is not None and saved.reply == reply
            digest = observation_sha(saved.model_dump(
                mode="json", exclude={"recorded_at"}, exclude_unset=True,
            ))
            assert late["expected_output_id"] == "research_transport_" + digest
            assert late["expected_role"] == row.role == "research_transport:" + digest
            assert hashlib.sha256(row.payload_json.encode()).hexdigest() == late["payload_hash"]
        assert stores == ["store"] and observations.latest is None

        # Submission failure is visible independently, with no real future/ref.
        with monkeypatch.context() as patch:
            patch.setattr(projector._executor, "submit", closed_submit)
            with pytest.raises(AgentExecutionError):
                await runtime._record_transport(reply.model_copy(update={"last_seq": 13}))
        records = [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records]
        failures = [item for item in records if item.get("stage") == "failed"]
        assert len(failures) == 1
        assert failures[0]["storage_state"] == "failed"
        assert failures[0]["storage_code"] == "cancellation_projection_closed"
        assert stores == ["store"] and observations.latest is None
        assert all(record.levelno == logging.WARNING for record in caplog.records)
        request.node.user_properties.append((
            "warning_records", json.dumps(records, sort_keys=True),
        ))
    finally:
        release.set()
        await projector.close()
        database.engine.dispose()
