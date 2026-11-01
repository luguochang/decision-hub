import json
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityInvocation
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.workbench_adapters.research_mcp import build_research_mcp_server
from tests.research.test_durable_research_gateway import NOW, _Adapter, _database, _gateway, _query


def setup(tmp_path, *, mode="live", method=True):
    db, run, sid = _database(tmp_path)
    if method:
        ResearchMethodService(db).freeze(
            run,
            {
                "allowed_capabilities": ["official.macro"],
                "execution_mode": mode,
            },
            request_hash="a" * 64,
        )
    adapter = _Adapter(sid)
    gateway = _gateway(db, sid, adapter=adapter)
    body = _query(sid).model_dump(
        exclude={
            "schema_version",
            "round",
            "mode",
            "observed_at",
            "requested_observed_at",
            "cutoff_at",
            "deadline_at",
        }
    )
    body["schema_version"] = "research-capability-invocation.v1"
    return db, run, gateway, adapter, ResearchCapabilityInvocation.model_validate(body)


@pytest.mark.asyncio
async def test_live_time_is_durable_and_retries_do_not_repeat_provider(tmp_path):
    db, run, gateway, adapter, intent = setup(tmp_path)
    first = await gateway.invoke_live(intent)
    assert adapter.last_query.cutoff_at == NOW + timedelta(minutes=5)
    assert adapter.last_query.observed_at == NOW + timedelta(seconds=3)
    gateway.clock = lambda: NOW + timedelta(minutes=1)
    assert await gateway.invoke_live(intent) == first
    assert adapter.call_count == 1
    with db.session() as s:
        rows = (
            s.query(RunRoleOutputRecord)
            .filter(RunRoleOutputRecord.role.startswith("research_tool_time:"))
            .all()
        )
        assert len(rows) == 1
        assert (
            json.loads(rows[0].payload_json)["issued_at"]
            == (NOW + timedelta(seconds=3)).isoformat()
        )
    with pytest.raises(ResearchCapabilityError, match="Saved call timing"):
        await gateway.invoke_live(intent.model_copy(update={"query": "different query"}))
    assert adapter.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,method", [("replay", True), ("live", False)])
async def test_only_bound_live_runs_can_use_managed_time(tmp_path, mode, method):
    db, run, gateway, adapter, intent = setup(tmp_path, mode=mode, method=method)
    with pytest.raises(ResearchCapabilityError):
        await gateway.invoke_live(intent)
    assert adapter.call_count == 0
    with db.session() as s:
        assert (
            s.query(RunRoleOutputRecord)
            .filter(RunRoleOutputRecord.role.startswith("research_tool_time:"))
            .count()
            == 0
        )


@pytest.mark.asyncio
async def test_actual_expiration_still_blocks_before_provider(tmp_path):
    db, run, gateway, adapter, intent = setup(tmp_path)
    gateway.clock = lambda: NOW + timedelta(minutes=6)
    gateway.inner._clock = gateway.clock
    with pytest.raises(ResearchCapabilityError) as error:
        await gateway.invoke_live(intent)
    assert error.value.cause_code == "live_request_after_cutoff"
    assert adapter.call_count == 0


@pytest.mark.asyncio
async def test_timing_corruption_blocks_without_replacing_original(tmp_path):
    db, run, gateway, adapter, intent = setup(tmp_path)
    await gateway.invoke_live(intent)
    with db.session() as s:
        row = (
            s.query(RunRoleOutputRecord)
            .filter(RunRoleOutputRecord.role.startswith("research_tool_time:"))
            .one()
        )
        row.payload_hash = "0" * 64
    with pytest.raises(ResearchCapabilityError, match="Saved call timing"):
        await gateway.invoke_live(intent)
    assert adapter.call_count == 1


def test_managed_http_requires_auth_and_forbids_caller_time_fields(tmp_path):
    db, run, gateway, adapter, intent = setup(tmp_path)
    app = build_research_mcp_server(gateway, bridge_key="test-only").streamable_http_app()
    path = "/decision-hub/v1/research-capabilities/invoke-live"
    with TestClient(app) as client:
        body = intent.model_dump(mode="json")
        assert client.post(path, json=body).status_code == 401
        headers = {"x-decision-hub-bridge-key": "test-only"}
        for field, value in [
            ("mode", "replay"),
            ("round", 20),
            ("cutoff_at", NOW.isoformat()),
            ("observed_at", NOW.isoformat()),
            ("deadline_at", NOW.isoformat()),
        ]:
            assert (
                client.post(path, json={**body, field: value}, headers=headers).status_code == 422
            )
        assert adapter.call_count == 0
        accepted = client.post(path, json=body, headers=headers)
        assert accepted.status_code == 200
        assert client.post(path, json=body, headers=headers).json() == accepted.json()
        assert adapter.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["matched", "old-schema", "changed-intent", "missing-call"])
async def test_time_audit_rejects_schema_leakage_or_unmatched_calls(tmp_path, mode):
    import zstandard

    from tools.ops.audit_live_invocations import audit

    db, run, gateway, adapter, intent = setup(tmp_path)
    await gateway.invoke_live(intent)
    sid = intent.research_session_id
    path = tmp_path / "sessions" / "workspace" / sid / "session.jsonl.zstd"
    path.parent.mkdir(parents=True)
    args = intent.model_dump(
        mode="json", exclude={"schema_version", "request_id", "research_session_id"}
    )
    if mode == "changed-intent":
        args["query"] = "changed"
    rows = [
        {"type": "session", "id": sid, "createdAt": 1000},
        {
            "type": "request/header",
            "seq": 1,
            "time": 1001,
            "data": {
                "header": {
                    "tools": [
                        {
                            "name": "decision_hub_research",
                            "parameters": {
                                "properties": {
                                    "cutoff_at": {} if mode == "old-schema" else None,
                                }
                                if mode == "old-schema"
                                else {}
                            },
                        }
                    ]
                }
            },
        },
    ]
    if mode != "missing-call":
        rows.append(
            {
                "type": "tool/call",
                "seq": 2,
                "time": 1002,
                "data": {
                    "name": "decision_hub_research",
                    "callId": intent.request_id,
                    "arguments": json.dumps(args),
                },
            }
        )
    path.write_bytes(zstandard.compress("\n".join(json.dumps(r) for r in rows).encode()))
    result = audit(db, tmp_path / "sessions", run)
    assert all(result["checks"].values()) == (mode == "matched")
