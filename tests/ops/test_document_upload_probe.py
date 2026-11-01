from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tools.ops.serve_document_recovery_probe import (
    REPO,
    UploadProbeOptions,
    build_app,
    database_snapshot,
    document_ticks,
    validate_output,
)
from tools.ops.verify_document_upload_probe import verify

TOPIC = REPO / "data/product-local/remediation/W6/pc-upload-recovery/probe"
MANIFEST = REPO / "data/product-local/remediation/W6/pc-upload-recovery/fixtures-02/manifest.json"


def fixture(name: str) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return next(row for row in manifest["files"] if Path(row["path"]).name == name)


def new_app(name: str, options: UploadProbeOptions):
    root = TOPIC / f"testclient-{name}-{uuid4().hex[:8]}"
    app, seed = asyncio.run(build_app(root, REPO / "apps/decision-desk/dist", 8074, options))
    return root, app, seed


def upload(client, name: str, key: str, *, title: str | None = None, owner: str | None = "owner"):
    item = fixture(name)
    intent = {
        "schema_version": "document-upload-intent.v1", "owner_id": "owner",
        "file_name": name, "file_size_bytes": item["bytes"], "file_sha256": item["sha256"],
        "metadata": {
            "schema_version": "document-upload.v1", "title": title or f"PC隔离验收 {name}",
            "source_type": "private_file", "market_scopes": ["crypto_macro"],
            "instrument_scopes": ["BTC-USDT"], "privacy_mode": "local_only",
            "visibility": "private", "notes": "非真实市场资料，仅核对PC上传回执。",
        },
    }
    headers = {"Idempotency-Key": key}
    if owner:
        headers["X-Owner-Id"] = owner
    with Path(item["path"]).open("rb") as source:
        return client.post("/v1/research-library/uploads", files={"file": (name, source)},
                           data={"intent": json.dumps(intent)}, headers=headers)


def wait_status(client, version: str, status: str):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/v1/research-library/versions/{version}")
        assert response.status_code == 200, response.text
        if response.json()["status"] == status:
            return response.json()
        time.sleep(0.05)
    raise AssertionError(f"version {version} did not reach {status}")


def audit(root: Path, *, stage: str, client, **kwargs):
    result = verify(root / "seed.json", MANIFEST, stage=stage, client=client, **kwargs)
    output = root / "audit" / stage
    output.mkdir(parents=True, exist_ok=False)
    (output / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    assert result["passed"], [row for row in result["checks"] if not row["passed"]]
    return output / "checks.json"


def test_commit503_receipt_restore_states_bad_pdf_then_real_pdf():
    digest = fixture("pc-readable-two-pages.pdf")["sha256"]
    late_digest = fixture("pc-corrupt-nine-bytes.pdf")["sha256"]
    root, app, seed = new_app("receipt", UploadProbeOptions(digest, True, True, late_digest, 15))
    key = "upload:pc-fixed-receipt"
    with TestClient(app) as client:
        policy = client.get("/v1/research-library/upload-policy")
        assert policy.status_code == 200 and policy.json()["max_file_size_bytes"] == 20971520
        audit(root, stage="before", client=client, expected_documents=0, expected_receipts=0)
        unauth = upload(client, "pc-readable-two-pages.pdf", key, owner=None)
        assert unauth.status_code == 403
        first = upload(client, "pc-readable-two-pages.pdf", key)
        assert first.status_code == 503, first.text
        error = audit(root, stage="after-upload-error", client=client, expected_status="received",
                      expected_documents=1, expected_receipts=1, http=True)
        client.get("/index.html")
        client.get("/v1/research-library/documents")
        audit(root, stage="after-return-no-auto", client=client, previous=error, no_auto_post=True,
              expected_status="received", expected_documents=1, expected_receipts=1)
        restored = client.get("/v1/research-library/uploads/" + key,
                              headers={"X-Owner-Id": "owner"})
        assert restored.status_code == 200, restored.text
        version = restored.json()["version"]["version_id"]
        retry = upload(client, "pc-readable-two-pages.pdf", key)
        assert retry.status_code == 202 and retry.json() == restored.json()
        conflict = upload(client, "pc-readable-two-pages.pdf", key, title="changed metadata")
        assert conflict.status_code == 409 and conflict.json()["detail"] == "product_request_reused"
        audit(root, stage="after-upload-confirm", client=client, require_confirmed=True,
              expected_status="received", expected_documents=1, expected_receipts=1, http=True)
        (root / "controls/release-parse-start").touch()
        wait_status(client, version, "parsing")
        audit(root, stage="parsing", client=client, require_confirmed=True,
              expected_status="parsing", expected_documents=1, expected_receipts=1)
        (root / "controls/release-parse-finish").touch()
        parsed = wait_status(client, version, "parsed")
        assert parsed["block_count"] == 2
        audit(root, stage="after-processing", client=client, require_confirmed=True,
              expected_status="parsed", expected_documents=1, expected_receipts=1, http=True)
        (root / "controls/release-upload-response").touch()
        for index, name in enumerate(("pc-corrupt-nine-bytes.pdf", "pc-truncated-64-bytes.pdf")):
            bad = upload(client, name, f"upload:bad-{index}")
            assert bad.status_code == 202, bad.text
            failed = wait_status(client, bad.json()["version"]["version_id"], "failed")
            assert failed["parse_receipt_ref"] is None
        after = upload(client, "pc-readable-two-pages.pdf", "upload:after-bad",
                       title="PC隔离坏PDF之后的合法资料")
        assert after.status_code == 202, after.text
        assert after.json()["version"]["version_id"] != version
        wait_status(client, after.json()["version"]["version_id"], "parsed")
        denied = client.post("/v1/research/library", json={})
        assert denied.status_code == 405
        wrong_owner = client.get("/v1/research-library/uploads/" + key,
                                 headers={"X-Owner-Id": "other"})
        assert wrong_owner.status_code == 404
        audit(root, stage="final", client=client, require_confirmed=True,
              expected_documents=4, expected_receipts=4, require_worker_continued=True, http=True)
        requests = json.loads((root / "requests.json").read_text(encoding="utf-8"))
        assert len([row for row in requests if row.get("committed_response_withheld")]) == 1
        held = [row for row in requests if row.get("response_gate_started_at")]
        assert len(held) == 1 and held[0]["receipt"]["intent"]["file_sha256"] == late_digest
    assert database_snapshot(Path(seed["database"]))["counts"]["runs"] == 0


def test_exact_limit_boundaries_empty_rejected_without_rows_or_originals():
    root, app, seed = new_app("limits", UploadProbeOptions())
    with TestClient(app) as client:
        for name, status in (("pc-empty.txt", 400), ("pc-limit-plus-one.txt", 413)):
            rejected = upload(client, name, f"upload:{name}")
            assert rejected.status_code == status, rejected.text
            snapshot = database_snapshot(Path(seed["database"]))
            assert snapshot["counts"]["research_documents"] == 0
            assert snapshot["counts"]["product_commands"] == 0
            assert not list((root / "runtime/document-bundles").glob("*/original*"))
        for name in ("pc-limit-minus-one.txt", "pc-limit.txt"):
            response = upload(client, name, f"upload:{name}")
            assert response.status_code == 202, response.text
            row = wait_status(client, response.json()["version"]["version_id"], "parsed")
            assert row["block_count"] == 321
        audit(root, stage="boundaries-final", client=client, expected_documents=2,
              expected_receipts=2, http=True)


def test_delayed_corrupt_commit_response_then_worker_still_parses_following_pdf():
    digest = fixture("pc-corrupt-nine-bytes.pdf")["sha256"]
    root, app, _seed = new_app("late", UploadProbeOptions(hold_response_sha256=digest,
                                                         gate_timeout_seconds=15))
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(upload, client, "pc-corrupt-nine-bytes.pdf", "upload:late-fixed")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            journal = json.loads((root / "requests.json").read_text(encoding="utf-8"))
            if journal and journal[0].get("response_gate_started_at"):
                break
            time.sleep(0.05)
        assert journal[0]["application_status"] == 202
        assert journal[0]["delivered_status"] is None and not pending.done()
        version = journal[0]["receipt"]["version"]["version_id"]
        wait_status(client, version, "failed")
        before = audit(root, stage="late-pending", client=client,
                       expected_documents=1, expected_receipts=1, http=True)
        client.get("/index.html")
        audit(root, stage="late-navigation-no-auto", client=client, previous=before,
              no_auto_post=True, expected_documents=1, expected_receipts=1)
        (root / "controls/release-upload-response").touch()
        assert pending.result(timeout=5).status_code == 202
        audit(root, stage="late-completed-no-auto", client=client, previous=before,
              no_auto_post=True, expected_documents=1, expected_receipts=1)
        after = upload(client, "pc-readable-two-pages.pdf", "upload:following-late-bad")
        assert after.status_code == 202, after.text
        wait_status(client, after.json()["version"]["version_id"], "parsed")
        audit(root, stage="late-final", client=client, expected_documents=2,
              expected_receipts=2, require_worker_continued=True, http=True)


def test_probe_guard_and_cancellation_do_not_start_extra_processing():
    with pytest.raises(ValueError):
        validate_output(REPO / "data/decision-hub")
    root = validate_output(TOPIC / f"testclient-cancel-{uuid4().hex[:8]}")
    root.mkdir(parents=True)

    class CancelWorker:
        async def tick(self):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(document_ticks(CancelWorker(), root))
    assert json.loads((root / "worker-events.json").read_text(encoding="utf-8")) == []
