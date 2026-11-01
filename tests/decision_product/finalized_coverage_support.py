"""Synthetic consumer wire fixtures; these do not certify real child provenance."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionRequest
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
    extract_attested_perpetual_synthesis_captures,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run

ROOT = Path(__file__).resolve().parents[2]
CHILD = "synthetic-research-child"
NATIVE_CALL = "synthetic-native-child-call"
EVIDENCE_ID = "synthetic-child-evidence"
FACT_ID = "synthetic-child-fact"
PROVIDER_REFERENCE = "synthetic-provider-reference"


def wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class FinalizedCase:
    request: ResearchSessionRequest
    binding: dict[str, Any]
    events: list[dict[str, Any]]
    extra_notifications: list[dict[str, Any]] = field(default_factory=list)

    @property
    def final_call(self) -> dict[str, Any]:
        return next(row for row in self.events if row["type"] == "tool/call"
                    and row["data"].get("arguments", {}).get("action") == "finalize")

    @property
    def wrapper(self) -> dict[str, Any]:
        call_id = self.final_call["data"]["callId"]
        return next(row["data"]["value"] for row in self.events
                    if row["type"] == "tool/result" and row["data"].get("callId") == call_id)

    @property
    def delivery(self) -> dict[str, Any]:
        return self.wrapper["coverage_delivery"]

    @property
    def snapshot(self) -> dict[str, Any]:
        return next(row for row in self.events if row["type"] == "user/message"
                    and row["data"].get("id") == "synthetic-source-snapshot")

    def update_snapshot(self, *, declared_prefixes: bool = False, **metadata: object) -> None:
        body = json.loads(self.snapshot["data"]["content"][1]["text"])
        body.update(metadata)
        if declared_prefixes:
            body["child_research_prefixes"] = deepcopy(self.delivery["child_prefixes"])
        text = wire(body)
        self.snapshot["data"]["content"][1]["text"] = text
        self.delivery["source_snapshot"]["body_sha256"] = sha(text)

    def payload(self, *, finish_reason: str = "completed") -> DshSessionResult:
        notes = [{"method": "session.event", "payload": {
            "sessionId": self.binding["sessionId"], "event": row,
        }} for row in self.events] + self.extra_notifications
        text = wire(notes)
        at = datetime.fromtimestamp(self.final_call["time"] / 1000, tz=UTC)
        return DshSessionResult.model_validate({
            "schema_version": "dsh-session-result.v1", "run_id": self.request.run_id,
            "dsh_session_id": self.binding["sessionId"], "generation": 1,
            "last_seq": max(row["seq"] for row in self.events), "final_response": "",
            "finish_reason": finish_reason, "events_json": text,
            "started_at": at - timedelta(minutes=10), "finished_at": at + timedelta(seconds=1),
            "trace_ref": "synthetic://finalized-child-coverage", "result_hash": sha("\0" + text),
        })


def captures(case: FinalizedCase):
    payload = case.payload()
    return extract_attested_perpetual_synthesis_captures(
        _sdk_run(payload).notifications, expected_session_id=payload.dsh_session_id,
        request=case.request,
    )


def mapped(case: FinalizedCase, *, finish_reason: str = "completed"):
    from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
    from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness

    payload = case.payload(finish_reason=finish_reason)
    run = _sdk_run(payload)
    traces = map_notifications(
        run.notifications, run_id=case.request.run_id, research_session_id=run.session_id,
        received_at=payload.finished_at, request=case.request,
    )
    return map_session_result_with_witness(
        run, case.request, traces, runtime_version="synthetic-r5j-consumer",
        profile_ref="decision-research.web.v2", started_at=payload.started_at,
        finished_at=payload.finished_at, accepted_synthesis_only=True,
    )


def _child_rows(request, binding, at):
    evidence = {
        "evidence_id": EVIDENCE_ID, "requirement_id": "event_identity", "kind": "official",
        "authority": "official", "source_id": "synthetic-official",
        "source_url": "https://example.invalid/original", "published_at": at,
        "observed_at": at, "received_at": at, "content_hash": "a" * 64,
        "excerpt": "Synthetic retained official evidence.", "content_ref": None,
        "acquisition_receipt_id": None, "source_content_hash": None, "parser_ref": None,
        "structured_payload_ref": None, "tool_call_id": PROVIDER_REFERENCE,
        "research_session_id": binding["sessionId"], "round": request.current_round,
        "quality": "accepted", "freshness_status": "fresh", "conflict_group": None,
    }
    fact = {
        "schema_version": "fact-envelope.v1", "fact_id": FACT_ID, "evidence_id": EVIDENCE_ID,
        "requirement_id": "event_identity", "metric_family": "synthetic.identity",
        "field": "value", "instrument": None, "venue": None, "value": "42.5", "unit": "count",
        "window_start_at": None, "window_end_at": None, "event_offset": None,
        "observed_at": at, "received_at": at, "published_at": at,
        "source_id": "synthetic-official", "independence_group": "synthetic-independent",
        "quality": "accepted", "delay_class": "historical", "payload_schema_ref": "synthetic.v1",
        "payload_hash": "c" * 64, "attributes": {
            "exact_execution": '{"receipt":"kept\\nverbatim","value":"42.5"}',
            "strategy_receipt": '{"source":"synthetic","bar_count":89}',
            "nullable": None, "flag": True, "ratio": 1.25,
        },
    }
    return evidence, fact


def _snapshot(request, binding, prefix, evidence, facts, seq):
    body = {
        "schema_version": "decision-hub-protected-context.v5",
        "snapshot_id": "synthetic-source-snapshot", "run_id": binding["runId"],
        "generation": binding["generation"], "root_session_id": binding["sessionId"],
        "request_hash": binding["requestHash"], "plugin_build_hash": "d" * 64,
        "request_id": request.request_id, "command_request_id": binding["requestId"],
        "native_first_seq": 1, "native_last_seq": seq - 1,
        "frozen_input_evidence": [], "evidence_candidates": [deepcopy(evidence)],
        "facts": deepcopy(facts), "independent_reviews": [],
        "child_research_prefixes": [deepcopy(prefix)], "pending_evidence_delivery": [],
        "proof_scope": "dsh_llm_generate_options", "model_understanding_verified": False,
        "budget_qualified": False, "financial_qualified": False,
    }
    return {"seq": seq, "type": "user/message", "data": {
        "id": body["snapshot_id"], "role": "user",
        "source": {"kind": "plugin", "plugin": "decision-hub-protected-context"},
        "content": [{"type": "text", "text": "Synthetic authenticated-source reference."},
                    {"type": "text", "text": wire(body)}],
    }}


def _root_pair(request, evidence, facts, seq):
    return [
        {"seq": seq, "type": "tool/call", "data": {
            "callId": "synthetic-root-call", "name": "research_capability_execute",
            "arguments": {"request_id": request.request_id},
        }},
        {"seq": seq + 1, "type": "tool/result", "data": {
            "callId": "synthetic-root-call", "value": {
                "schema_version": "research-capability-result.v1", "request_id": request.request_id,
                "capability_id": "replay.research", "provider": "synthetic",
                "evidence_candidates": [deepcopy(evidence)], "facts": deepcopy(facts),
                "cost_usd": None, "completed_at": evidence["received_at"], "provider_attempts": [],
            },
        }},
    ]


def make_case(*, version="v5", event_plan=False, facts=True, root_duplicate=False) -> FinalizedCase:
    path = (
        "artifacts/d2-event-scenario-action-plans/native-event-ts-proof.json" if event_plan
        else "artifacts/d2-native-preparation/phased-native-transcript.json"
    )
    raw = json.loads((ROOT / path).read_text("utf-8"))
    context = raw if event_plan else raw["context"]
    request = ResearchSessionRequest.model_validate(context["request"])
    binding, events = deepcopy(context["binding"]), deepcopy(context["events"])
    call, result = events[-2:]
    events = events[:-2]
    at = (datetime.fromtimestamp(call["time"] / 1000, tz=UTC) - timedelta(seconds=10)).isoformat()
    evidence, fact = _child_rows(request, binding, at)
    fact_rows = [fact] if facts else []
    prefix = {
        "sessionId": CHILD, "parentSessionId": binding["sessionId"], "firstLiveSeq": 2,
        "lifecycleId": "synthetic-lifecycle", "lastSeq": 5, "eventsHash": "e" * 64,
        "providerCalls": [{"callId": NATIVE_CALL, "receiptHash": "f" * 64}],
    }
    if root_duplicate:
        events.extend(_root_pair(request, evidence, fact_rows, events[-1]["seq"] + 1))
    snapshot = _snapshot(request, binding, prefix, evidence, fact_rows, events[-1]["seq"] + 1)
    events.append(snapshot)
    call["seq"], result["seq"] = snapshot["seq"] + 1, snapshot["seq"] + 2
    if version == "v5":
        result["data"]["value"].update({
            "schema_version": "decision-hub-synthesis-tool-result.v5",
            "coverage_delivery": {
                "schema_version": "decision-hub-finalized-coverage.v1",
                "binding": deepcopy(binding),
                "finalize_call_id": call["data"]["callId"], "finalize_call_seq": call["seq"],
                # Sparse fixtures cannot certify the omitted full root prefix.
                "root_cutoff": {"before_seq": call["seq"], "events_sha256": "b" * 64},
                "source_snapshot": {"message_id": snapshot["data"]["id"],
                                    "message_seq": snapshot["seq"],
                                    "body_sha256": sha(snapshot["data"]["content"][1]["text"])},
                "child_prefixes": [prefix], "evidence_sources": [{
                    "evidence_id": EVIDENCE_ID, "capability_id": "replay.research",
                    "execution_session_id": CHILD, "native_call_id": NATIVE_CALL,
                }], "evidence_candidates": [evidence], "facts": fact_rows,
            },
        })
    events.extend([call, result])
    return FinalizedCase(request, binding, events)


def set_path(value: dict[str, Any], path: tuple[str | int, ...], replacement: object) -> None:
    target: Any = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
