from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts import DocumentUpload, ResearchRequest
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchDocumentCorrectionRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
    RunRecord,
    SourceAcquisitionRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.documents import DocumentInput
from packages.kernel.decision_hub_kernel.ports.runtime import AgentResult, AgentUsage
from packages.provider_adapters.documents import (
    DocumentParserRouter,
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    ImagePlaceholderDocumentParser,
    MineruCliDocumentParser,
    ModelFinancialSemanticCorrectionAdapter,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore


def build_service(tmp_path: Path) -> ResearchLibraryService:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'library.sqlite3'}")
    database.create_all()
    store = DocumentBundleStore(tmp_path / "bundles")
    parser = DocumentParserRouter(
        (
            MineruCliDocumentParser(store),
            PypdfTextDocumentParser(store),
            ImagePlaceholderDocumentParser(store),
        )
    )

    return ResearchLibraryService(
        database,
        store,
        parser,
        FinancialSemanticCorrectionAdapter(store),
        HeuristicAnalystClaimExtractor(),
    )


@pytest.mark.asyncio
async def test_long_document_order_and_multiline_correction_keep_block_identity(tmp_path):
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="Long source",
        source_type="private_file",
        market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"],
        privacy_mode="local_only",
        visibility="private",
    )
    original = "\n".join(f"Paragraph {i}: BTC evidence." for i in range(1, 31)).encode()
    version = service.upload(payload, filename="long.txt", data=original)
    await service.process_next()
    blocks = service.get_blocks(version.version_id)
    assert [int(block.block_id.rsplit(":", 1)[1]) for block in blocks] == list(range(1, 31))
    service.request_correction(version.version_id)
    await service.process_next()
    correction = service.get_correction(version.version_id)
    assert service.store.read(correction.corrected_content_ref).decode().splitlines() == [
        block.content for block in blocks
    ]
    with service.database.session() as session:
        row = session.get(ResearchDocumentCorrectionRecord, correction.correction_id)
        row.changes_json = json.dumps(
            [
                {
                    "schema_version": "document-correction-change.v1",
                    "change_id": "multiline",
                    "page_no": blocks[1].page_no,
                    "block_id": blocks[1].block_id,
                    "original": blocks[1].content,
                    "corrected": "First line\nSecond line",
                    "reason": "reviewed correction",
                    "confidence": "high",
                    "requires_user_confirmation": True,
                    "accepted": False,
                }
            ]
        )
    captured = []

    class CaptureClaims:
        async def extract(self, **kwargs):
            captured.extend(kwargs["corrected_blocks"])
            return []

    service.claims = CaptureClaims()
    assert (await service.accept_correction(version.version_id)).status == "ready"
    assert [block.block_id for block in captured] == [block.block_id for block in blocks]
    assert captured[1].content == "First line\nSecond line"
    assert captured[2].content == blocks[2].content
    assert service.store.read(version.original_file_ref) == original


@pytest.mark.asyncio
async def test_claim_failure_remains_reviewable_and_retry_finishes_atomically(tmp_path):
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="Retry",
        source_type="private_file",
        market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"],
        privacy_mode="local_only",
        visibility="private",
    )
    version = service.upload(payload, filename="retry.txt", data=b"BTC evidence.")
    await service.process_next()
    service.request_correction(version.version_id)
    await service.process_next()
    extractor = service.claims

    class FailedClaims:
        async def extract(self, **kwargs):
            raise RuntimeError("extractor unavailable")

    service.claims = FailedClaims()
    with pytest.raises(RuntimeError, match="extractor unavailable"):
        await service.accept_correction(version.version_id)
    assert service.get_version(version.version_id).status == "review"
    assert service.get_correction(version.version_id).accepted_at is None
    service.claims = extractor
    assert (await service.accept_correction(version.version_id)).status == "ready"
    assert (await service.accept_correction(version.version_id)).status == "ready"


@pytest.mark.asyncio
async def test_interrupted_parsing_recovers_and_old_owner_cannot_overwrite(tmp_path):
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="Recovery note",
        source_type="private_file",
        market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"],
        privacy_mode="local_only",
        visibility="private",
    )
    raw = b"BTC research evidence."
    version = service.upload(payload, filename="recovery.txt", data=raw)
    assert service._claim_next("old-worker") == (version.version_id, "received")
    assert await service.process_next(worker_id="overlap") is None
    with service.database.session() as session:
        row = session.get(ResearchDocumentVersionRecord, version.version_id)
        assert row.status == "parsing"
        row.lease_until = utcnow() - timedelta(seconds=1)
    assert await service.process_next(worker_id="recovered") == version.version_id
    assert service.get_version(version.version_id).status == "parsed"
    with pytest.raises(PermissionError, match="lease_lost"):
        service._parse_version(version.version_id, "old-worker")
    assert service.get_version(version.version_id).status == "parsed"
    assert service.store.read(version.original_file_ref) == raw


@pytest.mark.asyncio
async def test_text_upload_parse_correction_and_claim_review(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="analyst note",
        source_type="private_file",
        market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"],
        privacy_mode="local_only",
        visibility="private",
    )
    version = service.upload(payload, filename="note.txt", data="BTC 看多，目标上涨 10%。".encode())
    assert version.status == "received"
    assert await service.process_next() == version.version_id
    assert service.get_version(version.version_id).status == "parsed"  # type: ignore[union-attr]
    assert service.get_blocks(version.version_id)[0].locator.endswith("/block/1")

    assert service.request_correction(version.version_id).status == "correcting"
    await service.process_next()
    correction = service.get_correction(version.version_id)
    assert correction is not None
    assert correction.quality_status == "needs_review"

    final = await service.accept_correction(version.version_id)
    assert final is not None and final.status == "ready"
    claims = service.get_claims(version.version_id)
    assert claims and claims[0].requires_independent_evidence is True
    scope = service.select_context(
        ResearchRequest(
            schema_version="research-request.v1",
            research_family="crypto_macro",
            instrument_scope=["BTC-USDT"],
            focus="BTC 最新观点",
            library_context_mode="selected",
            allow_remote_library_content=True,
            selected_version_ids=[version.version_id],
        ),
        run_id="run_library_test",
    )
    assert scope["selected_version_ids"] == [version.version_id]
    persisted_scope = service.get_context("run_library_test")
    assert persisted_scope is not None
    assert persisted_scope.selected_version_ids == [version.version_id]
    assert service.get_context("run_without_library_scope") is None


@pytest.mark.asyncio
async def test_zero_claim_source_enters_context_with_locators_and_source_time(tmp_path):
    from apps.hub_worker.research import _library_candidates
    from packages.kernel.decision_hub_kernel.application.research_context import (
        build_task_policy,
        select_context,
    )

    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="Official meeting calendar",
        source_type="private_file",
        market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"],
        privacy_mode="local_only",
        visibility="private",
    )
    original = "\n".join(f"Meeting {i}: January 27-28. " * 8 for i in range(1, 60)).encode()
    version = service.upload(payload, filename="calendar.txt", data=original)
    await service.process_next()
    service.request_correction(version.version_id)
    await service.process_next()
    await service.accept_correction(version.version_id)
    assert service.get_claims(version.version_id) == []
    request = ResearchRequest(
        schema_version="research-request.v1",
        research_family="crypto_macro",
        instrument_scope=["BTC-USDT"],
        focus="BTC",
        library_context_mode="selected",
        selected_version_ids=[version.version_id],
        allow_remote_library_content=True,
    )
    service.select_context(request, run_id="run_calendar")
    candidates = _library_candidates(service.database, "run_calendar")
    assert len(candidates) > 1
    assert all(item.kind == "document" and item.authority == "unverified" for item in candidates)
    assert all(item.published_at is None for item in candidates)
    assert all(item.received_at == version.received_at for item in candidates)
    assert all(item.source_content_hash == version.original_file_hash for item in candidates)
    assert all(len(item.excerpt) <= 4000 for item in candidates)
    assert all(
        f"document://{version.version_id}/page/1/block/" in item.excerpt for item in candidates
    )
    assert all(
        item.content_hash == hashlib.sha256(item.excerpt.encode()).hexdigest()
        for item in candidates
    )
    from packages.provider_adapters.research import CryptoMacroFactPack

    requirements = CryptoMacroFactPack.from_pack(
        Path("packs/crypto_macro")
    ).contract_requirements_for_event("crypto_market_snapshot")
    policy = build_task_policy(
        research_mode="current_decision",
        requirements=requirements,
        created_at=utcnow(),
        max_evidence_rounds=1,
    ).model_copy(update={"max_context_items": 1})
    inputs, manifest = select_context(
        [],
        candidates,
        requirements=requirements,
        policy=policy,
        manifest_prefix="test",
        revision=1,
        created_at=utcnow(),
    )
    assert inputs
    assert set(manifest.effective_inputs) | {
        item.evidence_ref for item in manifest.excluded_inputs
    } == {item.evidence_id for item in candidates}
    _, round_manifest = select_context(
        inputs,
        [],
        requirements=requirements,
        policy=policy,
        manifest_prefix="round",
        revision=2,
        created_at=utcnow(),
        prior_exclusions=manifest.excluded_inputs,
    )
    assert round_manifest.excluded_inputs == manifest.excluded_inputs
    assert service.store.read(version.original_file_ref) == original
    denied = request.model_copy(update={"allow_remote_library_content": False})
    with pytest.raises(ValueError, match="document_remote_consent_required"):
        service.select_context(denied, run_id="run_denied")
    assert service.get_context("run_denied") is None
    with pytest.raises(ValueError, match="research_library_context_immutable"):
        service.select_context(denied, run_id="run_calendar")
    assert service.get_context("run_calendar").allow_remote_library_content is True
    with service.database.session() as session:
        session.get(ResearchRunScopeRecord, "run_calendar").allow_remote_library_content = False
    with pytest.raises(ValueError, match="document_remote_consent_required"):
        _library_candidates(service.database, "run_calendar")


@pytest.mark.asyncio
async def test_model_correction_is_opt_in_review_diff_and_preserves_source(tmp_path: Path) -> None:
    class StubModelRuntime:
        enabled = True
        provider_config = SimpleNamespace(model="fixture-finance")

        async def execute(self, request: object) -> AgentResult:
            return AgentResult(
                role="financial_document_correction",
                payload={
                    "changes": [
                        {
                            "block_id": "docver_fixture:block:1",
                            "original": "BTC 看多，目标上升 10%。",
                            "corrected": "BTC 看多，目标上涨 10%。",
                            "reason": "金融术语 OCR 纠正",
                            "confidence": "high",
                        }
                    ],
                    "unresolved": [],
                },
                runtime_id="stub",
                runtime_version="stub.v1",
                latency_ms=1,
                usage=AgentUsage(cost_status="unknown"),
            )

    store = DocumentBundleStore(tmp_path / "bundles")
    source_path = tmp_path / "source.txt"
    source_text = "BTC 看多，目标上升 10%。"
    source_path.write_text(source_text, encoding="utf-8")
    parsed = PypdfTextDocumentParser(store).parse(
        DocumentInput(
            version_id="docver_fixture",
            file_path=source_path,
            original_file_ref="docbundle://fixture/source.txt",
            original_file_hash=hashlib.sha256(source_text.encode()).hexdigest(),
            title="fixture",
            privacy_mode="remote_allowed",
        )
    )
    adapter = ModelFinancialSemanticCorrectionAdapter(store, StubModelRuntime())
    result = await adapter.correct(
        parsed,
        title="fixture",
        market_scopes=("crypto_macro",),
        instrument_scopes=("BTC-USDT",),
        privacy_mode="remote_allowed",
    )
    assert result.correction.correction_model_ref == "financial-correction.fixture-finance.v2"
    assert result.correction.quality_status == "needs_review"
    assert result.correction.changes[0].original == source_text
    assert result.correction.changes[0].accepted is False
    assert result.corrected_text == "BTC 看多，目标上涨 10%。"
    assert source_path.read_text(encoding="utf-8") == source_text


@pytest.mark.asyncio
async def test_image_without_ocr_is_blocked_and_never_fabricates_text(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="screenshot",
        source_type="private_file",
        market_scopes=["cn_equity"],
        instrument_scopes=["000001"],
        privacy_mode="local_only",
        visibility="private",
    )
    version = service.upload(payload, filename="screen.png", data=b"not-an-image-but-immutable")
    await service.process_next()
    parsed = service.get_version(version.version_id)
    assert parsed is not None
    assert parsed.quality.quality_status == "blocked_low_quality"
    assert service.get_blocks(version.version_id)[0].content.startswith("[图片内容待 OCR")
    with pytest.raises(ValueError, match="document_quality_blocked"):
        service.request_correction(version.version_id)


def test_duplicate_upload_is_idempotent(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="same",
        source_type="private_file",
        market_scopes=["cross_asset"],
        privacy_mode="local_only",
        visibility="private",
    )
    first = service.upload(payload, filename="same.txt", data=b"same")
    second = service.upload(payload, filename="same.txt", data=b"same")
    assert first.version_id == second.version_id
    assert len(service.list_documents()) == 1


def test_existing_acquisition_receipt_is_materialized_without_network(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition.sqlite3'}")
    database.create_all()
    store = DocumentBundleStore(tmp_path / "bundles")
    artifact_root = tmp_path / "acquired"
    artifact_root.mkdir()
    artifact = artifact_root / "page.txt"
    artifact.write_text("official macro note", encoding="utf-8")
    import hashlib
    import json

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    with database.session() as session:
        session.add(
            SourceAcquisitionRecord(
                receipt_id="receipt_test",
                source_id="official-test",
                url="https://example.com/note",
                access_mode="playwright",
                status="succeeded",
                observed_at=utcnow(),
                status_code=200,
                artifacts_json=json.dumps([{"path": artifact.name, "content_hash": digest}]),
                error_code=None,
                receipt_hash="a" * 64,
                created_at=utcnow(),
            )
        )
    service = ResearchLibraryService(
        database,
        store,
        DocumentParserRouter(
            (PypdfTextDocumentParser(store), ImagePlaceholderDocumentParser(store))
        ),
        FinancialSemanticCorrectionAdapter(store),
        HeuristicAnalystClaimExtractor(),
        acquisition_root=artifact_root,
    )
    payload = DocumentUpload(
        schema_version="document-upload.v1",
        title="official note",
        source_type="public_web",
        source_profile_id="official-test",
        market_scopes=["cross_asset"],
        privacy_mode="local_only",
        visibility="shared",
    )
    version = service.import_acquisition("receipt_test", payload)
    assert version.status == "received"


def test_library_api_creates_a_scoped_existing_research_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", str(tmp_path / "bundles"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    metadata = {
        "schema_version": "document-upload.v1",
        "title": "API note",
        "source_type": "private_file",
        "market_scopes": ["crypto_macro"],
        "instrument_scopes": ["BTC-USDT"],
        "privacy_mode": "local_only",
        "visibility": "private",
    }
    with TestClient(create_app(database)) as client:
        uploaded = client.post(
            "/v1/research-library/documents",
            files={"file": ("note.txt", b"BTC bullish scenario", "text/plain")},
            data={"metadata": json.dumps(metadata)},
            headers={"X-Owner-Id": "owner"},
        )
        assert uploaded.status_code == 202
        version_id = uploaded.json()["version_id"]
        assert (
            client.get(f"/v1/research-library/versions/{version_id}").json()["status"] == "parsed"
        )
        assert (
            client.post(
                f"/v1/research-library/versions/{version_id}/correction",
                headers={"X-Owner-Id": "owner"},
            ).status_code == 202
        )
        assert (
            client.post(
                f"/v1/research-library/versions/{version_id}/correction/accept",
                headers={"X-Owner-Id": "owner"},
            ).status_code
            == 200
        )
        queued_payload = {
            "schema_version": "research-request.v1",
            "research_family": "crypto_macro",
            "instrument_scope": ["BTC-USDT"],
            "focus": "给出未来 7 天方向概率",
            "horizon": "7d",
            "library_context_mode": "selected",
            "allow_remote_library_content": True,
            "selected_version_ids": [version_id],
        }
        unauthorized = client.post(
            "/v1/research-library/research",
            headers={"Idempotency-Key": "library-api-research-0001"},
            json=queued_payload,
        )
        assert unauthorized.status_code == 403
        assert unauthorized.json()["detail"] == "owner_identity_required"
        with database.session() as session:
            assert session.query(RunRecord).count() == 0
            assert session.query(ResearchRunScopeRecord).count() == 0
        queued = client.post(
            "/v1/research-library/research",
            headers={"Idempotency-Key": "library-api-research-0001", "X-Owner-Id": "owner"},
            json=queued_payload,
        )
        assert queued.status_code == 202
        assert queued.json()["status"] == "queued"
        linked_scope = client.get(f"/v1/research-library/context/{queued.json()['run_id']}")
        assert linked_scope.status_code == 200
        assert linked_scope.json()["schema_version"] == "research-run-scope.v1"
        assert linked_scope.json()["selected_version_ids"] == [version_id]
        assert linked_scope.json()["allow_remote_library_content"] is True
        context_path = f"/v1/research-library/context/{queued.json()['run_id']}"
        context_denied = client.post(context_path, json=queued_payload)
        assert context_denied.status_code == 403
        assert context_denied.json()["detail"] == "owner_identity_required"
        context_confirmed = client.post(
            context_path, json=queued_payload, headers={"X-Owner-Id": "owner"},
        )
        assert context_confirmed.status_code == 200
        assert context_confirmed.json()["selected_version_ids"] == [version_id]
        context_changed = client.post(
            context_path, json={**queued_payload, "focus": "Different selection"},
            headers={"X-Owner-Id": "owner"},
        )
        assert context_changed.status_code == 409
        assert context_changed.json()["detail"] == "research_library_context_immutable"
        assert client.get(context_path).json() == linked_scope.json()
        with database.session() as session:
            assert session.query(RunRecord).count() == 1
            assert session.query(ResearchRunScopeRecord).count() == 1
        from apps.hub_worker.research import ResearchRequestFactory

        factory = ResearchRequestFactory(database, pack_root=Path("packs/crypto_macro"))
        built = factory.build(queued.json()["event_id"], queued.json()["run_id"])
        scoped_inputs = client.get(
            f"/v1/research-library/context/{queued.json()['run_id']}"
        ).json()["context_inputs"]
        assert scoped_inputs
        for item in scoped_inputs:
            matching = next(
                value for value in built.input_evidence if value.evidence_id == item["evidence_id"]
            )
            assert matching.excerpt == item["excerpt"]
            assert hashlib.sha256(item["excerpt"].encode()).hexdigest() == item["excerpt_hash"]
            assert item["original_file_hash"] == uploaded.json()["original_file_hash"]
        assert any(item["locator_refs"] for item in scoped_inputs)
        from packages.kernel.decision_hub_kernel.application.run import RunService

        child_id, _ = RunService(database).create(
            queued.json()["event_id"],
            strategy_version="research.v1",
            parent_run_id=queued.json()["run_id"],
            admission_origin="scheduled_recheck",
        )
        child = factory.build(queued.json()["event_id"], child_id)
        assert all(not item.evidence_id.startswith("library:") for item in child.input_evidence)
        assert child.context_manifest is not None
        assert any(
            item.evidence_ref == f"library:{version_id}"
            and item.reason == "parent_library_not_selected_for_this_run"
            for item in child.context_manifest.excluded_inputs
        )
        assert client.get(f"/v1/research-library/context/{child_id}").json() is None
        grandchild_id, _ = RunService(database).create(
            queued.json()["event_id"],
            strategy_version="research.v1",
            parent_run_id=child_id,
            admission_origin="scheduled_recheck",
        )
        grandchild = factory.build(queued.json()["event_id"], grandchild_id)
        assert grandchild.context_manifest is not None
        assert grandchild.context_manifest.excluded_inputs == child.context_manifest.excluded_inputs
        assert all(
            not item.evidence_id.startswith("library:") for item in grandchild.input_evidence
        )
        assert client.get(f"/v1/research-library/context/{grandchild_id}").json() is None
        request = {
            "schema_version": "research-request.v1",
            "research_family": "crypto_macro",
            "instrument_scope": ["BTC-USDT"],
            "focus": "Permission check",
            "library_context_mode": "selected",
            "selected_version_ids": [version_id],
        }
        with database.session() as session:
            before = session.query(RunRecord).count()
        denied = client.post(
            "/v1/research-library/research",
            headers={"Idempotency-Key": "denied-library", "X-Owner-Id": "owner"},
            json=request,
        )
        assert denied.status_code == 400
        assert denied.json()["detail"] == "document_remote_consent_required"
        missing = client.post(
            "/v1/research-library/research",
            headers={"Idempotency-Key": "missing-library", "X-Owner-Id": "owner"},
            json={
                **request,
                "selected_version_ids": ["docver_missing"],
                "allow_remote_library_content": True,
            },
        )
        assert missing.status_code == 400
        with database.session() as session:
            assert session.query(RunRecord).count() == before


def test_empty_upload_is_rejected_before_writing_original_or_version(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    payload = DocumentUpload(
        schema_version="document-upload.v1", title="Empty upload check",
        source_type="private_file", market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"], privacy_mode="local_only", visibility="private",
    )
    def unexpected_write(*args, **kwargs):
        pytest.fail("Empty file must not enter the original archive")
    monkeypatch.setattr(service.store, "write_original", unexpected_write)
    with pytest.raises(ValueError, match="document_empty_file"):
        service.upload(payload, filename="empty.txt", data=b"")
    with service.database.session() as session:
        assert session.query(ResearchDocumentVersionRecord).count() == 0
    with TestClient(create_app(service.database, source_connectors=[])) as client:
        response = client.post(
            "/v1/research-library/documents",
            data={"metadata": payload.model_dump_json()},
            files={"file": ("empty.txt", b"", "text/plain")},
            headers={"X-Owner-Id": "owner"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "document_empty_file"
    with service.database.session() as session:
        assert session.query(ResearchDocumentVersionRecord).count() == 0
