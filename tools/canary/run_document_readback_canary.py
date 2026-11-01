"""Run one real official-document fetch and a same-Gateway full-text readback."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    HttpDocumentResearchAdapter,
    ResearchSourceRegistry,
)
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://www.federalreserve.gov/newsevents/speech/waller20260903a.htm"


def _requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id="event.identity",
        description="Identify and verify the official event, speaker and document.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["official.macro"],
        freshness_seconds=604800,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.45,
    )


def _query(
    *,
    session_id: str,
    request_id: str,
    query: str,
    now: datetime,
    target_url: str | None = None,
    evidence_ref: str | None = None,
    content_ref: str | None = None,
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id=request_id,
        capability_id="web.fetch" if target_url else "document.readback",
        requirement_id="event.identity",
        query=query,
        target_url=AnyUrl(target_url) if target_url else None,
        symbols=[],
        fields=[],
        allowed_domains=["federalreserve.gov"] if target_url else [],
        max_results=1,
        max_cost_usd=0.0,
        research_session_id=session_id,
        round=1,
        mode="live",
        observed_at=now,
        cutoff_at=now + timedelta(minutes=5),
        evidence_ref=evidence_ref,
        content_ref=content_ref,
    )


async def run(url: str) -> dict[str, object]:
    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="decision-hub-readback-") as raw:
        database = Database(f"sqlite+pysqlite:///{Path(raw) / 'canary.sqlite3'}")
        database.create_all()
        try:
            run_id, _created = RunService(database, clock=lambda: now).create(
                "document-readback-canary",
                idempotency_key=f"readback:{hashlib.sha256(url.encode()).hexdigest()[:16]}",
                strategy_version="research.v1",
            )
            request_hash = hashlib.sha256(f"{run_id}:{url}".encode()).hexdigest()
            session_id, request_id = DshSessionLinkService.deterministic_ids(
                run_id, request_hash, 1
            )
            DshSessionLinkService(database, clock=lambda: now).reserve(
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
                    deadline_at=now + timedelta(minutes=5),
                    model_step_timeout_ms=150_000,
                    max_tool_calls=4,
                    generation=1,
                ),
                DshUpstreamIdentity(
                    source_commit="c" * 40,
                    source_version="0.1.2",
                    package_versions={"dsh": "0.1.2"},
                    plugin_build_hash="b" * 64,
                ),
            )
            store = ContentAddressedDocumentStore(Path(raw) / "documents")
            evidence = ResearchEvidenceService(database)
            registry = ResearchSourceRegistry.from_pack(ROOT / "packs" / "crypto_macro")
            official = HttpDocumentResearchAdapter(
                capability_id="web.fetch",
                kind="web",
                authority="verified_web",
                source_id="web-fetch",
                source_registry=registry,
                document_store=store,
            )
            def live_clock() -> datetime:
                return datetime.now(UTC)
            readback = DocumentReadbackResearchAdapter(evidence, store, clock=live_clock)
            manifests = [
                item
                for item in load_capability_manifests()
                if item.capability_id in {"web.fetch", "document.readback"}
            ]
            inner = ResearchCapabilityGatewayService(
                manifests,
                [official, readback],
                enabled_capabilities=["web.fetch", "document.readback"],
                clock=live_clock,
            )
            gateway = DurableResearchCapabilityGateway(
                inner,
                DshSessionLinkService(database, clock=live_clock),
                evidence,
                ResearchObservabilityService(database, clock=live_clock),
                requirements={"event.identity": _requirement()},
                clock=live_clock,
            )
            first = await gateway.execute(
                _query(
                    session_id=session_id,
                    request_id="fetch-waller",
                    query="Waller 2026-09-03",
                    target_url=url,
                    now=now,
                )
            )
            original = first.evidence_candidates[0]
            if original.content_ref is None:
                raise RuntimeError("real_document_did_not_archive_full_text")
            second = await gateway.execute(
                _query(
                    session_id=session_id,
                    request_id="readback-waller-later-qualification",
                    query="not a commitment conditions labor market evidence",
                    evidence_ref=original.evidence_id,
                    content_ref=original.content_ref,
                    now=now,
                )
            )
            reread = second.evidence_candidates[0]
            if reread.evidence_id == original.evidence_id or reread.excerpt == original.excerpt:
                raise RuntimeError("real_document_readback_did_not_change_excerpt")
            return {
                "status": "passed",
                "url": url,
                "run_id": run_id,
                "evidence_count": len(evidence.list_run_evidence(run_id)),
                "content_ref": original.content_ref,
                "source_content_hash": original.source_content_hash,
                "original_excerpt_chars": len(original.excerpt),
                "readback_excerpt_chars": len(reread.excerpt),
                "readback_contains_condition": "condition" in reread.excerpt.lower(),
            }
        finally:
            database.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args.url)), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": getattr(exc, "error_code", type(exc).__name__),
                    "cause_code": getattr(exc, "cause_code", None),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
