"""A failed document must not terminate the production consumer loop."""

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.hub_worker.documents import DocumentPipelineWorker
from apps.hub_worker.main import _run_documents_loop  # pyright: ignore[reportPrivateUsage]
from packages.contracts_py.decision_hub_contracts import DocumentUpload
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.documents import DocumentParserPort
from packages.provider_adapters.documents import (
    DocumentParserRouter,
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore


def test_production_loop_continues_after_a_failed_tick_and_preserves_cancellation(
    capsys: pytest.CaptureFixture[str],
):
    worker = MagicMock(spec=DocumentPipelineWorker)
    worker.tick = AsyncMock(side_effect=[ValueError("bad original"), "docver_next",
                                        asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run_documents_loop(worker, once=False, interval=0))
    assert worker.tick.await_count == 3
    log = capsys.readouterr().out
    assert '"status": "tick_failed"' in log
    assert "document_processing_failed:ValueError" in log
    assert "bad original" not in log


@pytest.mark.parametrize("error, expected", [(None, 0), (ValueError("bad"), 1)])
def test_once_mode_reports_the_tick_result_without_retry(error: Exception | None, expected: int):
    worker = MagicMock(spec=DocumentPipelineWorker)
    worker.tick = AsyncMock(return_value="docver_next", side_effect=error)
    assert asyncio.run(_run_documents_loop(worker, once=True, interval=0)) == expected
    assert worker.tick.await_count == 1


def test_worker_records_failure_heartbeat_then_clears_it_after_actual_next_tick():
    service = MagicMock()
    service.process_next = AsyncMock(side_effect=[ValueError("bad original"), "docver_next"])
    worker = DocumentPipelineWorker(service, MagicMock())
    worker.heartbeats = MagicMock()
    with pytest.raises(ValueError):
        asyncio.run(worker.tick())
    assert worker.last_error_code == "document_processing_failed:ValueError"
    assert worker.heartbeats.beat.call_args.kwargs["last_error_code"] == worker.last_error_code
    assert asyncio.run(worker.tick()) == "docver_next"
    assert worker.last_error_code is None
    assert worker.heartbeats.beat.call_args.kwargs["last_error_code"] is None


def test_worker_cancellation_is_not_classified_as_a_document_failure():
    service = MagicMock()
    service.process_next = AsyncMock(side_effect=asyncio.CancelledError())
    worker = DocumentPipelineWorker(service, MagicMock())
    worker.heartbeats = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.tick())
    assert worker.last_error_code is None


def test_real_production_loop_records_corrupt_pdf_and_parses_the_next_original(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'queue.sqlite3'}")
    database.create_all()
    store = DocumentBundleStore(tmp_path / "originals")
    library = ResearchLibraryService(
        database, store,
        cast(DocumentParserPort, DocumentParserRouter((PypdfTextDocumentParser(store),))),
        FinancialSemanticCorrectionAdapter(store), HeuristicAnalystClaimExtractor(),
    )
    metadata = DocumentUpload(
        schema_version="document-upload.v1", title="isolated consumer fixture",
        source_type="private_file", market_scopes=["crypto_macro"],
        privacy_mode="local_only", visibility="private",
    )
    corrupt = library.upload(metadata, filename="corrupt.pdf", data=b"not a PDF")
    original = b"Actual readable source following a damaged PDF. No model executed."
    valid = library.upload(metadata, filename="valid.txt", data=original)

    class StopAfterValid(DocumentPipelineWorker):
        async def tick(self) -> str | None:
            result = await super().tick()
            if result == valid.version_id:
                raise asyncio.CancelledError
            return result

    worker = StopAfterValid(library, database)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run_documents_loop(worker, once=False, interval=0))
    failed = library.get_version(corrupt.version_id)
    parsed = library.get_version(valid.version_id)
    assert failed is not None and parsed is not None
    assert failed.status == "failed"
    assert "document_processing_failed:parse:PdfStreamError" in failed.quality.issues
    assert parsed.status == "parsed" and parsed.block_count == 1
    assert store.read(failed.original_file_ref) == b"not a PDF"
    assert store.read(parsed.original_file_ref) == original
    assert worker.last_error_code is None
