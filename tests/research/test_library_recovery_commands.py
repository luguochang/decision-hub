from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.hub_api.research_library_routes import research_library_router
from packages.contracts_py.decision_hub_contracts import ClaimReviewCommand, DocumentUpload
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.application.research_library_commands import (
    DocumentRecoveryCommand,
    ResearchLibraryCommands,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ProductCommandRecord,
    ResearchDocumentVersionRecord,
)
from packages.provider_adapters.documents import (
    DocumentParserRouter,
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore


def build_library(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'library.sqlite3'}")
    database.create_all()
    store = DocumentBundleStore(tmp_path / "bundles")
    return ResearchLibraryService(
        database,
        store,
        DocumentParserRouter((PypdfTextDocumentParser(store),)),
        FinancialSemanticCorrectionAdapter(store),
        HeuristicAnalystClaimExtractor(),
    )


def upload(library):
    return library.upload(
        DocumentUpload(
            schema_version="document-upload.v1",
            title="Recovery source",
            source_type="private_file",
            market_scopes=["crypto_macro"],
            instrument_scopes=["BTC-USDT"],
            privacy_mode="local_only",
            visibility="private",
        ),
        filename="recovery.txt",
        data="BTC 看多，目标上涨10%。\nBTC 看空，可能下跌。".encode(),
    )


def recovery(version, key="document-recovery:test-command"):
    return DocumentRecoveryCommand(
        schema_version="document-recovery-command.v1",
        request_id=key,
        original_file_hash=version.original_file_hash,
    )


def persisted_version(library, version_id):
    with library.database.session() as session:
        row = session.get(ResearchDocumentVersionRecord, version_id)
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["parse", "correction"])
async def test_failed_processing_recovers_as_linked_version_and_keeps_all_old_fields(
    tmp_path, phase
):
    library = build_library(tmp_path)
    original = upload(library)
    parser, correction = library.parser, library.correction

    class FailedParser:
        def parse(self, _input):
            raise RuntimeError("isolated parser failure")

    class FailedCorrection:
        async def correct(self, *_args, **_kwargs):
            raise RuntimeError("isolated correction failure")

    if phase == "parse":
        library.parser = FailedParser()
    else:
        await library.process_next()
        library.request_correction(original.version_id)
        library.correction = FailedCorrection()
    with pytest.raises(RuntimeError, match="isolated"):
        await library.process_next()
    failed = library.get_version(original.version_id)
    assert failed.status == "failed"
    assert f"document_processing_failed:{phase}:RuntimeError" in failed.quality.issues
    before = persisted_version(library, original.version_id)
    commands = ResearchLibraryCommands(library)
    next_version = commands.recover(failed.version_id, "owner", recovery(failed))
    assert commands.recover(failed.version_id, "owner", recovery(failed)) == next_version
    assert next_version.version_id != failed.version_id
    assert next_version.supersedes_version_id == failed.version_id
    assert next_version.original_file_ref == failed.original_file_ref
    assert next_version.original_file_hash == failed.original_file_hash
    assert next_version.received_at == failed.received_at
    assert next_version.observed_at == failed.observed_at
    assert next_version.parse_receipt_ref is None
    assert next_version.corrected_content_ref is None
    assert next_version.block_count == next_version.claim_count == 0
    assert next_version.created_at >= failed.created_at
    assert persisted_version(library, original.version_id) == before

    library.parser, library.correction = parser, correction
    assert await library.process_next() == next_version.version_id
    parsed = library.get_version(next_version.version_id)
    assert parsed.status == "parsed"
    receipt = json.loads(library.store.read(parsed.parse_receipt_ref))
    assert receipt["version_id"] == next_version.version_id
    assert receipt["original_file_hash"] == original.original_file_hash
    if failed.parse_receipt_ref:
        assert parsed.parse_receipt_ref != failed.parse_receipt_ref
        assert (
            json.loads(library.store.read(failed.parse_receipt_ref))["version_id"]
            == failed.version_id
        )
    library.request_correction(next_version.version_id)
    await library.process_next()
    ready = await library.accept_correction(next_version.version_id)
    assert ready.status == "ready"
    assert library.get_claims(ready.version_id)
    assert persisted_version(library, original.version_id) == before


@pytest.mark.asyncio
async def test_recovery_serializes_duplicates_and_rejects_new_key_or_identity_conflicts(tmp_path):
    library = build_library(tmp_path)
    version = upload(library)
    with library.database.session() as session:
        session.get(ResearchDocumentVersionRecord, version.version_id).status = "failed"
    commands = ResearchLibraryCommands(library)
    payload = recovery(version)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda _: commands.recover(version.version_id, "owner", payload), range(3))
        )
    assert len({result.version_id for result in results}) == 1
    with library.database.session() as session:
        assert session.query(ResearchDocumentVersionRecord).count() == 2
        assert session.query(ProductCommandRecord).filter_by(kind="library_recovery").count() == 1
    with pytest.raises(ValueError, match="product_request_reused"):
        commands.recover(version.version_id, "other-owner", payload)
    with pytest.raises(ValueError, match="product_request_reused"):
        commands.recover(
            version.version_id, "owner", payload.model_copy(update={"original_file_hash": "0" * 64})
        )
    with pytest.raises(ValueError, match="latest_version_changed"):
        commands.recover(
            version.version_id, "owner", recovery(version, "document-recovery:other-key")
        )
    with pytest.raises(ValueError, match="requires_failed_version"):
        commands.recover(
            results[0].version_id, "owner", recovery(results[0], "document-recovery:active-child")
        )


@pytest.mark.asyncio
async def test_recovery_does_not_queue_modified_or_missing_original(tmp_path):
    library = build_library(tmp_path)
    version = upload(library)
    with library.database.session() as session:
        session.get(ResearchDocumentVersionRecord, version.version_id).status = "failed"
    path = library.store.path_for(version.original_file_ref)
    path.write_bytes(b"different bytes")
    commands = ResearchLibraryCommands(library)
    with pytest.raises(ValueError, match="hash_mismatch"):
        commands.recover(version.version_id, "owner", recovery(version))
    path.unlink()
    with pytest.raises(ValueError, match="asset_missing"):
        commands.recover(version.version_id, "owner", recovery(version))
    with library.database.session() as session:
        assert session.query(ResearchDocumentVersionRecord).count() == 1
        assert session.query(ProductCommandRecord).filter_by(kind="library_recovery").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,status", [("accept", "accepted"), ("reject", "rejected")])
async def test_claim_decision_is_audited_and_replay_does_not_overwrite_final_state(
    tmp_path, decision, status
):
    library = build_library(tmp_path)
    version = upload(library)
    await library.process_next()
    library.request_correction(version.version_id)
    await library.process_next()
    await library.accept_correction(version.version_id)
    claim = library.get_claims(version.version_id)[0]
    command = ClaimReviewCommand(
        schema_version="claim-review-command.v1",
        request_id="claim-review:test",
        decision=decision,
        reason="owner reviewed original quote",
    )
    commands = ResearchLibraryCommands(library)
    result = commands.review_claim(claim.claim_id, "owner", command)
    assert result.status == status
    assert result.requires_independent_evidence is True
    assert commands.review_claim(claim.claim_id, "owner", command) == result
    changed = command.model_copy(
        update={"decision": "reject" if decision == "accept" else "accept"}
    )
    with pytest.raises(ValueError, match="product_request_reused"):
        commands.review_claim(claim.claim_id, "owner", changed)
    with pytest.raises(ValueError, match="claim_review_state_changed"):
        commands.review_claim(
            claim.claim_id,
            "owner",
            changed.model_copy(update={"request_id": "claim-review:changed"}),
        )
    assert library.get_claims(version.version_id)[0] == result
    with library.database.session() as session:
        row = session.get(ProductCommandRecord, command.request_id)
        assert row.kind == "claim_review" and row.owner_id == "owner"
        assert row.target_id == claim.claim_id


def test_library_command_api_requires_owner_and_matching_key(tmp_path):
    library = build_library(tmp_path)
    version = upload(library)
    with library.database.session() as session:
        session.get(ResearchDocumentVersionRecord, version.version_id).status = "failed"

    def require_owner(owner):
        if not owner:
            raise HTTPException(403, "owner_identity_required")
        return owner

    app = FastAPI()
    app.include_router(research_library_router(library, require_owner))
    payload = recovery(version)
    path = f"/v1/research-library/versions/{version.version_id}/recover"
    with TestClient(app) as client:
        assert client.post(path, json=payload.model_dump()).status_code == 403
        assert (
            client.post(
                path, json=payload.model_dump(), headers={"X-Owner-Id": "owner"}
            ).status_code
            == 400
        )
        headers = {"X-Owner-Id": "owner", "Idempotency-Key": payload.request_id}
        first = client.post(path, json=payload.model_dump(), headers=headers)
        assert first.status_code == 202
        assert client.post(path, json=payload.model_dump(), headers=headers).json() == first.json()
        assert (
            client.post(
                path, json=payload.model_dump(), headers={**headers, "X-Owner-Id": "other"}
            ).status_code
            == 409
        )
        claim_path = "/v1/research-library/claims/claim_missing/review"
        review = {
            "schema_version": "claim-review-command.v1",
            "request_id": "claim-review:missing",
            "decision": "accept",
            "reason": "review",
        }
        assert client.post(claim_path, json=review).status_code == 403
        assert (
            client.post(claim_path, json=review, headers={"X-Owner-Id": "owner"}).status_code == 400
        )
        assert (
            client.post(
                claim_path,
                json=review,
                headers={"X-Owner-Id": "owner", "Idempotency-Key": review["request_id"]},
            ).status_code
            == 404
        )


def test_existing_library_write_routes_require_owner_before_processing(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", str(tmp_path / "bundles"))
    from apps.hub_api.main import create_app

    library = build_library(tmp_path)
    payload = {
        "schema_version": "document-upload.v1", "title": "Owner check",
        "source_type": "private_file", "market_scopes": ["crypto_macro"],
        "privacy_mode": "local_only", "visibility": "private",
    }
    with TestClient(create_app(library.database, source_connectors=[])) as client:
        requests = [
            ("/v1/research-library/documents", {
                "files": {"file": ("source.txt", b"BTC bullish source", "text/plain")},
                "data": {"metadata": json.dumps(payload)},
            }),
            ("/v1/research-library/web-import", {
                "data": {"receipt_id": "missing", "metadata": json.dumps(payload)},
            }),
            ("/v1/research-library/versions/missing/correction", {}),
            ("/v1/research-library/versions/missing/correction/accept", {}),
        ]
        for path, kwargs in requests:
            response = client.post(path, **kwargs)
            assert response.status_code == 403
            assert response.json()["detail"] == "owner_identity_required"
        authorized = client.post(
            requests[1][0], **requests[1][1], headers={"X-Owner-Id": "owner"},
        )
        assert authorized.status_code == 409
        assert authorized.json()["detail"] == "acquisition_artifact_root_not_configured"
    with library.database.session() as session:
        assert session.query(ResearchDocumentVersionRecord).count() == 0
