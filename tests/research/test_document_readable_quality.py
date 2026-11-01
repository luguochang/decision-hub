from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from packages.contracts_py.decision_hub_contracts import DocumentUpload, ResearchRequest
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchDocumentCorrectionRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
)
from packages.kernel.decision_hub_kernel.ports.documents import DocumentInput
from packages.provider_adapters.documents import (
    DocumentParserRouter,
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    ImagePlaceholderDocumentParser,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore


def pdf_bytes(pages: list[str]) -> bytes:
    """Actual PDF bytes: empty pages have no text layer, others use a plain font."""
    writer = PdfWriter()
    for content in pages:
        page = writer.add_blank_page(width=300, height=200)
        if not content:
            continue
        font = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        })
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 20 100 Td ({content}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def library(tmp_path: Path) -> ResearchLibraryService:
    database = Database(f'sqlite+pysqlite:///{tmp_path / "quality.sqlite3"}')
    database.create_all()
    store = DocumentBundleStore(tmp_path / 'bundles')
    return ResearchLibraryService(
        database, store,
        DocumentParserRouter((
            PypdfTextDocumentParser(store), ImagePlaceholderDocumentParser(store),
        )),
        FinancialSemanticCorrectionAdapter(store), HeuristicAnalystClaimExtractor(),
    )


def upload(service: ResearchLibraryService, filename: str, data: bytes):
    return service.upload(DocumentUpload(
        schema_version='document-upload.v1', title='PC isolated quality inspection',
        source_type='private_file', market_scopes=['crypto_macro'],
        instrument_scopes=['BTC-USDT'], privacy_mode='local_only', visibility='private',
    ), filename=filename, data=data)


@pytest.mark.asyncio
async def test_actual_pdf_without_text_layer_is_blocked_and_preserves_original(tmp_path):
    service = library(tmp_path)
    original = pdf_bytes(['', ''])
    version = upload(service, 'no-text.pdf', original)
    await service.process_next()
    parsed = service.get_version(version.version_id)
    assert parsed.quality.quality_status == 'blocked_low_quality'
    assert parsed.quality.text_readability == 0
    assert 'no_readable_text' in parsed.quality.issues
    assert 'ocr_required' in parsed.quality.issues
    assert service.get_blocks(version.version_id) == []
    assert service.store.read(version.original_file_ref) == original
    receipt = json.loads(service.store.read(parsed.parse_receipt_ref))
    assert receipt['page_count'] == 2
    assert service.store.read(receipt['parsed_bundle_ref']) == b''
    with pytest.raises(ValueError, match='document_quality_blocked'):
        service.request_correction(version.version_id)


def test_mixed_pdf_preserves_real_text_and_actual_page_locators(tmp_path):
    store = DocumentBundleStore(tmp_path / 'bundles')
    path = tmp_path / 'mixed.pdf'
    path.write_bytes(pdf_bytes([
        '', 'First readable paragraph.\nSecond paragraph.', '', 'Fourth page.',
    ]))
    parsed = PypdfTextDocumentParser(store).parse(DocumentInput(
        'docver_mixed', path, 'docbundle://test/original.pdf', 'a' * 64, 'Mixed PDF', 'local_only',
    ))
    assert [block.page_no for block in parsed.blocks] == [2, 2, 4]
    assert [block.content for block in parsed.blocks] == [
        'First readable paragraph.', 'Second paragraph.', 'Fourth page.',
    ]
    assert all('[Page' not in block.content for block in parsed.blocks)
    assert parsed.receipt.page_count == 4
    assert parsed.receipt.quality.quality_status == 'needs_review'
    assert 'pages_without_readable_text:1,3' in parsed.receipt.quality.issues
    assert parsed.normalized_text == 'First readable paragraph.\nSecond paragraph.\n\nFourth page.'


@pytest.mark.asyncio
async def test_explicit_failed_parse_quality_cannot_start_correction(tmp_path):
    service = library(tmp_path)
    version = upload(service, 'source.txt', b'Actual readable source.')
    await service.process_next()
    with service.database.session() as session:
        row = session.get(ResearchDocumentVersionRecord, version.version_id)
        quality = json.loads(row.quality_json)
        quality['quality_status'] = 'failed'
        row.quality_json = json.dumps(quality)
    with pytest.raises(ValueError, match='document_quality_blocked'):
        service.request_correction(version.version_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked_source', ['parse', 'correction'])
async def test_review_or_ready_cannot_bypass_explicit_quality_failure(tmp_path, blocked_source):
    service = library(tmp_path)
    version = upload(service, 'source.txt', b'Actual readable source.')
    await service.process_next()
    service.request_correction(version.version_id)
    await service.process_next()
    if blocked_source == 'parse':
        await service.accept_correction(version.version_id)
    with service.database.session() as session:
        if blocked_source == 'parse':
            row = session.get(ResearchDocumentVersionRecord, version.version_id)
            quality = json.loads(row.quality_json)
            quality['quality_status'] = 'blocked_low_quality'
            row.quality_json = json.dumps(quality)
        else:
            row = session.query(ResearchDocumentCorrectionRecord).filter_by(
                version_id=version.version_id,
            ).one()
            row.quality_status = 'blocked_low_quality'
    with pytest.raises(ValueError, match='document_quality_blocked'):
        await service.accept_correction(version.version_id)


@pytest.mark.asyncio
async def test_historical_ready_blocked_version_cannot_enter_a_new_context(tmp_path):
    service = library(tmp_path)
    version = upload(service, 'source.txt', b'Actual readable source.')
    await service.process_next()
    service.request_correction(version.version_id)
    await service.process_next()
    await service.accept_correction(version.version_id)
    request = ResearchRequest(
        schema_version='research-request.v1', research_family='crypto_macro',
        instrument_scope=['BTC-USDT'], focus='Local acceptance only.', horizon='72h',
        library_context_mode='selected', selected_version_ids=[version.version_id],
        allow_remote_library_content=True,
    )
    original_context = service.select_context(request, run_id='run_frozen_before_quality_failure')
    with service.database.session() as session:
        row = session.get(ResearchDocumentVersionRecord, version.version_id)
        quality = json.loads(row.quality_json)
        quality['quality_status'] = 'blocked_low_quality'
        row.quality_json = json.dumps(quality)
        original_row = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    with pytest.raises(ValueError, match='document_quality_blocked'):
        service.select_context(request, run_id='run_no_new_context')
    assert service.select_context(
        request, run_id='run_frozen_before_quality_failure',
    ) == original_context
    with service.database.session() as session:
        assert session.query(ResearchRunScopeRecord).count() == 1
        assert session.get(ResearchRunScopeRecord, 'run_no_new_context') is None
        row = session.get(ResearchDocumentVersionRecord, version.version_id)
        actual_row = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        assert actual_row == original_row


@pytest.mark.asyncio
async def test_quality_change_during_claim_extraction_cannot_commit_ready(tmp_path):
    service = library(tmp_path)
    version = upload(service, 'source.txt', b'Actual readable source.')
    await service.process_next()
    service.request_correction(version.version_id)
    await service.process_next()

    class QualityChangedWhileExtracting:
        async def extract(self, **_kwargs):
            with service.database.session() as session:
                row = session.get(ResearchDocumentVersionRecord, version.version_id)
                quality = json.loads(row.quality_json)
                quality['quality_status'] = 'blocked_low_quality'
                row.quality_json = json.dumps(quality)
            return []

    service.claims = QualityChangedWhileExtracting()
    with pytest.raises(ValueError, match='document_quality_blocked'):
        await service.accept_correction(version.version_id)
    assert service.get_version(version.version_id).status == 'review'
    with service.database.session() as session:
        correction = session.query(ResearchDocumentCorrectionRecord).filter_by(
            version_id=version.version_id,
        ).one()
        assert correction.accepted_at is None
