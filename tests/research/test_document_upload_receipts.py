from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from pypdf.errors import PdfReadError

from apps.hub_api.document_upload_limit import MAX_DOCUMENT_REQUEST_BYTES
from apps.hub_api.document_uploads import read_document_upload
from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts import (
    DocumentUpload,
    DocumentUploadIntent,
    DocumentUploadReceipt,
    ResearchDocumentVersion,
)
from packages.kernel.decision_hub_kernel.application.research_library import (
    MAX_DOCUMENT_FILE_SIZE_BYTES,
    ResearchLibraryService,
)
from packages.kernel.decision_hub_kernel.application.research_library_commands import (
    DocumentRecoveryCommand,
    ResearchLibraryCommands,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ProductCommandRecord,
    ResearchDocumentRecord,
    ResearchDocumentVersionRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.documents import DocumentParserPort
from packages.provider_adapters.documents import (
    DocumentParserRouter,
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore

type ApiFixture = tuple[TestClient, ResearchLibraryService]


def metadata(**changes: Any) -> DocumentUpload:
    return DocumentUpload.model_validate(
        {
            "schema_version": "document-upload.v1",
            "title": "完整上传意图",
            "source_type": "private_file",
            "market_scopes": ["crypto_macro"],
            "instrument_scopes": ["BTC-USDT"],
            "privacy_mode": "local_only",
            "visibility": "private",
            "notes": "最初备注",
            **changes,
        }
    )


def intent(
    data: bytes = b"immutable upload source",
    *,
    owner: str = "owner-a",
    name: str = "source.txt",
    **changes: Any,
) -> DocumentUploadIntent:
    return DocumentUploadIntent(
        schema_version="document-upload-intent.v1",
        owner_id=owner,
        file_name=name,
        file_size_bytes=len(data),
        file_sha256=hashlib.sha256(data).hexdigest(),
        metadata=metadata(**changes),
    )


@pytest.fixture
def library(tmp_path: Path) -> ResearchLibraryService:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'upload.sqlite3'}")
    database.create_all()
    store = DocumentBundleStore(tmp_path / "bundles")
    return ResearchLibraryService(
        database,
        store,
        cast(DocumentParserPort, DocumentParserRouter((PypdfTextDocumentParser(store),))),
        FinancialSemanticCorrectionAdapter(store),
        HeuristicAnalystClaimExtractor(),
    )


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ApiFixture]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "api-data"))
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", str(tmp_path / "api-bundles"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    app = create_app(db, source_connectors=[], sources_enabled=False)
    # No lifespan, worker, provider, or service is started by this isolated client.
    client = TestClient(app)
    yield client, cast(ResearchLibraryService, app.state.research_library)
    client.close()


def counts(library: ResearchLibraryService) -> tuple[int, int, int]:
    with library.database.session() as session:
        return (
            session.query(ResearchDocumentRecord).count(),
            session.query(ResearchDocumentVersionRecord).count(),
            session.query(ProductCommandRecord).filter_by(kind="library_upload").count(),
        )


def submit(
    commands: ResearchLibraryCommands,
    payload: DocumentUploadIntent,
    key: str = "upload-request-1",
    data: bytes = b"immutable upload source",
) -> tuple[DocumentUploadReceipt, bool]:
    return commands.upload(
        payload.owner_id,
        key,
        payload,
        filename=payload.file_name,
        data=data,
    )


def post(
    client: TestClient,
    payload: DocumentUploadIntent,
    *,
    key: str = "upload-request-1",
    data: bytes = b"immutable upload source",
    **kwargs: Any,
):
    return client.post(
        "/v1/research-library/uploads",
        data={"intent": payload.model_dump_json()},
        files={"file": (payload.file_name, data, "application/octet-stream")},
        headers={"X-Owner-Id": payload.owner_id, "Idempotency-Key": key},
        **kwargs,
    )


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_service_actual_capacity_boundaries_before_archive(
    library: ResearchLibraryService, delta: int
) -> None:
    data = b"x" * (MAX_DOCUMENT_FILE_SIZE_BYTES + delta)
    payload = intent(data)
    commands = ResearchLibraryCommands(library)
    if delta > 0:
        with pytest.raises(ValueError, match="document_file_too_large"):
            submit(commands, payload, data=data)
        assert counts(library) == (0, 0, 0)
        assert not list(library.store.root.rglob("original*"))
    else:
        receipt, created = submit(commands, payload, data=data)
        assert created and not receipt.deduplicated
        assert library.store.read(receipt.version.original_file_ref) == data


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_api_actual_capacity_boundaries(api: ApiFixture, delta: int) -> None:
    client, library = api
    data = b"x" * (MAX_DOCUMENT_FILE_SIZE_BYTES + delta)
    response = post(client, intent(data), data=data)
    assert response.status_code == (413 if delta > 0 else 202)
    if delta > 0:
        assert response.json()["detail"] == "document_file_too_large"
        assert counts(library) == (0, 0, 0)
        assert not list(library.store.root.rglob("original*"))
    else:
        receipt = DocumentUploadReceipt.model_validate(response.json())
        assert receipt.version.status == "received"
        assert receipt.intent.file_size_bytes == len(data)
        downloaded = client.get(
            f"/v1/research-library/versions/{receipt.version.version_id}/original",
        )
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == receipt.intent.file_sha256


def test_empty_and_wrong_declared_identity_never_archive(api: ApiFixture) -> None:
    client, library = api
    response = post(client, intent(b""), data=b"")
    assert response.status_code == 400
    assert response.json()["detail"] == "document_empty_file"
    cases = [
        ({"file_size_bytes": 1}, "document_upload_size_mismatch"),
        ({"file_sha256": "0" * 64}, "document_upload_hash_mismatch"),
    ]
    for changes, expected in cases:
        response = post(client, intent().model_copy(update=changes))
        assert response.status_code == 400 and response.json()["detail"] == expected
    wrong_name = client.post(
        "/v1/research-library/uploads",
        data={"intent": intent().model_dump_json()},
        files={"file": ("different.txt", b"immutable upload source")},
        headers={"X-Owner-Id": "owner-a", "Idempotency-Key": "upload-request-1"},
    )
    assert wrong_name.status_code == 400
    assert wrong_name.json()["detail"] == "document_upload_filename_mismatch"
    owner_mismatch = client.post(
        "/v1/research-library/uploads",
        data={"intent": intent().model_dump_json()},
        files={"file": ("source.txt", b"immutable upload source")},
        headers={"X-Owner-Id": "other-owner", "Idempotency-Key": "upload-request-1"},
    )
    assert owner_mismatch.status_code == 403
    assert owner_mismatch.json()["detail"] == "owner_identity_mismatch"
    assert counts(library) == (0, 0, 0)


def test_frozen_receipt_survives_processing_and_distinguishes_new_key(api: ApiFixture) -> None:
    client, library = api
    original = post(client, intent())
    assert original.status_code == 202
    receipt = original.json()
    version_id = receipt["version"]["version_id"]
    with library.database.session() as session:
        row = session.get(ResearchDocumentVersionRecord, version_id)
        assert row is not None
        row.status = "parsed"
    exact = client.get(
        "/v1/research-library/uploads/upload-request-1",
        headers={"X-Owner-Id": "owner-a"},
    )
    assert exact.status_code == 200 and exact.json() == receipt
    assert post(client, intent()).json() == receipt
    second = post(client, intent(), key="upload-request-2")
    assert second.status_code == 202
    assert second.json()["request_id"] == "upload-request-2"
    assert second.json()["deduplicated"] is True
    assert second.json()["version"] == receipt["version"]
    assert client.get(f"/v1/research-library/versions/{version_id}").json()["status"] == "parsed"
    assert counts(library) == (1, 1, 2)


@pytest.mark.parametrize(
    "change",
    [
        {"title": "另一个标题"},
        {"source_type": "official"},
        {"source_profile_id": "official-a"},
        {"author": "作者"},
        {"published_at": datetime(2025, 1, 1, tzinfo=UTC)},
        {"market_scopes": ["cross_asset"]},
        {"instrument_scopes": ["XAUUSD"]},
        {"privacy_mode": "remote_allowed"},
        {"visibility": "shared"},
        {"supersedes_version_id": "docver_previous"},
        {"notes": "不同备注"},
    ],
)
def test_metadata_changes_are_distinct_and_same_key_conflicts(
    library: ResearchLibraryService, change: dict[str, Any]
) -> None:
    commands = ResearchLibraryCommands(library)
    first, _ = submit(commands, intent())
    changed = intent(**change)
    with pytest.raises(ValueError, match="product_request_reused"):
        submit(commands, changed)
    assert counts(library) == (1, 1, 1)
    second, created = submit(commands, changed, key="upload-request-2")
    assert created and not second.deduplicated
    assert first.version.document_id != second.version.document_id
    assert first.version.original_file_ref == second.version.original_file_ref
    assert commands.upload_receipt(first.request_id, "owner-a") == first
    assert second.intent.metadata == changed.metadata
    assert counts(library) == (2, 2, 2)


def test_filename_owner_and_file_change_are_not_same_intent(
    library: ResearchLibraryService,
) -> None:
    commands = ResearchLibraryCommands(library)
    first, _ = submit(commands, intent())
    for index, changed in enumerate([intent(name="renamed.txt"), intent(owner="owner-b")]):
        with pytest.raises(ValueError, match="product_request_reused"):
            submit(commands, changed)
        next_receipt, created = submit(commands, changed, key=f"upload-other-{index}")
        assert created and next_receipt.version.document_id != first.version.document_id
    changed = intent(b"new bytes")
    with pytest.raises(ValueError, match="product_request_reused"):
        submit(commands, changed, data=b"new bytes")
    assert counts(library) == (3, 3, 3)


def test_parallel_new_keys_share_one_version_but_have_own_receipts(
    library: ResearchLibraryService,
) -> None:
    commands = ResearchLibraryCommands(library)
    barrier = Barrier(8)

    def upload_one(index: int) -> tuple[DocumentUploadReceipt, bool]:
        barrier.wait()
        return submit(commands, intent(), key=f"parallel-upload-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(upload_one, range(8)))
    assert len({receipt.version.version_id for receipt, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert sum(not receipt.deduplicated for receipt, _ in results) == 1
    assert len({receipt.request_id for receipt, _ in results}) == 8
    assert counts(library) == (1, 1, 8)
    for receipt, _ in results:
        assert commands.upload_receipt(receipt.request_id, "owner-a") == receipt


def test_parallel_same_key_is_exactly_one_command(library: ResearchLibraryService) -> None:
    commands = ResearchLibraryCommands(library)

    def upload_same_key(_index: int) -> tuple[DocumentUploadReceipt, bool]:
        return submit(commands, intent())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(upload_same_key, range(4)))
    assert all(result[0] == results[0][0] for result in results)
    assert sum(created for _, created in results) == 1
    assert counts(library) == (1, 1, 1)


def test_transaction_rollback_preserves_shared_original_and_all_old_rows(
    library: ResearchLibraryService, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = ResearchLibraryCommands(library)
    original, _ = submit(commands, intent())
    stored = library.store.read(original.version.original_file_ref)

    def fail_to_save(*_args: Any) -> None:
        raise RuntimeError("isolated receipt persistence failure")

    with monkeypatch.context() as failure:
        failure.setattr(commands, "_save", fail_to_save)
        with pytest.raises(RuntimeError, match="persistence failure"):
            submit(commands, intent(notes="different"), key="upload-after-failed-save")
    assert counts(library) == (1, 1, 1)
    assert library.store.read(original.version.original_file_ref) == stored
    assert commands.upload_receipt(original.request_id, "owner-a") == original
    with pytest.raises(KeyError, match="receipt_not_found"):
        commands.upload_receipt("upload-after-failed-save", "owner-a")
    recovered, created = submit(
        commands,
        intent(notes="different"),
        key="upload-after-failed-save",
    )
    assert created and recovered.version.original_file_ref == original.version.original_file_ref
    assert counts(library) == (2, 2, 2)


def test_recovery_can_create_legal_same_hash_version_without_changing_receipt(
    library: ResearchLibraryService,
) -> None:
    commands = ResearchLibraryCommands(library)
    original, _ = submit(commands, intent())
    with library.database.session() as session:
        row = session.get(ResearchDocumentVersionRecord, original.version.version_id)
        assert row is not None
        row.status = "failed"
    recovered = commands.recover(
        original.version.version_id,
        "owner-a",
        DocumentRecoveryCommand(
            schema_version="document-recovery-command.v1",
            request_id="recover-original-upload",
            original_file_hash=original.intent.file_sha256,
        ),
    )
    assert recovered.version_id != original.version.version_id
    assert recovered.original_file_hash == original.intent.file_sha256
    assert counts(library) == (1, 2, 1)
    assert commands.upload_receipt(original.request_id, "owner-a") == original


def test_exact_api_receipt_scope_conflict_and_no_file_needed(api: ApiFixture) -> None:
    client, library = api
    first = post(client, intent())
    assert first.status_code == 202
    conflict = post(client, intent(notes="changed"))
    assert conflict.status_code == 409 and conflict.json()["detail"] == "product_request_reused"
    for key, owner in [("missing-key", "owner-a"), ("upload-request-1", "owner-b")]:
        missing = client.get(
            f"/v1/research-library/uploads/{key}",
            headers={"X-Owner-Id": owner},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "document_upload_receipt_not_found"
    with library.database.session() as session:
        session.add(
            ProductCommandRecord(
                request_id="some-other-command",
                owner_id="owner-a",
                target_id="other",
                kind="library_recovery",
                payload_hash="0" * 64,
                result_json="{}",
                created_at=utcnow(),
            )
        )
    other = client.get(
        "/v1/research-library/uploads/some-other-command",
        headers={"X-Owner-Id": "owner-a"},
    )
    assert other.status_code == 404
    denied = client.get("/v1/research-library/uploads/upload-request-1")
    assert denied.status_code == 403
    assert counts(library) == (1, 1, 1)


def test_policy_raw_request_limit_and_legacy_file_limit(api: ApiFixture) -> None:
    client, library = api
    policy = client.get("/v1/research-library/upload-policy")
    assert policy.json() == {
        "schema_version": "document-upload-policy.v1",
        "max_file_size_bytes": 20971520,
    }
    too_large = b"x" * (MAX_DOCUMENT_REQUEST_BYTES + 1)
    response = post(client, intent(too_large), data=too_large)
    assert response.status_code == 413
    assert response.json()["detail"] == "document_upload_request_too_large"
    assert counts(library) == (0, 0, 0)
    assert not list(library.store.root.rglob("original*"))
    response = client.post(
        "/v1/research-library/documents",
        data={"metadata": metadata().model_dump_json()},
        files={"file": ("source.txt", b"x" * (MAX_DOCUMENT_FILE_SIZE_BYTES + 1))},
        headers={"X-Owner-Id": "owner-a"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "document_file_too_large"
    assert counts(library) == (0, 0, 0)


def test_legacy_full_intent_is_serialized_and_preserves_metadata_changes(
    library: ResearchLibraryService,
) -> None:
    payload = metadata()

    def upload_same_legacy(_index: int) -> ResearchDocumentVersion:
        return library.upload(payload, filename="legacy.txt", data=b"legacy source")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(upload_same_legacy, range(4)))
    assert len({result.version_id for result in results}) == 1
    different = library.upload(
        metadata(notes="different legacy intent"),
        filename="legacy.txt",
        data=b"legacy source",
    )
    assert different.document_id != results[0].document_id
    assert counts(library) == (2, 2, 2)


def test_old_hash_without_complete_receipt_does_not_prove_same_metadata(
    library: ResearchLibraryService,
) -> None:
    with library.database.session() as session:
        old = library.record_upload_in_session(
            session, metadata(), filename="old.txt", data=b"old bytes"
        )
    commands = ResearchLibraryCommands(library)
    new, created = submit(commands, intent(b"old bytes", name="old.txt"), data=b"old bytes")
    assert created and not new.deduplicated
    assert new.version.document_id != old.document_id
    assert new.version.original_file_ref == old.original_file_ref
    assert library.get_version(old.version_id) == old
    assert counts(library) == (2, 2, 1)


@pytest.mark.parametrize("extra", [0, 5])
async def test_route_reader_never_uses_unbounded_read_or_copies_past_l_plus_one(extra: int) -> None:
    class ReadAudit(BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.calls: list[int | None] = []

        def read(self, size: int | None = -1) -> bytes:
            self.calls.append(size)
            return super().read(size)

    stream = ReadAudit(b"x" * (MAX_DOCUMENT_FILE_SIZE_BYTES + extra))
    upload_file = UploadFile(file=stream, filename="source.txt", size=1)
    if extra:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            await read_document_upload(upload_file)
        assert error.value.status_code == 413
        assert stream.tell() == MAX_DOCUMENT_FILE_SIZE_BYTES + 1
    else:
        data = await read_document_upload(upload_file)
        assert len(data) == MAX_DOCUMENT_FILE_SIZE_BYTES
    assert stream.calls and all(size is not None and 0 < size <= 64 * 1024 for size in stream.calls)


def test_upload_persistence_failure_keeps_uncommitted_original_for_later_retry(
    library: ResearchLibraryService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = ResearchLibraryCommands(library)
    original, _ = submit(commands, intent())
    data = b"a distinct new source retained after failed receipt commit"
    payload = intent(data)

    def fail_to_save(*_args: Any) -> None:
        raise RuntimeError("isolated receipt persistence failure")

    with monkeypatch.context() as failure:
        failure.setattr(commands, "_save", fail_to_save)
        with pytest.raises(RuntimeError, match="persistence failure"):
            submit(commands, payload, key="failed-new-original", data=data)
    assert counts(library) == (1, 1, 1)
    assets = list(library.store.root.rglob("original.txt"))
    assert len(assets) == 2
    assert any(path.read_bytes() == data for path in assets)
    assert library.store.read(original.version.original_file_ref) == b"immutable upload source"
    receipt, created = submit(commands, payload, key="failed-new-original", data=data)
    assert created and library.store.read(receipt.version.original_file_ref) == data
    assert counts(library) == (2, 2, 2)


async def test_actual_corrupt_pdf_parse_failure_does_not_change_upload_acceptance_receipt(
    api: ApiFixture,
) -> None:
    client, library = api
    data = b"%PDF-1.7\ncorrupt content without a cross reference table or EOF"
    submitted = post(client, intent(data, name="broken.pdf"), data=data)
    assert submitted.status_code == 202
    receipt = DocumentUploadReceipt.model_validate(submitted.json())
    assert receipt.version.status == "received"
    with pytest.raises(PdfReadError):
        await library.process_next(worker_id="isolated-real-pypdf-test")
    current = library.get_version(receipt.version.version_id)
    assert current is not None and current.status == "failed"
    assert any("document_processing_failed:parse" in issue for issue in current.quality.issues)
    confirmed = client.get(
        f"/v1/research-library/uploads/{receipt.request_id}",
        headers={"X-Owner-Id": receipt.intent.owner_id},
    )
    assert confirmed.status_code == 200
    assert DocumentUploadReceipt.model_validate(confirmed.json()) == receipt
    original = client.get(
        f"/v1/research-library/versions/{receipt.version.version_id}/original",
    )
    assert original.status_code == 200 and original.content == data
    assert hashlib.sha256(original.content).hexdigest() == receipt.intent.file_sha256
    assert counts(library) == (1, 1, 1)
