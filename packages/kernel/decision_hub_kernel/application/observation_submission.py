"""Atomic admission and durable receipts for the legacy text analysis entry."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProductCommandRecord,
    RunRecord,
    RunStatus,
    utcnow,
)


def submit_observation(
    analyzer: AnalyzeTextService, payload: ObservationCreate, request_id: str
) -> tuple[dict[str, str], bool]:
    """Return the original receipt on retry, including content-deduplicated inputs.

    Receipt, event and Run share one transaction. A conflicting key cannot leave
    an orphan event behind. Existing legacy Runs remain readable and adopt a
    receipt only when their event and strategy agree with this input.
    """
    if not request_id or len(request_id) > 128:
        raise ValueError("observation_request_id_invalid")
    fingerprint = hashlib.sha256(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    owner, kind = "legacy-observation-api", "legacy_observation"
    with analyzer.database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        previous = session.get(ProductCommandRecord, request_id)
        if previous:
            if (previous.owner_id, previous.kind, previous.payload_hash) != (
                owner,
                kind,
                fingerprint,
            ):
                raise ValueError("observation_idempotency_conflict")
            return json.loads(previous.result_json), False
        event_id, _envelope, admitted = analyzer.admission.admit(payload, session=session)
        keyed = session.query(RunRecord).filter_by(idempotency_key=request_id).first()
        if keyed and (
            keyed.event_id != event_id or keyed.strategy_version != analyzer.strategy_version
        ):
            raise ValueError("observation_idempotency_conflict")
        previous_run = keyed or (
            session.query(RunRecord)
            .filter_by(event_id=event_id, strategy_version=analyzer.strategy_version)
            .order_by(RunRecord.created_at.desc())
            .first()
            if not admitted
            else None
        )
        execute = False
        if previous_run:
            run_id = previous_run.run_id
        else:
            run_id, created = analyzer.runs.create(
                event_id, request_id, strategy_version=analyzer.strategy_version, session=session
            )
            execute = admitted and created
            if not admitted and created:
                run = session.get(RunRecord, run_id)
                run.status = RunStatus.degraded.value
                run.error_code = "duplicate_observation"
                run.finished_at = run.updated_at = utcnow()
        receipt = {"event_id": event_id, "run_id": run_id, "status_url": f"/v1/runs/{run_id}"}
        session.add(
            ProductCommandRecord(
                request_id=request_id,
                owner_id=owner,
                target_id=run_id,
                kind=kind,
                payload_hash=fingerprint,
                result_json=json.dumps(receipt),
                created_at=utcnow(),
            )
        )
        return receipt, execute
