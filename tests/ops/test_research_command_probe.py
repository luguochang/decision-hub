from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tools.ops.serve_research_command_probe import REPO, build_app, snapshot, validate_output
from tools.ops.verify_research_command_probe import verify

TOPIC = REPO / "data/product-local/remediation/W6/pc-run-command-recovery/probe"


def wait_for(predicate, label):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"did not observe {label}")


def audit(root: Path, stage: str, *, previous: Path | None = None):
    result = verify(root / "seed.json", stage=stage, previous=previous)
    output = root / "audit" / stage
    output.mkdir(parents=True, exist_ok=False)
    (output / "checks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result["passed"], [row for row in result["checks"] if not row["passed"]]
    return output / "checks.json"


def test_real_worker_cancel_receipt_and_late_retry_are_isolated_and_unique():
    root = TOPIC / f"testclient-main-{uuid4().hex[:8]}"
    app, seed = asyncio.run(
        build_app(root, REPO / "apps/decision-desk/dist", 8075, gate_timeout_seconds=15)
    )
    source = seed["source_run_id"]
    command_path = f"/v1/research/runs/{source}/commands"
    cancel = {
        "schema_version": "research-run-command.v1",
        "request_id": "pc-cancel-fixed",
        "command": "cancel",
        "reason": "PC隔离取消；保留原任务和资料。",
    }
    retry = {
        "schema_version": "research-run-command.v1",
        "request_id": "pc-retry-fixed",
        "command": "retry",
        "reason": "PC隔离重试；不继承原资料授权。",
    }
    headers = {"X-Owner-Id": "owner", "Idempotency-Key": cancel["request_id"]}
    def current():
        return snapshot(Path(seed["database"]), source)

    def runtime():
        return json.loads((root / "runtime-events.json").read_text(encoding="utf-8"))
    with TestClient(app) as client:
        assert "模型调用0" in client.get("/index.html").text
        audit(root, "before")
        (root / "controls/release-worker-start").touch()
        wait_for(lambda: runtime(), "real runtime entry")
        audit(root, "researching")
        assert (
            client.post(
                command_path, json=cancel, headers={"Idempotency-Key": cancel["request_id"]}
            ).status_code
            == 403
        )
        first = client.post(command_path, json=cancel, headers=headers)
        assert first.status_code == 503, first.text
        error = audit(root, "after-cancel-error")
        client.get("/index.html")
        client.get(f"/v1/research/runs/{source}")
        audit(root, "after-cancel-return-no-auto", previous=error)
        receipt_path = command_path + "/" + cancel["request_id"]
        stored = client.get(receipt_path, headers={"X-Owner-Id": "owner"})
        assert stored.status_code == 200, stored.text
        assert stored.json()["result"]["status"] == "accepted"
        assert stored.json()["command"] == cancel
        assert client.get(receipt_path, headers={"X-Owner-Id": "foreign"}).status_code == 404
        assert (
            client.get(command_path + "/missing", headers={"X-Owner-Id": "owner"}).status_code
            == 404
        )
        other_path = f"/v1/research/runs/{seed['reference_run_id']}/commands/{cancel['request_id']}"
        assert client.get(other_path, headers={"X-Owner-Id": "owner"}).status_code == 404
        upload_key = seed["baseline"]["historical"]["non_research_product_commands"][0][
            "request_id"
        ]
        assert (
            client.get(command_path + "/" + upload_key, headers={"X-Owner-Id": "owner"}).status_code
            == 404
        )
        audit(root, "after-cancel-confirm")
        wait_for(
            lambda: any(row["kind"] == "cooperative_cancel" for row in runtime()),
            "cooperative cancellation propagated",
        )
        audit(root, "after-source-cancelled")
        replay = client.post(command_path, json=cancel, headers=headers)
        assert replay.status_code == 200 and replay.json()["status"] == "already_applied"
        changed = client.post(command_path, json={**cancel, "reason": "changed"}, headers=headers)
        assert changed.status_code == 409
        assert client.get(receipt_path, headers={"X-Owner-Id": "owner"}).json() == stored.json()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.post,
                command_path,
                json=retry,
                headers={"X-Owner-Id": "owner", "Idempotency-Key": retry["request_id"]},
            )
            wait_for(lambda: len(current()["receipts"]) == 2, "actual retry commit")
            wait_for(
                lambda: any(
                    row.get("response_held")
                    for row in json.loads((root / "requests.json").read_text(encoding="utf-8"))
                ),
                "held HTTP response",
            )
            held = audit(root, "after-retry-held")
            client.get("/index.html")
            client.get(f"/v1/research/runs/{seed['reference_run_id']}")
            away = audit(root, "after-retry-away-no-auto", previous=held)
            (root / "controls/release-retry-response").touch()
            final_response = future.result(timeout=5)
            assert final_response.status_code == 200
            child = final_response.json()["target_run_id"]
            audit(root, "after-retry-released", previous=away)
        confirmed = client.get(
            command_path + "/" + retry["request_id"], headers={"X-Owner-Id": "owner"}
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["result"] == final_response.json()
        audit(root, "after-retry-confirm")
        wait_for(
            lambda: any(row["run_id"] == child for row in runtime()), "child actual runtime entry"
        )
        (root / "controls/release-child-failure").touch()
        wait_for(
            lambda: (
                next(row for row in current()["runs"] if row["run_id"] == child)["status"]
                == "failed"
            ),
            "child worker durable failure",
        )
        audit(root, "after-child-failure")
        assert client.post("/v1/research/requests", json={}).status_code == 405
        assert (
            client.post(
                command_path, json={**retry, "command": "recheck"}, headers={"X-Owner-Id": "owner"}
            ).status_code
            == 405
        )
        audit(root, "final")
    assert not json.loads((root / "worker-status.json").read_text(encoding="utf-8"))["alive"]


def test_probe_guards_run_before_startup_or_environment_changes():
    for path in (
        REPO / "data/product-local",
        REPO / "data/product-local/remediation",
        REPO / "data/product-local/remediation/W6",
        REPO / "tools/outside-probe",
    ):
        with pytest.raises(ValueError):
            validate_output(path)
    fresh = TOPIC / f"guard-{uuid4().hex[:8]}"
    with pytest.raises(ValueError, match="gate timeout"):
        asyncio.run(
            build_app(fresh, REPO / "apps/decision-desk/dist", 8075, gate_timeout_seconds=0)
        )
    assert not fresh.exists()
