"""Immutable planning outcomes, including reasons for producing no executable candidate."""

import hashlib
import json

from packages.kernel.decision_hub_kernel.persistence.db import (
    EvolutionContextSnapshotRecord,
    EvolutionJobRecord,
    EvolutionPlanningDecisionRecord,
)


def planning_decision(database, job_id, value=None):
    with database.session() as session:
        if session.get(EvolutionJobRecord, job_id) is None:
            raise KeyError(job_id)
        frozen = session.get(EvolutionContextSnapshotRecord, job_id)
        existing = session.get(EvolutionPlanningDecisionRecord, job_id)
        if existing:
            if (not frozen or existing.context_hash != frozen.content_hash
                    or hashlib.sha256(existing.content_json.encode()).hexdigest()
                    != existing.content_hash):
                raise ValueError("planning_decision_integrity_invalid")
            result = json.loads(existing.content_json)
            if value is not None and result != value:
                raise ValueError("planning_decision_conflict")
            return result
        if value is None:
            return None
        if not frozen:
            raise ValueError("planning_decision_requires_frozen_context")
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        session.add(EvolutionPlanningDecisionRecord(
            job_id=job_id, context_hash=frozen.content_hash,
            content_hash=hashlib.sha256(body.encode()).hexdigest(), content_json=body,
        ))
        return value
