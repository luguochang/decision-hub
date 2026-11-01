import json
import sqlite3

import zstandard
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord
from tests.evolution.test_research_worker import NOW, _admitted_run


def archive(home, identity, parent=None, tokens=20, *, missing=False, inherited=False):
    events = [{"type": "session", "id": identity, "createdAt": 1000, "parentSession": parent}]
    if inherited:
        events.append(
            {
                "type": "assistant/message",
                "time": 500,
                "data": {
                    "turn": 99,
                    "step": 1,
                    "usage": {"inputTokens": 999, "outputTokens": 1, "totalTokens": 1000},
                },
            }
        )
    events += [
        {"type": "request/context", "time": 1000,
         "data": {"provider": "fixture", "model": "test-model"}},
        {"type": "turn/start", "time": 1001, "data": {"turn": 1}},
        {"type": "step/start", "time": 1002, "data": {"turn": 1, "step": 1}},
        {
            "type": "assistant/message",
            "time": 1003,
            "data": {
                "turn": 1,
                "step": 1,
                "message": "PRIVATE CONTENT MUST NOT BE RETURNED",
                **(
                    {}
                    if missing
                    else {
                        "usage": {
                            "inputTokens": tokens - 3,
                            "outputTokens": 3,
                            "totalTokens": tokens,
                        }
                    }
                ),
            },
        },
        {"type": "step/end", "time": 1004, "data": {"turn": 1, "step": 1}},
        {"type": "turn/end", "time": 1005, "data": {"turn": 1}},
    ]
    target = home / "sessions" / "workspace" / identity / "session.jsonl.zstd"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Real DSH appends separately compressed frames, not one monolithic frame.
    target.write_bytes(
        b"".join(
            zstandard.ZstdCompressor().compress(json.dumps(event).encode() + b"\n")
            for event in events
        )
    )


def setup(tmp_path, monkeypatch):
    db, _, run_id = _admitted_run(tmp_path)
    with db.session() as session:
        session.add(
            DshSessionLinkRecord(
                run_id=run_id,
                dsh_session_id="main-session",
                request_hash="a" * 64,
                state="completed",
                generation=1,
                upstream_identity_json="{}",
                plugin_build_hash="b" * 64,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    home = tmp_path / "dsh"
    monkeypatch.setenv("DECISION_HUB_DSH_HOME", str(home))
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path))
    return home, TestClient(app), run_id


def test_usage_api_tracks_only_related_sessions_and_never_returns_messages(tmp_path, monkeypatch):
    home, client, run_id = setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    archive(home, "child", "main-session", 30, inherited=True)
    archive(home, "grandchild", "child", 40)
    archive(home, "unrelated", None, 500)
    archive(home, "other-child", "unrelated", 1000)
    response = client.get(f"/v1/research/runs/{run_id}/usage")
    assert response.status_code == 200
    assert "PRIVATE CONTENT" not in response.text
    view = response.json()
    assert {item["session_id"]: item["recorded_tokens"] for item in view["sessions"]} == {
        "main-session": 20,
        "child": 30,
        "grandchild": 40,
    }
    assert view["total_run_tokens"] is None and view["estimated_cost_usd"] is None
    assert view["status"] == "partial"


def test_missing_archives_and_missing_usage_do_not_become_zero_cost(tmp_path, monkeypatch):
    home, client, run_id = setup(tmp_path, monkeypatch)
    assert client.get(f"/v1/research/runs/{run_id}/usage").json()["status"] == "unavailable"
    archive(home, "main-session", missing=True)
    view = client.get(f"/v1/research/runs/{run_id}/usage").json()
    assert view["sessions"][0]["status"] == "partial"
    assert view["sessions"][0]["usage_samples"] == 0
    assert view["estimated_cost_usd"] is None
    assert client.get("/v1/research/runs/absent/usage").status_code == 404


def test_usage_snapshots_survive_archive_loss_and_retries_do_not_overwrite(tmp_path, monkeypatch):
    home, client, run_id = setup(tmp_path, monkeypatch)
    url = f"/v1/research/runs/{run_id}/usage-snapshots"
    assert client.get(url).json()["total"] == 0
    archive(home, "main-session")
    first = client.post(url, json={"request_id": "check-1"})
    assert first.status_code == 200
    saved = first.json()
    assert saved["session_generation"] == 1
    assert saved["view"]["sessions"][0]["recorded_tokens"] == 20
    assert saved["recorded_at"].endswith("Z")
    assert "PRIVATE CONTENT" not in first.text and str(home) not in first.text
    archive(home, "main-session", tokens=35)
    assert client.post(url, json={"request_id": "check-1"}).json() == saved
    second = client.post(url, json={"request_id": "check-2"}).json()
    assert second["view"]["sessions"][0]["recorded_tokens"] == 35
    assert second["payload_sha256"] != saved["payload_sha256"]
    (home / "sessions" / "workspace" / "main-session" / "session.jsonl.zstd").unlink()
    missing = client.post(url, json={"request_id": "check-3"}).json()
    assert missing["view"]["status"] == "unavailable"
    assert missing["view"]["estimated_cost_usd"] is None
    page = client.get(url, params={"limit": 1, "offset": 2}).json()
    assert page["total"] == 3 and page["items"] == [saved]
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        rows = db.execute("SELECT payload_json FROM research_usage_snapshots").fetchall()
        assert len(rows) == 3
        assert all("PRIVATE CONTENT" not in row[0] for row in rows)
    # New API instance and database connection read only the durable receipt.
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    app = FastAPI()
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'worker.sqlite3'}")
    app.include_router(asset_router(database, tmp_path))
    assert TestClient(app).get(url, params={"offset": 2}).json()["items"] == [saved]
    assert client.get(url, params={"offset": -1}).status_code == 422
    assert client.post(url, json={"request_id": ""}).status_code == 422
    assert client.get("/v1/research/runs/absent/usage-snapshots").status_code == 404


def test_tampered_usage_receipt_is_not_returned_as_valid(tmp_path, monkeypatch):
    home, client, run_id = setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    url = f"/v1/research/runs/{run_id}/usage-snapshots"
    client.post(url, json={"request_id": "check-1"}).raise_for_status()
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        db.execute("UPDATE research_usage_snapshots SET payload_sha256=?", ("f" * 64,))
    assert client.get(url).status_code == 409
    assert client.post(url, json={"request_id": "check-1"}).status_code == 409


def test_legacy_receipt_is_read_without_inventing_detail_or_changing_hash(tmp_path, monkeypatch):
    import hashlib

    from packages.workbench_adapters.usage_ledger import _canonical

    home, client, run_id = setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    url = f"/v1/research/runs/{run_id}/usage-snapshots"
    saved = client.post(url, json={"request_id": "legacy"}).json()
    assert len(saved["view"]["sessions"][0]["calls"]) == 1
    saved["view"]["sessions"][0].pop("calls")
    saved["accounting_version"] = "dsh-final-messages.v1"
    saved.pop("payload_sha256")
    encoded = _canonical(saved)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        db.execute("UPDATE research_usage_snapshots SET payload_json=?,payload_sha256=?",
                   (encoded, digest))
    expected = {**saved, "payload_sha256": digest}
    assert client.get(url).json()["items"] == [expected]
    assert client.post(url, json={"request_id": "legacy"}).json() == expected
