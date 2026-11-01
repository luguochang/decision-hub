"""Synthetic HTML/HTTP receipts; real library/context rows, no network or model."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import DocumentUpload, SourceAcquisition
from packages.kernel.decision_hub_kernel.application.public_opinion import (
    IMPORT_KIND,
    PublicOpinionImportService,
    PublicOpinionResearchContext,
)
from packages.kernel.decision_hub_kernel.application.research_context_selector_v3 import (
    input_from_candidate,
)
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.application.research_signal import (
    read_claim_signal_in_session,
)
from packages.kernel.decision_hub_kernel.application.source_acquisition import (
    SourceAcquisitionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalystClaimRecord,
    Database,
    ProductCommandRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.provider_adapters.documents.claim_extractor import HeuristicAnalystClaimExtractor
from packages.provider_adapters.documents.correction import FinancialSemanticCorrectionAdapter
from packages.provider_adapters.documents.parser import PypdfTextDocumentParser
from packages.provider_adapters.documents.public_opinion import parse_public_opinion
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry

ROOT = Path(__file__).resolve().parents[2]
URL = "https://www.federalreserve.gov/newsevents/speech/synthetic-fixture.htm"
QUOTE = (
    "If inflation remains persistent, I anticipate additional policy rate hikes, "
    "while weaker employment could require reassessment."
)
HTML = (
    f'<html lang="en"><head><title>Synthetic conditional opinion</title>'
    '<meta name="author" content="Fixture Author"></head><main><time>October 08, 2026</time>'
    f"<p>{QUOTE}</p><p>This fixture is not actual market evidence "
    "and makes no verified forecast.</p>"
    "</main></html>"
).encode()


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'opinion.sqlite'}")
    db.create_all()
    yield _context(tmp_path, db)
    db.engine.dispose()


def _context(tmp_path, db):
    store = DocumentBundleStore(tmp_path / "bundles")
    capture_root = tmp_path / "capture"
    capture_root.mkdir()
    (capture_root / "article.html").write_bytes(HTML)
    registry = SourceRegistry()
    for item in official_source_presets(include_calendar=False):
        registry.register(item)
    source_registry = ResearchSourceRegistry.from_pack(ROOT / "packs" / "crypto_macro")
    source = SourceAcquisitionService(db, registry, artifact_root=capture_root)
    observed = datetime.now(UTC) - timedelta(seconds=2)
    source.ingest(
        SourceAcquisition.model_validate(
            {
                "schema_version": "acquisition-receipt.v1",
                "receipt_id": "synthetic-acquisition",
                "source_id": "fed-speeches",
                "url": URL,
                "access_mode": "http",
                "status": "succeeded",
                "request_started_at": observed - timedelta(seconds=1),
                "request_completed_at": observed,
                "observed_at": observed,
                "status_code": 200,
                "error_code": None,
                "artifacts": [
                    {
                        "kind": "html",
                        "path": str(capture_root / "article.html"),
                        "content_hash": hashlib.sha256(HTML).hexdigest(),
                    }
                ],
            }
        )
    )
    library = ResearchLibraryService(
        db,
        store,
        PypdfTextDocumentParser(store),
        FinancialSemanticCorrectionAdapter(store),
        HeuristicAnalystClaimExtractor(),
        capture_root,
    )
    importer = PublicOpinionImportService(library, source_registry)
    return SimpleNamespace(
        db=db,
        store=store,
        root=capture_root,
        library=library,
        importer=importer,
        context=PublicOpinionResearchContext(library),
        registry=source_registry,
    )


def imported(ctx):
    return ctx.importer.import_acquisition(
        "synthetic-acquisition", market_scopes=("crypto_macro",), instrument_scopes=("BTC-USDT",)
    )


def prepared(ctx):
    version = imported(ctx)
    cutoff = datetime.now(UTC)
    with ctx.db.session() as session:
        begin_immediate(session)
        session.add(
            RunRecord(run_id="run-op", event_id="event-op", created_at=cutoff, updated_at=cutoff)
        )
        session.flush()
        candidates = ctx.context.prepare_candidates_in_session(
            session,
            run_id="run-op",
            research_family="crypto_macro",
            instrument_scopes=("BTC-USDT",),
            cutoff=cutoff,
        )
    return version, cutoff, candidates


def test_public_import_is_idempotent_english_quote_not_fact_and_unknown_publication(context):
    first, second = imported(context), imported(context)
    assert first.version_id == second.version_id
    assert first.status == "ready" and first.claim_count == 1
    assert first.published_at is None
    assert context.store.read(first.original_file_ref) == HTML
    with context.db.session() as session:
        claim = session.scalar(select(AnalystClaimRecord))
        assert claim.claim_text == claim.supporting_quote == QUOTE
        assert claim.claim_type == "scenario" and claim.stance == "unknown"
        assert claim.status == "candidate" and claim.requires_independent_evidence
        assert len(session.scalars(select(ResearchDocumentVersionRecord)).all()) == 1
        assert (
            len(
                session.scalars(
                    select(ProductCommandRecord).where(ProductCommandRecord.kind == IMPORT_KIND)
                ).all()
            )
            == 1
        )


def test_exact_selected_claim_freezes_same_run_original_and_is_delivered_as_unverified(context):
    version, cutoff, candidates = prepared(context)
    assert len(candidates) == 1
    item = candidates[0]
    assert item.authority == "unverified" and item.kind == "document"
    assert QUOTE in item.excerpt and "publication_precision=unknown" in item.excerpt
    with context.db.session() as session:
        begin_immediate(session)
        (frozen,) = context.context.freeze_selected_in_session(
            session, run_id="run-op", selected_inputs=[input_from_candidate(item)], cutoff=cutoff
        )
        read = read_claim_signal_in_session(
            session,
            run_id="run-op",
            output_id=frozen.output_id,
            payload_hash=frozen.payload_hash,
            cutoff=cutoff,
            store=context.store,
        )
        assert read.source_attestation == "acquisition_verified"
        assert read.signal.version_id == version.version_id
        assert read.signal.author == "Fixture Author"
        assert read.signal.source.publication.precision == "unknown"
        assert read.signal.requires_independent_evidence


def test_context_excluded_claim_is_not_frozen(context):
    _, cutoff, _ = prepared(context)
    with context.db.session() as session:
        begin_immediate(session)
        assert (
            context.context.freeze_selected_in_session(
                session, run_id="run-op", selected_inputs=[], cutoff=cutoff
            )
            == ()
        )
        assert session.scalar(select(RunRoleOutputRecord)) is None


def test_wrong_bytes_refuse_before_library_write(context):
    (context.root / "article.html").write_bytes(HTML + b"changed")
    with pytest.raises(ValueError, match="original_hash_mismatch"):
        imported(context)
    with context.db.session() as session:
        assert session.scalar(select(ResearchDocumentVersionRecord)) is None


def test_explicit_no_library_selection_is_respected(context):
    imported(context)
    now = datetime.now(UTC)
    with context.db.session() as session:
        begin_immediate(session)
        session.add(
            ResearchRunScopeRecord(
                run_id="run-op",
                research_family="crypto_macro",
                domain_pack_ref="crypto_macro.research",
                instrument_scope_json='["BTC-USDT"]',
                library_context_mode="none",
                selected_version_ids_json="[]",
                created_at=now,
            )
        )
        session.flush()
        assert (
            context.context.prepare_candidates_in_session(
                session,
                run_id="run-op",
                research_family="crypto_macro",
                instrument_scopes=("BTC-USDT",),
                cutoff=now,
            )
            == ()
        )


def test_different_asset_and_before_received_do_not_enter_context(context):
    imported(context)
    with context.db.session() as session:
        begin_immediate(session)
        assert (
            context.context.prepare_candidates_in_session(
                session,
                run_id="run-eth",
                research_family="crypto_macro",
                instrument_scopes=("ETH-USDT",),
                cutoff=datetime.now(UTC),
            )
            == ()
        )
        assert (
            context.context.prepare_candidates_in_session(
                session,
                run_id="run-past",
                research_family="crypto_macro",
                instrument_scopes=("BTC-USDT",),
                cutoff=datetime.now(UTC) - timedelta(days=1),
            )
            == ()
        )


def test_tradingnews_policy_records_real_gap_instead_of_approving_retention(context):
    decision = context.registry.classify_locator(
        "https://www.tradingnews.com/news/bitcoin-analysis"
    )
    assert decision.status == "discovery_only"
    assert decision.source_ref == "tradingnews.market_analysis"
    assert not decision.allow_fetch and not decision.allow_evidence


def test_author_and_publication_are_not_invented_from_title_or_fetch_clock():
    item = parse_public_opinion(
        HTML.replace(b'<meta name="author" content="Fixture Author">', b""), URL
    )
    assert item.author is None
    assert item.publication.precision == "unknown" and item.publication.published_at is None


def test_normal_library_import_uses_captured_author_not_callers_metadata(context):
    context.library.public_opinion_importer = context.importer
    result = context.library.import_acquisition(
        "synthetic-acquisition",
        DocumentUpload(
            schema_version="document-upload.v1",
            title="Caller title is not provenance",
            source_type="public_web",
            author="Invented author",
            published_at=datetime(2000, 1, 1, tzinfo=UTC),
            market_scopes=["crypto_macro"],
            instrument_scopes=["BTC-USDT"],
            privacy_mode="remote_allowed",
            visibility="private",
        ),
    )
    document = context.library.get_document(result.document_id)
    assert document.author == "Fixture Author"
    assert result.published_at is None
    assert result.status == "ready"


@pytest.mark.parametrize(
    "change",
    [
        {"excerpt": "The original conditional view was silently removed"},
        {"content_hash": "a" * 64},
        {"authority": "official"},
    ],
)
def test_selected_claim_projection_tampering_is_rejected(context, change):
    _, cutoff, candidates = prepared(context)
    item = input_from_candidate(candidates[0]).model_copy(update=change)
    with context.db.session() as session:
        begin_immediate(session)
        with pytest.raises(ValueError, match="selected_claim_projection_changed"):
            context.context.freeze_selected_in_session(
                session,
                run_id="run-op",
                selected_inputs=[item],
                cutoff=cutoff,
            )
        assert session.scalar(select(RunRoleOutputRecord)) is None
