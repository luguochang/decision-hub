"""Registered native tools with synthetic independent backends; no financial permission."""

import hashlib
import json
import os
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
    review_originals_from_prompt,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
    extract_attested_perpetual_synthesis_captures,
    preparation_for_capture,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run

ROOT = Path(__file__).resolve().parents[2]


def test_registered_negative_revision_new_child_and_exact_python_history(tmp_path):
    output = tmp_path / "revision-transcript.json"
    env = dict(os.environ, D2_REVISION_OUTPUT=str(output))
    result = subprocess.run(
        [
            str(ROOT / "apps/decision-desk/node_modules/.bin/vitest.cmd"),
            "run",
            "tests/native-v2.spec.ts",
            "--environment",
            "node",
            "-t",
            "revises a genuinely negative",
        ],
        cwd=ROOT / "extensions/dsh/decision-hub",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(output.read_text("utf-8"))
    context = value["context"]
    request = ResearchSessionRequest.model_validate(context["request"])

    def notifications(actual):
        events = [
            dict(
                method="session.event",
                payload=dict(sessionId=context["binding"]["sessionId"], event=e),
            )
            for e in actual["context"]["events"]
        ]
        text = json.dumps(events)
        times = [e["time"] for e in actual["context"]["events"] if "time" in e]
        payload = DshSessionResult(
            schema_version="dsh-session-result.v1",
            run_id=request.run_id,
            dsh_session_id=context["binding"]["sessionId"],
            generation=1,
            last_seq=actual["context"]["events"][-1]["seq"],
            final_response="",
            finish_reason="completed",
            events_json=text,
            started_at=datetime.fromtimestamp(min(times) / 1000, tz=UTC),
            finished_at=datetime.fromtimestamp(max(times) / 1000, tz=UTC),
            trace_ref="synthetic://independent-revision-tools",
            result_hash=hashlib.sha256(("\0" + text).encode()).hexdigest(),
        )
        return _sdk_run(payload).notifications

    captures = extract_attested_perpetual_synthesis_captures(
        notifications(value), expected_session_id=context["binding"]["sessionId"], request=request
    )
    assert len(captures) == 1 and captures[0].call_id == "f2" and captures[0].review_call_id == "r2"
    assert captures[0].authorization.verdict == "approved"
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        _review_originals,
    )

    capture = captures[0]
    originals = _review_originals(
        SimpleNamespace(
            synthesis=capture.synthesis,
            review_request=capture.review_request,
            authorization=capture.authorization,
            review_wrapper_json=capture.review_wrapper_json,
            raw_candidate_hash=capture.raw_candidate_hash,
        )
    )
    assert json.loads(originals.authorization_json)["verdict"] == "approved"
    proof = preparation_for_capture(
        notifications(value),
        expected_session_id=context["binding"]["sessionId"],
        call_id="f2",
        catalog=review_originals_from_prompt(value["reviews"][1]["actual_prompt"]),
        request=request,
    )
    assert [a["call_id"] for a in proof["attempts"]] == ["p1", "p2"]
    assert [a["authorization"].verdict for a in proof["attempts"]] == ["needs_revision", "approved"]
    for attack in ("old_handle", "draft_substitution", "deleted_negative", "reused_child"):
        changed = deepcopy(value)
        events = changed["context"]["events"]
        if attack == "old_handle":
            next(e for e in events if e["data"].get("callId") == "f2" and e["type"] == "tool/call")[
                "data"
            ]["arguments"]["prepared_call_id"] = "p1"
        elif attack == "draft_substitution":
            next(e for e in events if e["data"].get("callId") == "p1" and e["type"] == "tool/call")[
                "data"
            ]["arguments"]["draft"]["analysis"]["thesis"] += " Unreviewed alteration."
        elif attack == "deleted_negative":
            changed["context"]["events"] = [e for e in events if e["data"].get("callId") != "r1"]
        else:
            wrapper = next(
                e for e in events if e["data"].get("callId") == "r2" and e["type"] == "tool/result"
            )["data"]["value"]
            wrapper["child_session_id"] = "revision-child-1"
            wrapper["original_output"]["runId"] = "revision-child-1"
        with pytest.raises(AgentExecutionError):
            extract_attested_perpetual_synthesis_captures(
                notifications(changed),
                expected_session_id=context["binding"]["sessionId"],
                request=request,
            )
    (ROOT / "artifacts/d2-perpetual-review-revision/verified-transcript.json").write_text(
        json.dumps(value, indent=2), encoding="utf-8"
    )
