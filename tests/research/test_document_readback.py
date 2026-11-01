from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import AnyUrl

from apps.research_mcp.main import load_capability_manifests
from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceRequirement,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.research import (
    ContentAddressedDocumentStore,
    DocumentReadbackResearchAdapter,
    FetchedDocument,
    HttpDocumentResearchAdapter,
    ResearchSourceRegistry,
)
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)

NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def _reserve(database: Database, event_id: str, request_hash: str) -> tuple[str, str]:
    run_id, _ = RunService(database, clock=lambda: NOW).create(event_id)
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, request_hash, 1)
    DshSessionLinkService(database, clock=lambda: NOW).reserve(
        DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=request_hash,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=NOW + timedelta(minutes=5),
            model_step_timeout_ms=150_000,
            max_tool_calls=12,
            generation=1,
        ),
        DshUpstreamIdentity(
            source_commit="c" * 40,
            source_version="0.1.2",
            package_versions={"dsh": "0.1.2"},
            plugin_build_hash="b" * 64,
        ),
    )
    return run_id, session_id


def _requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id="event_identity",
        description="Identify and verify the official event.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["web.fetch", "document.readback"],
        freshness_seconds=86_400,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.5,
    )


def _query(
    session_id: str,
    *,
    request_id: str,
    capability_id: str,
    query: str,
    target_url: str | None = None,
    evidence_ref: str | None = None,
    content_ref: str | None = None,
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id=request_id,
        capability_id=capability_id,
        requirement_id="event_identity",
        query=query,
        target_url=cast(AnyUrl | None, target_url),
        symbols=[],
        fields=[],
        allowed_domains=["federalreserve.gov"] if target_url else [],
        max_results=1,
        max_cost_usd=0.0,
        research_session_id=session_id,
        round=1,
        mode="live",
        observed_at=NOW,
        cutoff_at=NOW + timedelta(minutes=4),
        evidence_ref=evidence_ref,
        content_ref=content_ref,
    )


def _runtime(tmp_path: Path, *, vary_fetch_receipts: bool = False):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'readback.sqlite3'}")
    database.create_all()
    run_id, session_id = _reserve(database, "event-document-readback", "a" * 64)
    store = ContentAddressedDocumentStore(tmp_path / "documents")
    evidence = ResearchEvidenceService(database)
    body = (
        "Baseline section: the prior stance remained restrictive.\n\n"
        + ("Earlier background with no later-condition terms.\n\n" * 180)
        + "Later qualification: inflation reversal is a condition, not a commitment.\n"
        + "The speaker would reassess only after additional labor-market evidence."
    )

    fetch_count = 0

    async def fetcher(url: str) -> FetchedDocument:
        nonlocal fetch_count
        fetch_count += 1
        return FetchedDocument(
            source_url=url,
            text=body,
            published_at=NOW - timedelta(hours=1),
            observed_at=NOW + timedelta(seconds=1),
            received_at=NOW + timedelta(
                seconds=2, microseconds=fetch_count if vary_fetch_receipts else 0
            ),
        )

    web = HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        fetcher=fetcher,
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
        document_store=store,
    )
    readback = DocumentReadbackResearchAdapter(
        evidence,
        store,
        clock=lambda: NOW + timedelta(seconds=3),
    )
    manifests = [
        item
        for item in load_capability_manifests()
        if item.capability_id in {"web.fetch", "document.readback"}
    ]
    inner = ResearchCapabilityGatewayService(
        manifests,
        [web, readback],
        enabled_capabilities=["web.fetch", "document.readback"],
        clock=lambda: NOW + timedelta(seconds=3),
    )
    gateway = DurableResearchCapabilityGateway(
        inner,
        DshSessionLinkService(database, clock=lambda: NOW + timedelta(seconds=3)),
        evidence,
        ResearchObservabilityService(database, clock=lambda: NOW + timedelta(seconds=3)),
        requirements={"event_identity": _requirement()},
        clock=lambda: NOW + timedelta(seconds=3),
    )
    return database, run_id, session_id, store, gateway, body


@pytest.mark.asyncio
async def test_repeated_fetch_keeps_distinct_original_receipts_and_readback_identity(
    tmp_path: Path,
) -> None:
    database, run_id, session_id, store, gateway, _ = _runtime(
        tmp_path, vary_fetch_receipts=True
    )
    query = _query(
        session_id,
        request_id="fetch-original-1",
        capability_id="web.fetch",
        query="prior stance restrictive baseline",
        target_url="https://www.federalreserve.gov/newsevents/speech/waller-test.htm",
    )
    first = (await gateway.execute(query)).evidence_candidates[0]
    second = (
        await gateway.execute(query.model_copy(update={"request_id": "fetch-original-2"}))
    ).evidence_candidates[0]
    assert first.content_hash == second.content_hash
    assert first.content_ref != second.content_ref
    assert first.evidence_id != second.evidence_id
    assert store.load(first.content_ref).received_at < store.load(second.content_ref).received_at
    for index, original in enumerate((first, second)):
        reread = await gateway.execute(
            _query(
                session_id,
                request_id=f"read-original-{index}",
                capability_id="document.readback",
                query=query.query,
                evidence_ref=original.evidence_id,
                content_ref=original.content_ref,
            )
        )
        assert reread.evidence_candidates[0].evidence_id == original.evidence_id
    # A transport retry of the same call reuses the original result, not a new fetch.
    assert (await gateway.execute(query)).evidence_candidates[0] == first
    assert len(ResearchEvidenceService(database).list_run_evidence(run_id)) == 2
    # Existing identity checks must still reject a forged replacement archive.
    with pytest.raises(ValueError, match="research_evidence_identity_conflict"):
        ResearchEvidenceService(database).accept_candidates(
            run_id=run_id,
            capability_id="web.fetch",
            candidates=[first.model_copy(update={"content_ref": second.content_ref})],
            requirements={"event_identity": _requirement()},
            cutoff_at=NOW + timedelta(seconds=4),
        )


@pytest.mark.asyncio
async def test_same_gateway_re_reads_later_passage_from_immutable_original(
    tmp_path: Path,
) -> None:
    database, run_id, session_id, store, gateway, body = _runtime(tmp_path)
    source_url = "https://www.federalreserve.gov/newsevents/speech/waller-test.htm"

    fetched = await gateway.execute(
        _query(
            session_id,
            request_id="fetch-original",
            capability_id="web.fetch",
            query="prior stance restrictive baseline",
            target_url=source_url,
        )
    )
    original = fetched.evidence_candidates[0]
    assert original.content_ref is not None
    assert original.source_content_hash is not None
    assert original.parser_ref == "http-document-main-text.v2+publication-time.v1+official-event.v1"
    assert str(tmp_path) not in original.content_ref
    assert store.load(original.content_ref).text == body

    reread = await gateway.execute(
        _query(
            session_id,
            request_id="read-later-qualification",
            capability_id="document.readback",
            query="later qualification inflation reversal commitment labor-market",
            evidence_ref=original.evidence_id,
            content_ref=original.content_ref,
        )
    )

    selected = reread.evidence_candidates[0]
    assert "inflation reversal is a condition" in selected.excerpt
    assert "additional labor-market evidence" in selected.excerpt
    assert selected.content_ref == original.content_ref
    assert selected.evidence_id != original.evidence_id
    assert len(ResearchEvidenceService(database).list_run_evidence(run_id)) == 2


@pytest.mark.asyncio
async def test_readback_rejects_cross_run_ref_mismatch_and_tampered_asset(
    tmp_path: Path,
) -> None:
    database, _run_id, session_id, store, gateway, _body = _runtime(tmp_path)
    fetched = await gateway.execute(
        _query(
            session_id,
            request_id="fetch-for-negative-cases",
            capability_id="web.fetch",
            query="baseline",
            target_url="https://www.federalreserve.gov/newsevents/speech/waller-test.htm",
        )
    )
    original = fetched.evidence_candidates[0]
    assert original.content_ref is not None

    _other_run, other_session = _reserve(database, "event-other-run", "d" * 64)
    with pytest.raises(ResearchCapabilityError) as cross_run:
        await gateway.execute(
            _query(
                other_session,
                request_id="cross-run-read",
                capability_id="document.readback",
                query="qualification",
                evidence_ref=original.evidence_id,
            )
        )
    assert cross_run.value.error_code == "research_evidence_scope_denied"

    with pytest.raises(ResearchCapabilityError) as mismatch:
        await gateway.execute(
            _query(
                session_id,
                request_id="mismatched-content-ref",
                capability_id="document.readback",
                query="qualification",
                evidence_ref=original.evidence_id,
                content_ref="document://sha256/" + "0" * 64,
            )
        )
    assert mismatch.value.error_code == "research_document_content_ref_mismatch"

    digest = original.content_ref.rsplit("/", 1)[-1]
    (store.root / f"{digest}.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(ResearchCapabilityError) as tampered:
        await gateway.execute(
            _query(
                session_id,
                request_id="tampered-content",
                capability_id="document.readback",
                query="qualification",
                evidence_ref=original.evidence_id,
            )
        )
    assert tampered.value.error_code == "research_document_asset_hash_mismatch"
