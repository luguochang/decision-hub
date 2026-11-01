"""Synthetic call-bound instruction fixture; never a live SDK/native source proof."""
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionRequest

ROOT = Path(__file__).resolve().parents[2]
RESERVED = "decision-hub.reviewer-instruction-bindings"


def synthetic_case(*, baseline=True):
    raw = json.loads((ROOT / "artifacts/d2-native-v2/native-transcript.json").read_text("utf-8"))
    binding = raw["context"]["binding"]
    events = raw["context"]["events"]
    wrapper = events[4]["data"]["value"]
    source = {
        "kind": "agent-instructions", "form": "instructions", "baseline": True,
        "baselineIdentity": "synthetic-baseline", "changes": [{
            "action": "set", "scope": "project", "path": "AGENTS.md", "digest": "a" * 40,
        }],
    }
    content = [{"type": "text", "text": "Synthetic frozen parent instructions."}]
    if baseline:
        child = json.loads(wrapper["child_events_json"])
        child.insert(1, {"type": "user/message", "data": {
            "id": "child-baseline", "role": "user", "source": deepcopy(source),
            "content": deepcopy(content),
        }})
        for seq, event in enumerate(child, 1):
            event["seq"] = seq
        wrapper["child_events_json"] = json.dumps(child, ensure_ascii=False)
        wrapper["child_events_sha256"] = hashlib.sha256(
            wrapper["child_events_json"].encode()
        ).hexdigest()
    notes = [{"method": "session.event", "payload": {
        "sessionId": binding["sessionId"], "event": event,
    }} for event in events]
    encoded = json.dumps(notes, ensure_ascii=False)
    payload = dict(
        schema_version="dsh-session-result.v1", run_id=binding["runId"],
        dsh_session_id=binding["sessionId"], generation=1, last_seq=7, final_response="",
        finish_reason="completed", events_json=encoded,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 0, 25, tzinfo=UTC), trace_ref="synthetic:baseline",
        result_hash=hashlib.sha256(("\0" + encoded).encode()).hexdigest(),
    )
    projection = {
        "schema_version": "dsh-reviewer-instruction-bindings.v1",
        "run_id": binding["runId"], "dsh_session_id": binding["sessionId"], "generation": 1,
        "request_id": binding["requestId"], "request_hash": binding["requestHash"],
        "bindings": [{"reviewer_call_id": "review", "reviewer_call_seq": 4,
                      "reviewer_generation": 1, "reviewer_request_id": binding["requestId"],
                      "parent_prefix_last_seq": 3, "parent_prefix_sha256": "b" * 64,
                      "baseline_event": {"seq": 1, "time": 0, "type": "user/message", "data": {
                          "id": "parent-baseline", "role": "user", "source": source,
                          "content": content,
                      }}}],
    }
    return payload, projection, ResearchSessionRequest.model_validate(raw["context"]["request"])


def result_payload(payload, projection=None):
    value = deepcopy(payload)
    if projection is not None:
        value["reviewer_instruction_bindings"] = projection
    return DshSessionResult.model_validate(value)


def change_child(payload, mutate):
    notes = json.loads(payload["events_json"])
    wrapper = notes[4]["payload"]["event"]["data"]["value"]
    child = json.loads(wrapper["child_events_json"])
    mutate(child)
    for seq, event in enumerate(child, 1):
        event["seq"] = seq
    wrapper["child_events_json"] = json.dumps(child, ensure_ascii=False)
    wrapper["child_events_sha256"] = hashlib.sha256(
        wrapper["child_events_json"].encode()
    ).hexdigest()
    payload["events_json"] = json.dumps(notes, ensure_ascii=False)
    payload["result_hash"] = hashlib.sha256(("\0" + payload["events_json"]).encode()).hexdigest()
