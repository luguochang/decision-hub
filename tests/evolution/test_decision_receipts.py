from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import (
    ActivePointerRecord,
    CandidateVersionRecord,
    Database,
    PromotionDecisionRecord,
)

PACK = "crypto_macro.v1"
HEADERS = {"X-Owner-Id": "owner"}
type ReceiptContext = tuple[Database, TestClient, dict[str, object]]


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ReceiptContext]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'receipts.sqlite3'}")
    client = TestClient(create_app(db, sources_enabled=False, source_connectors=[]))
    for target in ("challenger", "other"):
        # A real checked candidate artifact is necessary to reach the promotion
        # evidence gate; missing artifacts are rejected earlier by the Kernel.
        artifact = {"schema_version": "candidate-artifact.v1", "proposal": {
            "proposal_id": target, "candidate_type": "strategy", "version": f"{target}.v1",
            "parent_version": None, "summary": "Isolated checked rule",
            "changes": ["Check counter evidence"],
            "rationale": "Receipt boundary test", "evidence_refs": [],
            "execution_scope": "directional_synthesis",
            "execution_rules": ["examine_counter_evidence"],
        }}
        body = json.dumps(artifact).encode()
        artifact_path = tmp_path / f"{target}.json"
        artifact_path.write_bytes(body)
        response = client.post("/v1/evolution/candidates", headers=HEADERS, json={
            "execution_scope": "directional_synthesis", "candidate_id": target,
            "candidate_type": "strategy", "content_hash": hashlib.sha256(body).hexdigest(),
            "version": f"{target}.v1", "parent_version": None, "status": "candidate",
            "created_at": "2026-10-01T00:00:00Z",
            "content_ref": str(artifact_path), "source": "owner",
        })
        assert response.status_code == 200
    request: dict[str, object] = {
        "request_id": "evolution-decision:original", "candidate_id": "challenger",
        "owner": "owner", "decision": "reject", "reason": "Evidence is insufficient",
        "evaluation_refs": [], "expected_generation": 0,
    }
    yield db, client, request
    client.close()
    db.engine.dispose()


def test_committed_reject_receipt_survives_lost_response_and_pointer_change(
    context: ReceiptContext,
) -> None:
    db, client, payload = context
    post = f"/v1/evolution/{PACK}/decisions"
    receipt = client.post(post, headers=HEADERS, json=payload)
    assert receipt.status_code == 200
    fresh_key = client.post(post, headers=HEADERS, json={**payload, "request_id": "new-key"})
    assert fresh_key.status_code == 409
    assert fresh_key.json()["detail"] == "candidate_not_rejectable"
    # The client loses this body. Other work subsequently advances the pointer.
    with db.session() as session:
        other = session.get(CandidateVersionRecord, "other")
        assert other is not None
        other.status = "active"
        session.add(ActivePointerRecord(
            pointer_id=f"pointer:{PACK}", domain_pack_ref=PACK, candidate_id="other",
            generation=7, updated_at=datetime.now(UTC),
        ))
    retrieved = client.get(f"{post}/{payload['request_id']}", headers=HEADERS)
    assert retrieved.status_code == 200
    assert retrieved.json() == receipt.json()
    assert retrieved.json()["decision"]["resulting_generation"] == 0
    assert retrieved.json()["active_pointer"] is None
    replay = client.post(post, headers=HEADERS, json=payload)
    assert replay.status_code == 200
    assert replay.json() == receipt.json()
    wrong_retry = client.post(post, headers=HEADERS, json={**payload, "request_id": "new-key"})
    assert wrong_retry.status_code == 409
    with db.session() as session:
        assert session.query(PromotionDecisionRecord).count() == 1
        candidate = session.get(CandidateVersionRecord, "challenger")
        assert candidate is not None and candidate.status == "rejected"
        assert session.query(ActivePointerRecord).one().generation == 7


def test_receipt_lookup_is_owner_and_domain_scoped_and_read_only(
    context: ReceiptContext,
) -> None:
    db, client, payload = context
    post = f"/v1/evolution/{PACK}/decisions"
    assert client.post(post, headers=HEADERS, json=payload).status_code == 200
    path = f"{post}/{payload['request_id']}"
    assert client.get(path).status_code == 403
    for target, headers in (
        (path, {"X-Owner-Id": "other-owner"}),
        (f"/v1/evolution/other-pack/decisions/{payload['request_id']}", HEADERS),
        (f"{post}/missing", HEADERS),
    ):
        result = client.get(target, headers=headers)
        assert result.status_code == 404
        assert result.json()["detail"] == "promotion_decision_not_found"
    with db.session() as session:
        assert session.query(PromotionDecisionRecord).count() == 1
        assert session.query(ActivePointerRecord).count() == 0


def test_exact_receipt_does_not_depend_on_recent_decision_list(context: ReceiptContext) -> None:
    _, client, payload = context
    post = f"/v1/evolution/{PACK}/decisions"
    original = client.post(post, headers=HEADERS, json=payload)
    assert original.status_code == 200
    second = client.post(post, headers=HEADERS, json={
        **payload, "request_id": "newer", "candidate_id": "other",
    })
    assert second.status_code == 200
    assert client.get("/v1/evolution/decisions?limit=1").json()[0]["request_id"] == "newer"
    assert client.get(f"{post}/{payload['request_id']}", headers=HEADERS).json() == original.json()


def test_application_gate_refusal_rolls_back_and_has_no_success_receipt(
    context: ReceiptContext,
) -> None:
    db, client, payload = context
    post = f"/v1/evolution/{PACK}/decisions"
    request = {**payload, "decision": "promote"}
    response = client.post(post, headers=HEADERS, json=request)
    assert response.status_code == 409
    assert response.json()["detail"] == "promotion_gate_failed"
    lookup = client.get(f"{post}/{payload['request_id']}", headers=HEADERS)
    assert lookup.status_code == 404
    with db.session() as session:
        assert session.query(PromotionDecisionRecord).count() == 0
        assert session.query(ActivePointerRecord).count() == 0
        candidate = session.get(CandidateVersionRecord, "challenger")
        assert candidate is not None and candidate.status == "candidate"


def test_changed_payload_cannot_reuse_an_acknowledged_request(context: ReceiptContext) -> None:
    db, client, payload = context
    post = f"/v1/evolution/{PACK}/decisions"
    original = client.post(post, headers=HEADERS, json=payload)
    assert original.status_code == 200
    changed = client.post(post, headers=HEADERS, json={**payload, "reason": "changed reason"})
    assert changed.status_code == 404
    assert changed.json()["detail"] == "promotion_request_reused"
    assert client.get(f"{post}/{payload['request_id']}", headers=HEADERS).json() == original.json()
    with db.session() as session:
        assert session.query(PromotionDecisionRecord).count() == 1
