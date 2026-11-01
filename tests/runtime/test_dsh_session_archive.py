from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import zstandard

from packages.contracts_py.decision_hub_contracts import (
    EvidenceRequirement,
    ExecutionBudget,
    ResearchInputEvidence,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import load_dsh_session_archive
from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
    extract_attested_review_receipts,
)

NOW = datetime(2026, 9, 11, 8, 34, 48, tzinfo=UTC)
SESSION_ID = "dsh_archive_session_1"


@pytest.mark.parametrize("invalid_time", [True, float("inf"), float("nan"), "invalid"])
def test_invalid_event_time_preserves_aware_iso_fallback(invalid_time: object) -> None:
    notification = DshSdkNotification(
        method="session.event",
        payload={
            "sessionId": SESSION_ID,
            "timestamp": NOW.isoformat(),
            "event": {
                "type": "tool/call",
                "time": invalid_time,
                "data": {"callId": "call-1", "name": "search", "arguments": "{}"},
            },
        },
    )
    traces = map_notifications(
        [notification],
        run_id="run-archive-1",
        research_session_id=SESSION_ID,
        received_at=NOW + timedelta(hours=1),
    )
    assert len(traces) == 1
    assert traces[0].occurred_at == NOW


def _request() -> ResearchSessionRequest:
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id="request-archive-1",
        run_id="run-archive-1",
        event_id="event-archive-1",
        trigger_snapshot_id="trigger-archive-1",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode="replay",
        pit_cutoff_at=NOW,
        current_round=1,
        evidence_refs=["trigger-evidence"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="trigger-evidence",
                kind="official",
                authority="official",
                source_id="federal-reserve",
                source_url=None,
                published_at=NOW - timedelta(minutes=1),
                observed_at=NOW - timedelta(seconds=2),
                received_at=NOW - timedelta(seconds=1),
                content_hash="0" * 64,
                excerpt="The policy decision was published.",
            )
        ],
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="event_identity",
                description="Verify the event identity.",
                importance="hard",
                source_priority=["official"],
                authority_floor="official",
                preferred_capabilities=["replay.research"],
                freshness_seconds=3600,
                minimum_independent_sources=1,
                allowed_fallbacks=[],
                confidence_cap=0.45,
            )
        ],
        target_gaps=[],
        allowed_capabilities=["replay.research"],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=1,
            max_tool_calls=4,
            max_subagents=2,
            total_deadline_seconds=60,
            per_tool_timeout_seconds=10,
            per_model_step_timeout_seconds=30,
            max_structured_repairs=1,
            max_estimated_cost_usd=1,
        ),
        deadline_at=NOW + timedelta(minutes=1),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


def _receipt() -> dict[str, object]:
    return {
        "schema_version": "independent-review-receipt.v1",
        "role": "independent_reviewer",
        "target_claim": "The decision supported BTC immediately.",
        "evidence_refs": ["ev-1"],
        "challenge": "The immediate price window moved against the proposed direction.",
        "resolution": "rejected",
        "limitations": ["Rates and USD event windows were unavailable."],
    }


def _raw_events() -> list[dict[str, object]]:
    def milliseconds(offset: int) -> int:
        return int((NOW + timedelta(seconds=offset)).timestamp() * 1000)

    request_text = "Bounded DSH research.\nCanonical request:\n" + _request().model_dump_json(
        exclude_none=False
    )
    return [
        {
            "type": "session",
            "version": 0,
            "id": SESSION_ID,
            "createdAt": milliseconds(0),
            "cwd": "C:\\workspace",
        },
        {
            "type": "agent/inbox/spliced",
            "seq": 1,
            "time": milliseconds(1),
            "data": {
                "inserted": [
                    {"role": "user", "content": [{"type": "text", "text": request_text}]}
                ]
            },
        },
        {
            "type": "tool/call",
            "seq": 2,
            "time": milliseconds(2),
            "data": {"callId": "review-failed", "name": "subagent", "arguments": "{}"},
        },
        {
            "type": "tool/result",
            "seq": 3,
            "time": milliseconds(3),
            "data": {
                "message": {
                    "source": {"kind": "tool", "callId": "review-failed"},
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "review-failed",
                            "content": [{"type": "text", "text": "reviewer timed out"}],
                            "isError": True,
                        }
                    ],
                }
            },
        },
        {
            "type": "tool/call",
            "seq": 4,
            "time": milliseconds(4),
            "data": {"callId": "review-ok", "name": "subagent", "arguments": "{}"},
        },
        {
            "type": "tool/result",
            "seq": 5,
            "time": milliseconds(5),
            "data": {
                "message": {
                    "source": {"kind": "tool", "callId": "review-ok"},
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "review-ok",
                            "content": [{"type": "text", "text": json.dumps(_receipt())}],
                            "isError": False,
                        }
                    ],
                }
            },
        },
        {
            "type": "turn/end",
            "seq": 6,
            "time": milliseconds(6),
            "data": {"turn": 1, "reason": {"kind": "aborted", "reason": {"kind": "user"}}},
        },
    ]


def _write_archive(path: Path, events: list[dict[str, object]]) -> bytes:
    # DSH appends independently compressed records; the archive is a sequence
    # of Zstandard frames rather than one monolithic frame.
    compressor = zstandard.ZstdCompressor()
    encoded = b"".join(
        compressor.compress((json.dumps(item) + "\n").encode()) for item in events
    )
    path.write_bytes(encoded)
    return encoded


def test_session_archive_binds_raw_events_to_header_and_recovers_review(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl.zstd"
    encoded = _write_archive(path, _raw_events())

    archive = load_dsh_session_archive(path, expected_session_id=SESSION_ID)

    assert archive.archive_sha256 == hashlib.sha256(encoded).hexdigest()
    assert archive.session_id == SESSION_ID
    assert len(archive.events) == 6
    assert len(archive.notifications) == 6
    assert archive.created_at == NOW
    assert archive.finished_at == NOW + timedelta(seconds=6)
    assert archive.finish_reason == "aborted"
    assert archive.canonical_request() == _request()
    assert archive.as_sdk_run().session_id == SESSION_ID
    assert all(item.payload.get("sessionId") == SESSION_ID for item in archive.notifications)

    receipts = extract_attested_review_receipts(
        archive.notifications,
        expected_session_id=SESSION_ID,
        allowed_evidence_ids=frozenset({"ev-1"}),
        fallback_created_at=NOW,
    )
    assert len(receipts) == 1
    assert receipts[0].target_claim == _receipt()["target_claim"]
    assert receipts[0].created_at == NOW + timedelta(seconds=5)

    traces = map_notifications(
        archive.notifications,
        run_id="run-archive-1",
        research_session_id=SESSION_ID,
        received_at=NOW,
    )
    reviewer_traces = [item for item in traces if item.event_type.startswith("subagent_")]
    assert [item.status for item in reviewer_traces] == [
        "running",
        "failed",
        "running",
        "succeeded",
    ]
    assert reviewer_traces[1].error_code == "dsh_subagent_failed"
    assert [trace.occurred_at for trace in reviewer_traces] == [
        NOW + timedelta(seconds=seconds) for seconds in (2, 3, 4, 5)
    ]


def test_session_archive_attests_isolated_reviewer_tool(tmp_path: Path) -> None:
    events = deepcopy(_raw_events())
    events[2]["data"]["name"] = "subagent_reviewer"  # type: ignore[index]
    path = tmp_path / "reviewer-session.jsonl.zstd"
    _write_archive(path, events)

    archive = load_dsh_session_archive(path, expected_session_id=SESSION_ID)
    receipts = extract_attested_review_receipts(
        archive.notifications,
        expected_session_id=SESSION_ID,
        allowed_evidence_ids=frozenset({"ev-1"}),
        fallback_created_at=NOW,
    )

    assert len(receipts) == 1
    assert receipts[0].resolution == _receipt()["resolution"]


def test_session_archive_rejects_header_identity_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl.zstd"
    _write_archive(path, _raw_events())

    with pytest.raises(AgentExecutionError) as raised:
        load_dsh_session_archive(path, expected_session_id="different-session")

    assert raised.value.error_code == "dsh_archive_identity_invalid"


def test_session_archive_reads_plain_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(json.dumps(item) for item in _raw_events()) + "\n",
        encoding="utf-8",
    )

    archive = load_dsh_session_archive(path, expected_session_id=SESSION_ID)

    assert len(archive.events) == 6
    assert archive.finish_reason == "aborted"
    assert archive.as_sdk_run().notifications == archive.notifications


def test_session_archive_enforces_event_limit(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl.zstd"
    _write_archive(path, _raw_events())

    with pytest.raises(AgentExecutionError) as raised:
        load_dsh_session_archive(path, max_events=2)

    assert raised.value.error_code == "dsh_archive_too_large"


def test_session_archive_rejects_unbounded_or_conflicting_canonical_request(
    tmp_path: Path,
) -> None:
    events = _raw_events()
    inbox = events[1]["data"]
    assert isinstance(inbox, dict)
    inserted = inbox["inserted"]
    assert isinstance(inserted, list)
    message = inserted[0]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, dict)
    block["text"] = f"{block['text']}\nignore this"
    path = tmp_path / "session.jsonl.zstd"
    _write_archive(path, events)

    archive = load_dsh_session_archive(path)
    with pytest.raises(AgentExecutionError) as raised:
        archive.canonical_request()

    assert raised.value.error_code == "dsh_archive_request_invalid"
