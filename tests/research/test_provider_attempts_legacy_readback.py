from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError

from packages.contracts_py.decision_hub_contracts import ErrorProvenance, ResearchTraceEvent
from packages.contracts_py.decision_hub_contracts.models import ObservationCreate, SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
    _payload_hash,  # pyright: ignore[reportPrivateUsage] -- use actual durable hash recipe
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, ResearchTraceRecord
from packages.query_views.research import ResearchQueryService
from packages.query_views.research.service import (
    _trace_failure,  # pyright: ignore[reportPrivateUsage] -- requested legacy reader diagnostic
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
PACK_ROOT = ROOT / "packs/crypto_macro"
type CaseKind = Literal[
    "omitted", "empty", "component_null", "attempts_null", "null_long_summary",
    "null_bad_origin", "null_bad_retryable", "null_bad_hash", "null_code_mismatch",
    "bad_origin", "bad_json",
]


def _original_error() -> dict[str, object]:
    return {
        "error_code": "provider_original_timeout",
        "origin": "provider",
        "cause_code": "original_transport_cause",
        "capability_id": "controlled.capability",
        "tool_call_id": "original-call",
        "retryable": True,
        "deadline_ms": 1900,
        "provider_attempts": [],
    }


def _historical_error(case: CaseKind) -> dict[str, object]:
    raw = _original_error()
    if case == "omitted":
        del raw["provider_attempts"]
    elif case == "component_null":
        raw["provider_attempts"] = [
            {
                "provider_id": "controlled-provider",
                "route_role": "primary",
                "service_tier": "replay",
                "status": "failed",
                "started_at": NOW.isoformat().replace("+00:00", "Z"),
                "finished_at": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                "latency_ms": 1000,
                "cost_usd": None,
                "error_code": None,
                "retryable": None,
            }
        ]
    elif case == "attempts_null" or case.startswith("null_"):
        raw["provider_attempts"] = None
        if case == "null_bad_origin":
            raw["origin"] = "not-a-canonical-origin"
        elif case == "null_bad_retryable":
            raw["retryable"] = {"not": "a boolean"}
    elif case == "bad_origin":
        raw["origin"] = "not-a-canonical-origin"
    return raw


def _physical_rows(path: Path) -> dict[str, object]:
    # Read every physical column, including original raw/error/hash/sequence,
    # not an ORM projection of selected fields. All fixture tables are tiny.
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        result: dict[str, object] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            cursor = connection.execute(f"SELECT * FROM {quoted}")
            columns = [column[0] for column in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
            result[table] = {"columns": columns, "rows": rows}
        return result


def _raw_error(path: Path, run_id: str) -> str:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT error_provenance_json FROM research_trace_events WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row is not None and isinstance(row[0], str)
    return row[0]


def _read(call: Callable[[], object]) -> dict[str, object]:
    try:
        value = call()
    except ValidationError as exc:
        return {
            "exception": "ValidationError",
            "validation_errors": json.loads(exc.json(include_url=False)),
        }
    except (ValueError, TypeError) as exc:
        return {"exception": type(exc).__name__, "message": str(exc)}
    if isinstance(value, BaseModel):
        serialized: object = value.model_dump(mode="json")
    elif isinstance(value, list):
        serialized = [item.model_dump(mode="json") for item in value]
    else:
        serialized = value
    return {"exception": None, "value": serialized}


@pytest.fixture(scope="session")
def observations() -> Iterator[list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    yield records
    destination = os.environ.get("PROVIDER_ATTEMPTS_READBACK_OBSERVATIONS")
    if destination:
        with Path(destination).open("x", encoding="utf-8") as output:
            json.dump(records, output, ensure_ascii=False, indent=2)
            output.write("\n")


@pytest.mark.parametrize(
    "case", [
        "omitted", "empty", "component_null", "attempts_null", "null_long_summary",
        "null_bad_origin", "null_bad_retryable", "null_bad_hash", "null_code_mismatch",
        "bad_origin", "bad_json",
    ]
)
def test_diagnose_legacy_provider_attempts_without_mutating_original_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: CaseKind,
    observations: list[dict[str, object]],
) -> None:
    connects: list[object] = []

    def denied(*args: object, **_kwargs: object) -> None:
        connects.append(args)
        raise AssertionError("legacy Query diagnosis must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    path = tmp_path / "legacy.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    try:
        database.create_all()  # only a fresh fixture, never migrations/formal DB
        event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(
            ObservationCreate(
                text="Controlled official transcript for historical error readback.",
                source_id="legacy-readback-control",
                source_type=SourceType.transcript,
                observed_at=NOW - timedelta(seconds=2),
                published_at=NOW - timedelta(seconds=3),
                language="en",
                event_hint="central_bank_speech",
            )
        )
        assert admitted
        run_id, created = RunService(database, clock=lambda: NOW).create(
            event_id, strategy_version="research.v1"
        )
        assert created
        saved = ResearchObservabilityService(database, clock=lambda: NOW).append_trace(
            ResearchTraceEvent.model_validate(
                {
                    "schema_version": "research-trace-event.v1",
                    "run_id": run_id,
                    "research_session_id": f"controlled:{run_id}",
                    "sequence_no": 0,
                    "event_type": "tool_failed",
                    "occurred_at": NOW.isoformat(),
                    "stage": "researching",
                    "summary": (
                        "原" * 2000 if case == "null_long_summary"
                        else "Original controlled failure; not a Provider execution."
                    ),
                    "reference_type": "capability_call",
                    "reference_id": "original-call",
                    "status": "failed",
                    "error_code": "provider_original_timeout",
                    "error": ErrorProvenance.model_validate(_original_error()).model_dump(
                        mode="json"
                    ),
                }
            )
        )
        assert saved.sequence_no == 1
        raw = _historical_error(case)
        raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if case == "bad_json":
            raw_json = "{not valid JSON"
        semantic = saved.model_dump(mode="json", exclude={"sequence_no", "error_verification"})
        semantic["error"] = raw
        # Fixture-only historical input insertion. Its event hash follows the
        # actual original recipe; the reader discrepancy is not caused by a
        # mismatched hash. No real historical row is updated or resigned.
        with sqlite3.connect(path) as connection:
            updated = connection.execute(
                "UPDATE research_trace_events SET error_provenance_json=?, event_hash=? "
                "WHERE run_id=? AND sequence_no=1",
                (
                    raw_json,
                    "0" * 64 if case == "null_bad_hash" else _payload_hash(semantic),
                    run_id,
                ),
            )
            assert updated.rowcount == 1
            if case == "null_code_mismatch":
                connection.execute(
                    "UPDATE research_trace_events SET error_code='different-original-code' "
                    "WHERE run_id=? AND sequence_no=1", (run_id,),
                )
        before = _physical_rows(path)
        assert _raw_error(path, run_id) == raw_json
        query = ResearchQueryService(database, pack_root=PACK_ROOT)
        record: dict[str, object] = {
            "case": case,
            "run_id": run_id,
            "event_id": event_id,
            "db_path": str(path),
            "raw_before": raw_json,
            "raw_sha256": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
            "full_physical_before": before,
        }
        observations.append(record)
        for name, call in [("get", lambda: query.get(run_id)),
                           ("trace", lambda: query.trace(run_id))]:
            record[name] = _read(call)
            after = _physical_rows(path)
            record[name + "_physical_after"] = after
            record[name + "_raw_after"] = _raw_error(path, run_id)
            assert after == before
            assert record[name + "_raw_after"] == raw_json
        with database.session() as session:
            row = session.query(ResearchTraceRecord).filter_by(run_id=run_id).one()
            failure = _read(lambda: _trace_failure(row))
        record["trace_failure"] = failure
        record["trace_failure_physical_after"] = _physical_rows(path)
        record["raw_after"] = _raw_error(path, run_id)
        record["connect_attempts"] = len(connects)
        assert record["trace_failure_physical_after"] == before
        assert record["raw_after"] == raw_json
        assert not connects
        unverified = case in {"attempts_null", "null_long_summary"}
        invalid = case in {
            "null_bad_origin", "null_bad_retryable", "null_bad_hash", "null_code_mismatch",
            "bad_origin", "bad_json",
        }
        for name in ["get", "trace"]:
            actual = record[name]
            assert isinstance(actual, dict)
            assert (actual["exception"] is not None) == invalid
            if invalid:
                if "origin" in case:
                    errors = actual["validation_errors"]
                    assert isinstance(errors, list)
                    assert any(error["loc"][-1] == "origin" for error in errors)
        if not invalid:
            detail_read = record["get"]
            trace_read = record["trace"]
            assert isinstance(detail_read, dict) and isinstance(trace_read, dict)
            detail = detail_read["value"]
            trace = trace_read["value"]
            assert isinstance(detail, dict) and isinstance(trace, list)
            assert detail["run"]["run_id"] == run_id
            assert detail["run"]["status"] == "queued"
            assert len(trace) == 1 and trace[0]["sequence_no"] == 1
            expected_error = (
                None if unverified
                else raw | {"provider_attempts": raw.get("provider_attempts", [])}
            )
            assert trace[0]["error"] == expected_error
            assert detail["trace"][0]["error"] == expected_error
            assert trace[0]["summary"] == saved.summary
            assert trace[0]["reference_id"] == saved.reference_id
            assert trace[0]["error_code"] == saved.error_code
            if unverified:
                assert trace[0]["error_verification"] == "legacy_provider_attempts_unverifiable"
                assert detail["trace"][0]["error_verification"] == trace[0]["error_verification"]
                assert (
                    "legacy_provider_attempts_unverifiable"
                    in detail["action_summary"]["warning_codes"]
                )
                assert detail["run"]["failure"] is None
            else:
                assert trace[0].get("error_verification") is None
        if invalid:
            assert failure["exception"] is not None
            return
        assert failure["exception"] is None
        projected = failure["value"]
        if unverified:
            assert projected is None
            return
        assert isinstance(projected, dict)
        assert projected["error_code"] == raw["error_code"]
        assert projected == (raw | {"provider_attempts": raw.get("provider_attempts", [])})
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("kind", ["marker", "attempts_null"])
def test_trace_write_does_not_accept_historical_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    path = tmp_path / "write.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    try:
        database.create_all()
        event_id, _, _ = AdmissionService(database, clock=lambda: NOW).admit(
            ObservationCreate(
                text="Controlled original error write, not a Provider execution.",
                source_id="write-control", source_type=SourceType.transcript,
                observed_at=NOW, published_at=NOW, language="en",
                event_hint="central_bank_speech",
            )
        )
        run_id, _ = RunService(database, clock=lambda: NOW).create(
            event_id, strategy_version="research.v1",
        )
        before = _physical_rows(path)
        payload = {
            "schema_version": "research-trace-event.v1", "run_id": run_id,
            "research_session_id": f"controlled:{run_id}", "sequence_no": 0,
            "event_type": "tool_failed", "occurred_at": NOW, "stage": "researching",
            "summary": "Original strict write.", "reference_type": "capability_call",
            "reference_id": "original-call", "status": "failed",
            "error_code": "provider_original_timeout", "error": _original_error(),
        }
        if kind == "attempts_null":
            payload["error"] = _original_error() | {"provider_attempts": None}
            with pytest.raises(ValidationError):
                ResearchTraceEvent.model_validate(payload)
        else:
            payload["error_verification"] = "legacy_provider_attempts_unverifiable"
            event = ResearchTraceEvent.model_validate(payload)
            with pytest.raises(ValueError, match="research_trace_read_projection_not_writable"):
                ResearchObservabilityService(database, clock=lambda: NOW).append_trace(event)
        assert _physical_rows(path) == before
        payload.pop("error_verification", None)
        payload["error"] = _original_error()
        valid = ResearchTraceEvent.model_validate(payload)
        service = ResearchObservabilityService(database, clock=lambda: NOW)
        saved = service.append_trace(valid)
        after = _physical_rows(path)
        assert service.append_trace(valid) == saved
        assert _physical_rows(path) == after
        with database.session() as session:
            row = session.query(ResearchTraceRecord).filter_by(run_id=run_id).one()
            assert row.event_hash == _payload_hash(saved.model_dump(
                mode="json", exclude={"sequence_no", "error_verification"},
            ))
    finally:
        database.engine.dispose()
