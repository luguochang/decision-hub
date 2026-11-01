"""Existing 0093 ledger writer/read envelope, with caller-specific original reauthentication."""

from __future__ import annotations

from datetime import datetime

from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ResearchSkillUseEventRecord,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, as_utc

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from .research_skill_package import decoded, encoded, sha
from .research_skill_use import COLUMNS

ROLE = "skill_node_proof"


def event_key(use_id, stage, node_id):
    # First seed has exactly one attempt per node. A second caller key cannot
    # bypass an earlier result or silently expand the attempt denominator.
    return canonical_hash([use_id, stage, node_id])[:40]


def freeze_event(
    session,
    contracts,
    *,
    selection,
    execution_session_id,
    stage,
    node_id,
    stage_request_id,
    parent_event,
    details,
    intent,
    known_at,
):
    if not isinstance(stage_request_id, str) or not 1 <= len(stage_request_id) <= 128:
        raise ValueError("skill_stage_request_invalid")
    now = aware_utc(known_at)
    key = event_key(selection["use_id"], stage, node_id)
    event_id, original_id = "skill_evt_" + key, "skill_node_" + key
    original = dict(
        schema_version="research-skill-node-original.v1",
        intent=intent,
        details=details,
        known_at=now.isoformat(),
    )
    reference = dict(
        output_id=original_id,
        role=ROLE + ":" + key,
        schema_version=original["schema_version"],
        payload_hash=canonical_hash(original),
        recorded_at=now.isoformat(),
    )
    body = dict(
        schema_version="research-skill-use-event.v1",
        event_id=event_id,
        **{
            name: selection[name]
            for name in (
                "use_id",
                "run_id",
                "generation",
                "skill_version_id",
                "package_hash",
                "workflow_hash",
                "method_binding_hash",
            )
        },
        root_session_id=execution_session_id,
        execution_session_id=execution_session_id,
        stage=stage,
        node_id=node_id,
        stage_request_id=stage_request_id,
        parent_event=parent_event,
        original=reference,
        first_known_at=now.isoformat(),
        details=details,
    )
    contracts.check("use_event_v1", body)
    existing = session.get(ResearchSkillUseEventRecord, event_id, populate_existing=True)
    existing_original = session.get(RunRoleOutputRecord, original_id, populate_existing=True)
    if existing is not None or existing_original is not None:
        if (
            existing is None
            or existing_original is None
            or existing.payload_json.encode() != encoded(body)
            or existing.payload_hash != canonical_hash(body)
            or existing_original.payload_json.encode() != encoded(original)
            or existing_original.payload_hash != canonical_hash(original)
        ):
            raise ValueError("skill_node_stage_key_conflict")
        checked, saved_intent = read_envelope(
            session,
            contracts,
            run_id=selection["run_id"],
            event_ref={"event_id": event_id, "payload_hash": canonical_hash(body)},
            stage=stage,
        )
        if checked != body or saved_intent != intent:
            raise ValueError("skill_node_stage_key_conflict")
        return checked
    session.add(
        RunRoleOutputRecord(
            output_id=original_id,
            run_id=selection["run_id"],
            role=reference["role"],
            schema_version=original["schema_version"],
            payload_json=encoded(original).decode(),
            payload_hash=canonical_hash(original),
            created_at=now,
        )
    )
    session.add(
        ResearchSkillUseEventRecord(
            **{name: body[name] for name in COLUMNS},
            parent_event_ref=encoded(parent_event).decode(),
            original_ref=encoded(reference).decode(),
            original_hash=reference["payload_hash"],
            first_known_at=now,
            payload_json=encoded(body).decode(),
            payload_hash=canonical_hash(body),
        )
    )
    session.flush()
    return body


def read_envelope(session, contracts, *, run_id, event_ref, stage):
    row = session.get(ResearchSkillUseEventRecord, event_ref["event_id"], populate_existing=True)
    if row is None:
        raise ValueError("skill_node_event_missing")
    body = decoded(row.payload_json)
    contracts.check("use_event_v1", body)
    if (
        row.run_id != run_id
        or body["stage"] != stage
        or row.payload_hash != event_ref["payload_hash"]
        or row.payload_hash != canonical_hash(body)
        or row.payload_json.encode() != encoded(body)
        or any(getattr(row, key) != body[key] for key in COLUMNS)
        or row.original_ref != encoded(body["original"]).decode()
        or row.parent_event_ref != encoded(body["parent_event"]).decode()
        or row.original_hash != body["original"]["payload_hash"]
        or as_utc(row.first_known_at) != datetime.fromisoformat(body["first_known_at"])
    ):
        raise ValueError("skill_node_event_invalid")
    key = event_key(body["use_id"], stage, body["node_id"])
    reference = body["original"]
    original = session.get(RunRoleOutputRecord, reference["output_id"], populate_existing=True)
    if original is None:
        raise ValueError("skill_node_original_missing")
    payload = decoded(original.payload_json)
    if (
        body["event_id"] != "skill_evt_" + key
        or original.output_id != "skill_node_" + key
        or original.role != ROLE + ":" + key
        or reference["role"] != original.role
        or original.run_id != run_id
        or original.schema_version != "research-skill-node-original.v1"
        or reference["schema_version"] != original.schema_version
        or payload.get("schema_version") != original.schema_version
        or original.payload_hash != reference["payload_hash"]
        or sha(original.payload_json.encode()) != original.payload_hash
        or original.payload_json.encode() != encoded(payload)
        or as_utc(original.created_at) != datetime.fromisoformat(reference["recorded_at"])
        or reference["recorded_at"] != body["first_known_at"]
        or payload.get("known_at") != body["first_known_at"]
        or payload.get("details") != body["details"]
        or set(payload) != {"schema_version", "intent", "details", "known_at"}
    ):
        raise ValueError("skill_node_original_invalid")
    return body, payload["intent"]


def node_output_ref(event):
    details = event["details"]
    return dict(
        node_id=event["node_id"],
        event_ref={"event_id": event["event_id"], "payload_hash": canonical_hash(event)},
        **{name: details[name] for name in ("output_contract", "output_ref", "output_hash")},
    )
