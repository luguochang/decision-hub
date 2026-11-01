import json

import zstandard

from packages.query_views.usage_tools import tool_ledger
from tests.research.test_usage_view import archive, setup


def call(identity, *, time=1001):
    return {
        "type": "tool/call",
        "time": time,
        "data": {
            "callId": identity,
            "name": "decision_hub_research",
            "arguments": {"private": "SECRET INPUT"},
        },
    }


def result(identity, failed=False, body="SECRET OUTPUT", *, time=1002):
    return {
        "type": "tool/result",
        "time": time,
        "data": {
            "message": {
                "content": [
                    {
                        "toolCallId": identity,
                        "isError": failed,
                        "content": body,
                    }
                ]
            }
        },
    }


def test_nested_host_validation_error_missing_result_and_duplicates():
    view = tool_ledger(
        [
            call("first"),
            result(
                "first", True, [{"type": "text", "text": "Error: invalid arguments: SECRET INPUT"}]
            ),
            call("second"),
            call("second"),
            result("second"),
            call("third"),
            result("orphan"),
        ]
    )
    assert [item["state"] for item in view["calls"]] == ["failed", "succeeded", "incomplete"]
    assert view["calls"][0]["failure_kind"] == "argument_validation"
    assert view["issues"] == ["duplicate_tool_call", "tool_result_without_call"]
    assert "SECRET" not in json.dumps(view)


def test_missing_error_flag_and_conflicting_results_are_not_success():
    missing = result("missing")
    del missing["data"]["message"]["content"][0]["isError"]
    view = tool_ledger(
        [call("conflict"), result("conflict"), result("conflict", True), call("missing"), missing]
    )
    assert all(item["state"] == "incomplete" for item in view["calls"])
    assert view["issues"] == ["conflicting_tool_results", "tool_result_state_missing"]


def test_host_attempts_are_saved_without_secrets_or_copied_parent_events(tmp_path, monkeypatch):
    home, client, run_id = setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    archive(home, "child", "main-session")
    for sid in ("main-session", "child"):
        target = home / "sessions" / "workspace" / sid / "session.jsonl.zstd"
        with target.open("ab") as stream:
            for event in [
                call("copied", time=500),
                result("copied", time=501),
                call("same-id"),
                result("same-id", failed=sid == "main-session"),
            ]:
                stream.write(
                    zstandard.ZstdCompressor().compress(json.dumps(event).encode() + b"\n")
                )
    view = client.get(f"/v1/research/runs/{run_id}/usage").json()
    assert [len(item["tool_attempts"]) for item in view["sessions"]] == [1, 1]
    assert {item["tool_attempts"][0]["state"] for item in view["sessions"]} == {
        "failed",
        "succeeded",
    }
    url = f"/v1/research/runs/{run_id}/usage-snapshots"
    saved = client.post(url, json={"request_id": "tools-proof"}).json()
    assert "SECRET" not in json.dumps(saved)
    assert saved["accounting_version"] == "dsh-call-and-tool-metadata.v3"
    for target in home.glob("sessions/*/*/session.jsonl.zstd"):
        target.unlink()
    assert client.get(url).json()["items"][0] == saved
    unavailable = client.get(f"/v1/research/runs/{run_id}/usage").json()
    assert unavailable["status"] == "unavailable" and unavailable["sessions"] == []
