from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    ResearchRunDetailView,
    ResearchTraceEvent,
)
from packages.contracts_py.decision_hub_contracts.models import ObservationCreate, SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
    _payload_hash,  # pyright: ignore[reportPrivateUsage] -- actual original Trace hash recipe
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from tests.research.test_provider_attempts_legacy_readback import (
    _physical_rows,  # pyright: ignore[reportPrivateUsage] -- full physical rows, not projections
)


def test_historical_null_detail_and_terminal_sse_keep_original_rows_and_parse_dto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_connect = socket.socket.connect
    socketpair = getattr(socket, "_fallback_socketpair", None)
    socketpair_code = getattr(socketpair, "__code__", None)
    internal_ipc: list[object] = []
    network_attempts: list[object] = []

    def deny_network(sock: socket.socket, address: tuple[object, ...] | str | bytes) -> None:
        # Windows asyncio's private self-pipe uses this standard-library IPC,
        # even though TestClient never opens an HTTP origin. No other caller
        # is allowed to connect, including any Provider or app client.
        caller = sys._getframe(1)  # pyright: ignore[reportPrivateUsage] -- exact stdlib IPC caller
        if socketpair_code is not None and caller.f_code is socketpair_code:
            internal_ipc.append(address)
            return original_connect(sock, address)
        network_attempts.append(address)
        raise AssertionError("in-process API compatibility must not access a real socket")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "import-data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    # Import only after this fixture's data directory is isolated. The module's
    # global app must never initialize the formal database during collection.
    from fastapi.testclient import TestClient

    from apps.hub_api.main import create_app

    now = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)
    path = tmp_path / "api.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    try:
        database.create_all()
        event_id, _, admitted = AdmissionService(database, clock=lambda: now).admit(
            ObservationCreate(
                text="Controlled original official transcript, no Provider execution.",
                source_id="compat-api-control", source_type=SourceType.transcript,
                observed_at=now - timedelta(seconds=1),
                published_at=now - timedelta(seconds=2), language="en",
                event_hint="central_bank_speech",
            )
        )
        assert admitted
        run_id, created = RunService(database, clock=lambda: now).create(
            event_id, strategy_version="research.v1",
        )
        assert created
        original = {
            "error_code": "original-provider-timeout", "origin": "provider",
            "cause_code": "original-cause", "capability_id": "controlled.capability",
            "tool_call_id": "original-call", "retryable": True,
            "deadline_ms": 1200, "provider_attempts": [],
        }
        summary = "原" * 2000
        saved = ResearchObservabilityService(database, clock=lambda: now).append_trace(
            ResearchTraceEvent(
                schema_version="research-trace-event.v1", run_id=run_id,
                research_session_id=f"controlled:{run_id}", sequence_no=0,
                event_type="tool_failed", occurred_at=now, stage="researching",
                summary=summary, reference_type="capability_call", reference_id="original-call",
                status="failed", error_code="original-provider-timeout",
                error=ErrorProvenance.model_validate(original),
            )
        )
        raw = original | {"provider_attempts": None}
        raw_json = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        semantic = saved.model_dump(mode="json", exclude={"sequence_no", "error_verification"})
        semantic["error"] = raw
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE research_trace_events SET error_provenance_json=?,event_hash=? "
                "WHERE run_id=?", (raw_json, _payload_hash(semantic), run_id),
            )
            # Fixture terminal status closes the real finite SSE route. It does
            # not derive retry eligibility or terminal state from unknown provenance.
            connection.execute(
                "UPDATE runs SET status='failed',error_code=?,finished_at=? WHERE run_id=?",
                ("original-provider-timeout", now.isoformat(), run_id),
            )
        app = create_app(database, source_connectors=[])
        before = _physical_rows(path)
        client = TestClient(app)
        try:
            detail_response = client.get(f"/v1/research/runs/{run_id}")
            assert detail_response.status_code == 200
            detail = ResearchRunDetailView.model_validate(detail_response.json())
            assert detail.run.status == "failed"
            assert detail.run.failure is None
            assert "legacy_provider_attempts_unverifiable" in detail.action_summary.warning_codes
            assert detail.trace[0].error is None
            assert detail.trace[0].error_verification == "legacy_provider_attempts_unverifiable"
            assert detail.trace[0].summary == summary
            assert detail.trace[0].error_code == "original-provider-timeout"
            assert detail.trace[0].reference_id == "original-call"
            after_detail = _physical_rows(path)
            assert after_detail == before

            events_response = client.get(f"/v1/research/runs/{run_id}/events")
            assert events_response.status_code == 200
            payloads = [line.removeprefix("data: ") for line in events_response.text.splitlines()
                        if line.startswith("data: ")]
            assert len(payloads) == 1
            event = ResearchTraceEvent.model_validate_json(payloads[0])
            assert event == detail.trace[0]
            after_sse = _physical_rows(path)
            assert after_sse == before
            assert not network_attempts
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                connection.execute("PRAGMA query_only=ON")
                original_after = connection.execute(
                    "SELECT error_provenance_json FROM research_trace_events WHERE run_id=?",
                    (run_id,),
                ).fetchone()
            assert original_after is not None and original_after[0] == raw_json
            destination = os.environ.get("PROVIDER_ATTEMPTS_READBACK_OBSERVATIONS")
            if destination:
                with Path(destination).open("x", encoding="utf-8") as stream:
                    json.dump({
                        "run_id": run_id, "fixture_database": str(path),
                        "raw_original": raw_json, "raw_after": original_after[0],
                        "full_physical_before": before,
                        "full_physical_after_detail": after_detail,
                        "full_physical_after_sse": after_sse,
                        "detail": detail.model_dump(mode="json"),
                        "sse": event.model_dump(mode="json"),
                        "real_network_connect_attempts": len(network_attempts),
                        "stdlib_private_socketpair_ipc_count": len(internal_ipc),
                    }, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
        finally:
            client.close()
    finally:
        database.engine.dispose()
