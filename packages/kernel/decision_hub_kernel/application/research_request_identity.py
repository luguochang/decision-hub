"""Identity shared by DSH submission and historical coverage evaluation."""

import hashlib
import json

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest


def research_request_hash(request: ResearchSessionRequest) -> str:
    # Continuations keep the original Run identity; evidence and deadlines vary.
    payload = request.model_dump(
        mode="json",
        exclude={
            "current_round", "evidence_refs", "input_evidence", "target_gaps",
            "repair_instructions", "deadline_at",
        },
    )
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
