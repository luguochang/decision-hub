"""Atomic intake receipts for the local research-observation API.

The fixed service identity scopes this single-machine entry. It is not account
authentication, and this internal receipt is not a new public API contract.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ObservationCreate, ResearchRunQueued
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.research_intake import (
    normalize_latest_advice_request,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    EventRecord,
    ObservationRecord,
    ProductCommandRecord,
    RunRecord,
    as_utc,
    utcnow,
)

SERVICE_OWNER = "research-observation-api"
COMMAND_KIND = "research_submission"
INTENT_CONFLICT = "research_submission_intent_conflict"
LEGACY_BINDING_UNAVAILABLE = "research_submission_legacy_binding_unavailable"
RECEIPT_CORRUPT = "research_submission_receipt_corrupt"


class _ObservationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    event_id: str
    source_id: str
    source_type: str
    observed_at: AwareDatetime
    published_at: AwareDatetime | None
    received_at: AwareDatetime
    language: str
    text: str
    content_hash: str
    source_url: str | None
    event_hint: str | None
    scheduled_at: AwareDatetime | None
    revision_of: str | None


class _EventBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_type: str
    occurred_at: AwareDatetime
    received_at: AwareDatetime


class _SubmissionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-submission-receipt.internal.v1"]
    request_id: str = Field(min_length=1, max_length=128)
    owner_id: Literal["research-observation-api"]
    kind: Literal["research_submission"]
    original_input: ObservationCreate
    normalized_input: ObservationCreate
    admitted_observation: _ObservationBinding
    admitted_event: _EventBinding
    result: ResearchRunQueued
    receipt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class _OwnerSubmissionReceipt(_SubmissionReceipt):
    schema_version: Literal["research-submission-receipt.internal.v2"]
    owner_id: str = Field(min_length=1, max_length=128)
    created_at: AwareDatetime
    admitted_run_created_at: AwareDatetime


def _product_owner(owner_id: str) -> str:
    if (not owner_id or owner_id != owner_id.strip() or len(owner_id) > 128
            or owner_id in {SERVICE_OWNER, "legacy-research-command-internal"}):
        raise ValueError("research_submission_owner_invalid")
    return owner_id


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: ObservationCreate, owner_id: str = SERVICE_OWNER) -> str:
    intent = {"owner_id": owner_id, "kind": COMMAND_KIND,
              "original_input": payload.model_dump(mode="json")}
    return hashlib.sha256(_canonical(intent).encode("utf-8")).hexdigest()


def _observation_binding(row: ObservationRecord) -> _ObservationBinding:
    return _ObservationBinding.model_validate({
        "observation_id": row.observation_id, "event_id": row.event_id,
        "source_id": row.source_id, "source_type": row.source_type,
        "observed_at": as_utc(row.observed_at), "published_at": as_utc(row.published_at),
        "received_at": as_utc(row.received_at), "language": row.language,
        "text": row.text, "content_hash": row.content_hash, "source_url": row.source_url,
        "event_hint": row.event_hint, "scheduled_at": as_utc(row.scheduled_at),
        "revision_of": row.revision_of,
    })


def _event_binding(row: EventRecord) -> _EventBinding:
    return _EventBinding.model_validate({
        "event_id": row.event_id, "event_type": row.event_type,
        "occurred_at": as_utc(row.occurred_at), "received_at": as_utc(row.received_at),
    })


def _original_receipt(
    session: Session, record: ProductCommandRecord, payload: ObservationCreate
) -> ResearchRunQueued:
    if record.owner_id != SERVICE_OWNER or record.kind != COMMAND_KIND:
        raise ValueError(INTENT_CONFLICT)
    try:
        receipt = _SubmissionReceipt.model_validate_json(record.result_json)
        if (
            receipt.request_id != record.request_id
            or hashlib.sha256(_canonical(receipt.model_dump(
                mode="json", exclude={"receipt_hash"}
            )).encode("utf-8")).hexdigest() != receipt.receipt_hash
            or _fingerprint(receipt.original_input) != record.payload_hash
            or record.target_id != receipt.result.run_id
            or receipt.result.status_url != f"/v1/runs/{receipt.result.run_id}"
        ):
            raise ValueError(RECEIPT_CORRUPT)
        run = session.get(RunRecord, receipt.result.run_id)
        admitted_event = session.get(EventRecord, receipt.result.event_id)
        observation = session.get(ObservationRecord, receipt.admitted_observation.observation_id)
        if (
            run is None
            or run.idempotency_key != record.request_id
            or run.strategy_version != "research.v1"
            or run.event_id != receipt.result.event_id
            or run.parent_run_id is not None
            or run.admission_origin != "manual"
            or admitted_event is None
            or observation is None
            or receipt.admitted_observation.event_id != receipt.result.event_id
            or _observation_binding(observation) != receipt.admitted_observation
            or _event_binding(admitted_event) != receipt.admitted_event
            or receipt.normalized_input.text != receipt.admitted_observation.text
        ):
            raise ValueError(RECEIPT_CORRUPT)
    except ValueError as exc:
        raise ValueError(RECEIPT_CORRUPT) from exc
    if (
        _fingerprint(payload) != record.payload_hash
        or _canonical(payload.model_dump(mode="json"))
        != _canonical(receipt.original_input.model_dump(mode="json"))
    ):
        raise ValueError(INTENT_CONFLICT)
    # Do not normalize again, consult current policies, or replace the first
    # queued receipt with the Run's later execution/terminal status.
    return receipt.result


def _verified_owner_receipt(
    session: Session, record: ProductCommandRecord, owner_id: str
) -> _OwnerSubmissionReceipt:
    """Prove the original owner-bound root creation, never infer it from a header."""
    _product_owner(owner_id)
    if record.owner_id != owner_id or record.kind != COMMAND_KIND:
        raise ValueError(INTENT_CONFLICT)
    try:
        receipt = _OwnerSubmissionReceipt.model_validate_json(record.result_json)
        run = session.get(RunRecord, receipt.result.run_id)
        observation = session.get(ObservationRecord, receipt.admitted_observation.observation_id)
        admitted_event = session.get(EventRecord, receipt.result.event_id)
        if (
            receipt.owner_id != owner_id
            or receipt.request_id != record.request_id
            or record.target_id != receipt.result.run_id
            or _fingerprint(receipt.original_input, owner_id) != record.payload_hash
            or hashlib.sha256(_canonical(receipt.model_dump(
                mode="json", exclude={"receipt_hash"}
            )).encode("utf-8")).hexdigest() != receipt.receipt_hash
            or as_utc(record.created_at) != receipt.created_at
            or receipt.result.status_url != f"/v1/runs/{receipt.result.run_id}"
            or run is None
            or run.idempotency_key != record.request_id
            or run.strategy_version != "research.v1"
            or run.event_id != receipt.result.event_id
            or run.parent_run_id is not None
            or run.admission_origin != "manual"
            or as_utc(run.created_at) != receipt.admitted_run_created_at
            or receipt.admitted_run_created_at > receipt.created_at
            or observation is None
            or admitted_event is None
            or receipt.admitted_observation.event_id != receipt.result.event_id
            or _observation_binding(observation) != receipt.admitted_observation
            or _event_binding(admitted_event) != receipt.admitted_event
            or receipt.normalized_input.text != receipt.admitted_observation.text
        ):
            raise ValueError(RECEIPT_CORRUPT)
        return receipt
    except (ValueError, TypeError) as exc:
        raise ValueError(RECEIPT_CORRUPT) from exc


def owner_root_creation_receipt_in_session(
    session: Session, run_id: str, owner_id: str
) -> _OwnerSubmissionReceipt | None:
    _product_owner(owner_id)
    rows = session.scalars(select(ProductCommandRecord).where(
        ProductCommandRecord.target_id == run_id,
        ProductCommandRecord.owner_id == owner_id,
        ProductCommandRecord.kind == COMMAND_KIND,
    )).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError(RECEIPT_CORRUPT)
    return _verified_owner_receipt(session, rows[0], owner_id)


def submit_research_observation(
    analyzer: AnalyzeTextService, payload: ObservationCreate, request_id: str,
    *, owner_id: str | None = None,
) -> ResearchRunQueued:
    """Freeze the complete original input and admission in one transaction.

    Historical keys without this receipt fail closed: normalized observations
    cannot prove the user's original language, source type, times or text.
    Their existing Run and report remain readable through the existing API.
    """
    if not request_id or request_id != request_id.strip() or len(request_id) > 128:
        raise ValueError("research_submission_request_id_invalid")
    owner = _product_owner(owner_id) if owner_id is not None else SERVICE_OWNER
    with analyzer.database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        previous = session.get(ProductCommandRecord, request_id)
        if previous is not None:
            if owner_id is None:
                return _original_receipt(session, previous, payload)
            receipt = _verified_owner_receipt(session, previous, owner)
            if (_fingerprint(payload, owner) != previous.payload_hash
                    or _canonical(payload.model_dump(mode="json"))
                    != _canonical(receipt.original_input.model_dump(mode="json"))):
                raise ValueError(INTENT_CONFLICT)
            return receipt.result
        if session.scalar(select(RunRecord.run_id).where(
            RunRecord.idempotency_key == request_id
        ).limit(1)) is not None:
            raise ValueError(LEGACY_BINDING_UNAVAILABLE)
        normalized = normalize_latest_advice_request(payload)
        event_id, run_id, created = analyzer.queue_research(
            normalized, request_id, session=session
        )
        if not created:
            # The write lock and legacy check should make this unreachable;
            # never create a new proof for an unproven pre-existing Run.
            raise ValueError(LEGACY_BINDING_UNAVAILABLE)
        result = ResearchRunQueued(
            schema_version="research-run-queued.v1", event_id=event_id,
            run_id=run_id, status="queued", status_url=f"/v1/runs/{run_id}",
        )
        # Admission's original global content dedup remains intact. Freeze the
        # actual admitted row separately; do not invent normalized metadata on
        # an older content-equivalent Observation.
        observation = session.scalar(select(ObservationRecord).where(
            ObservationRecord.event_id == event_id
        ).order_by(ObservationRecord.received_at, ObservationRecord.observation_id).limit(1))
        admitted_event = session.get(EventRecord, event_id)
        if observation is None or admitted_event is None or observation.text != normalized.text:
            raise ValueError(RECEIPT_CORRUPT)
        recorded_at = utcnow()
        receipt_body = {
            "schema_version": "research-submission-receipt.internal.v1",
            "request_id": request_id, "owner_id": owner, "kind": COMMAND_KIND,
            "original_input": payload.model_dump(mode="json"),
            "normalized_input": normalized.model_dump(mode="json"),
            "admitted_observation": _observation_binding(observation).model_dump(mode="json"),
            "admitted_event": _event_binding(admitted_event).model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        receipt_type = _SubmissionReceipt
        if owner_id is not None:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise ValueError(RECEIPT_CORRUPT)
            receipt_body.update(
                schema_version="research-submission-receipt.internal.v2",
                created_at=recorded_at.isoformat().replace("+00:00", "Z"),
                admitted_run_created_at=as_utc(run.created_at).isoformat().replace("+00:00", "Z"),
            )
            receipt_type = _OwnerSubmissionReceipt
        receipt = receipt_type.model_validate({
            **receipt_body,
            "receipt_hash": hashlib.sha256(_canonical(receipt_body).encode("utf-8")).hexdigest(),
        })
        session.add(ProductCommandRecord(
            request_id=request_id, owner_id=owner, target_id=run_id,
            kind=COMMAND_KIND, payload_hash=_fingerprint(payload, owner),
            result_json=_canonical(receipt.model_dump(mode="json")), created_at=recorded_at,
        ))
        return result
