"""Canonical stored wire readback, including historical absence of the new Host field."""
import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from tests.decision_product.reviewer_instruction_binding_support import (
    result_payload,
    synthetic_case,
)


def saved_wire(tmp_path, *, projection):
    payload, binding, _ = synthetic_case(baseline=projection)
    result = result_payload(payload, binding if projection else None)
    body = {
        "schema_version": "research-transport-observation.v1",
        "context": {
            "run_id": result.run_id, "event_id": "synthetic-event", "snapshot_id": None,
            "request_id": "synthetic-product-request", "dsh_session_id": result.dsh_session_id,
            "generation": 1, "original_task_request_hash": "1" * 64, "bundle_hash": "2" * 64,
            "method_hash": "3" * 64, "execution_request_hash": "4" * 64,
            "first_linked_request_hash": "5" * 64, "prompt_hash": "6" * 64, "rows": [],
        },
        "phase": "result", "reply": result.model_dump(mode="json", exclude_unset=True),
        "error_code": None,
        "events_json_hash": hashlib.sha256(result.events_json.encode()).hexdigest(),
        "final_response_hash": hashlib.sha256(result.final_response.encode()).hexdigest(),
        "recorded_at": "2026-01-01T00:25:00Z",
    }
    # This is the old wire identity formula, independently applied to saved JSON.
    digest = hashlib.sha256(observation_json({
        key: value for key, value in body.items() if key != "recorded_at"
    }).encode()).hexdigest()
    encoded = observation_json(body)
    now = datetime(2026, 1, 1, 0, 25, tzinfo=UTC)
    reference = ObservedExecutionRef(
        output_id="research_transport_" + digest, role="research_transport:" + digest,
        schema_version=body["schema_version"],
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        recorded_at=now,
    )
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "observation.sqlite3"))
    RunRoleOutputRecord.__table__.create(engine)
    with Session(engine) as session:
        session.add(RunRoleOutputRecord(
            output_id=reference.output_id, run_id=result.run_id, role=reference.role,
            schema_version=reference.schema_version, payload_json=encoded,
            payload_hash=reference.payload_hash, created_at=now,
        ))
        session.commit()
    return engine, reference, encoded


def test_old_transport_row_without_optional_field_reads_with_unchanged_canonical_hash(tmp_path):
    engine, reference, encoded = saved_wire(tmp_path, projection=False)
    assert "reviewer_instruction_bindings" not in json.loads(encoded)["reply"]
    with Session(engine) as session:
        saved = read_transport_observation(session, reference)
        assert saved.reply.run_id == "native-v2-run"
        row = session.get(RunRoleOutputRecord, reference.output_id)
        assert row.payload_json == encoded and row.payload_hash == reference.payload_hash
    engine.dispose()


def test_new_host_projection_survives_the_canonical_transport_readback(tmp_path):
    engine, reference, encoded = saved_wire(tmp_path, projection=True)
    with Session(engine) as session:
        saved = read_transport_observation(session, reference)
        projection = saved.reply.reviewer_instruction_bindings
        assert projection.bindings[0].reviewer_call_id == "review"
        assert session.get(RunRoleOutputRecord, reference.output_id).payload_json == encoded
    engine.dispose()


@pytest.mark.parametrize("rehash", [False, True])
def test_stored_projection_tampering_cannot_reuse_old_observation_identity(tmp_path, rehash):
    engine, reference, _ = saved_wire(tmp_path, projection=True)
    with Session(engine) as session:
        row = session.get(RunRoleOutputRecord, reference.output_id)
        body = json.loads(row.payload_json)
        projection = body["reply"]["reviewer_instruction_bindings"]
        projection["bindings"][0]["parent_prefix_sha256"] = "f" * 64
        row.payload_json = observation_json(body)
        if rehash:
            row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
            reference = reference.model_copy(update={"payload_hash": row.payload_hash})
        session.commit()
    with Session(engine) as session, pytest.raises(ValueError, match="research_observation"):
        read_transport_observation(session, reference)
    engine.dispose()
