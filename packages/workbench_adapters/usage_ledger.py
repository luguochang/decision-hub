"""Append-only, idempotent storage for metadata-only DSH usage observations."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from packages.contracts_py.decision_hub_contracts.generated.research_product_view import (
    ResearchUsageSnapshot,
    ResearchUsageView,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchUsageSnapshotRecord,
    RunRecord,
)
from packages.query_views.research_usage import usage_view


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read(row):
    payload = json.loads(row.payload_json)
    if hashlib.sha256(_canonical(payload).encode()).hexdigest() != row.payload_sha256:
        raise ValueError("usage_snapshot_integrity_failed")
    return ResearchUsageSnapshot.model_validate({**payload, "payload_sha256": row.payload_sha256})


def capture_usage(database, sessions_root, run_id, request_id, *, binding=None, prepared_view=None):
    identity = "usage_" + hashlib.sha256(_canonical([run_id, request_id]).encode()).hexdigest()
    with database.session() as session:
        existing = session.get(ResearchUsageSnapshotRecord, identity)
        if existing:
            return _read(existing)
        if session.get(RunRecord, run_id) is None:
            raise KeyError(run_id)
        link = session.get(DshSessionLinkRecord, run_id)
        linked = (link.dsh_session_id, link.generation) if link else (None, None)
        if binding is not None:
            prompt = session.get(DshSessionPromptRecord, (run_id, binding[1]))
            if binding != linked and (prompt is None or prompt.dsh_session_id != binding[0]):
                raise ValueError("usage_session_binding_unverified")
            linked = binding
    view = ResearchUsageView.model_validate(
        prepared_view if prepared_view is not None else usage_view(database, sessions_root, run_id)
    )
    if view.run_id != run_id or any(
        item.role == "main" and item.session_id != linked[0] for item in view.sessions
    ):
        raise ValueError("usage_session_binding_mismatch")
    payload = {
        "schema_version": "research-usage-snapshot.v1",
        "snapshot_id": identity,
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "accounting_version": "dsh-call-and-tool-metadata.v3",
        "session_generation": linked[1],
        "view": view.model_dump(mode="json", exclude_unset=True),
    }
    encoded = _canonical(payload)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    try:
        with database.session() as session:
            link = session.get(DshSessionLinkRecord, run_id)
            current = (link.dsh_session_id, link.generation) if link else (None, None)
            if binding is None and current != linked:
                raise ValueError("usage_session_changed_retry")
            session.add(ResearchUsageSnapshotRecord(
                snapshot_id=identity,
                run_id=run_id,
                recorded_at=datetime.fromisoformat(payload["recorded_at"]),
                payload_sha256=digest,
                payload_json=encoded,
            ))
    except IntegrityError:
        # A concurrent retry of the same command must return the committed receipt.
        with database.session() as session:
            row = session.get(ResearchUsageSnapshotRecord, identity)
            if row is None:
                raise
            return _read(row)
    return ResearchUsageSnapshot.model_validate({**payload, "payload_sha256": digest})


def usage_history(database, run_id, *, limit=20, offset=0):
    with database.session() as session:
        if session.get(RunRecord, run_id) is None:
            raise KeyError(run_id)
        total = session.query(ResearchUsageSnapshotRecord).filter_by(run_id=run_id).count()
        rows = session.scalars(
            select(ResearchUsageSnapshotRecord)
            .where(ResearchUsageSnapshotRecord.run_id == run_id)
            .order_by(
                ResearchUsageSnapshotRecord.recorded_at.desc(),
                ResearchUsageSnapshotRecord.snapshot_id.desc(),
            )
            .offset(offset).limit(limit)
        ).all()
        return {
            "items": [_read(row) for row in rows], "total": total, "limit": limit, "offset": offset
        }
