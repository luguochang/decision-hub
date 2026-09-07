"""Run the bounded DSH-native Search -> Fetch -> Gateway canary.

The command is intentionally isolated from the product database. It creates a
temporary Run and DSH session link, so a provider experiment cannot mutate a
user's ledger or active pointer. Search remains a discovery candidate; only the
audited Fetch/Official adapters are allowed to close the event identity fact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import AnyUrl, TypeAdapter

from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    ObservationCreate,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.official_sources import OfficialDocumentResearchAdapter
from packages.provider_adapters.research import CryptoMacroFactPack, HttpDocumentResearchAdapter
from packages.provider_adapters.research.web_search import WebSearchResearchAdapter
from packages.provider_adapters.search.dsh_native import DshNativeWebSearchTransport
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)

ROOT = Path(__file__).resolve().parents[2]
CANARY_QUERY = "latest Federal Reserve official speech or statement September 2026"
CANARY_DOMAIN = "federalreserve.gov"


def _manifests() -> list[ResearchCapabilityManifest]:
    payload = yaml.safe_load(
        (ROOT / "packs/crypto_macro/tools/bindings.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        raise RuntimeError("research_capability_bindings_invalid")
    return [ResearchCapabilityManifest.model_validate(item) for item in payload["capabilities"]]


def _query(
    *,
    request_id: str,
    capability_id: str,
    requirement_id: str,
    session_id: str,
    now: datetime,
    target_url: str | None = None,
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id=request_id,
        capability_id=capability_id,
        requirement_id=requirement_id,
        query=(
            CANARY_QUERY
            if target_url is None
            else "fetch the selected official Federal Reserve source"
        ),
        target_url=(TypeAdapter(AnyUrl).validate_python(target_url) if target_url else None),
        symbols=[],
        fields=[],
        allowed_domains=[CANARY_DOMAIN],
        max_results=3,
        max_cost_usd=0.05,
        research_session_id=session_id,
        round=1,
        mode="live",
        observed_at=now,
        cutoff_at=now + timedelta(seconds=120),
    )


def _error_payload(exc: BaseException) -> dict[str, object]:
    return {
        "error_code": getattr(exc, "error_code", type(exc).__name__),
        "origin": getattr(exc, "origin", "canary"),
        "cause_code": getattr(exc, "cause_code", None),
        "retryable": bool(getattr(exc, "retryable", False)),
    }


async def _run() -> dict[str, object]:
    if os.getenv("DECISION_HUB_G2AF_CANARY") != "1":
        raise RuntimeError("set DECISION_HUB_G2AF_CANARY=1 to authorize external read-only use")

    started_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="decision-hub-g2af-search-canary-") as raw_temp:
        temp = Path(raw_temp)
        database = Database(url=f"sqlite+pysqlite:///{temp / 'canary.sqlite3'}")
        database.create_all()

        # Establish the same durable identity boundary used by DSH Web, but in
        # a throwaway database. No production Run, JSONL or active pointer can
        # be changed by this canary.
        now = datetime.now(UTC)
        observation = ObservationCreate(
            text="G2-AF native Search attestation canary for an official Fed source.",
            source_id="g2af-search-canary",
            source_type=SourceType.manual,
            language="en",
            observed_at=now - timedelta(seconds=2),
            published_at=now - timedelta(seconds=2),
            event_hint="central_bank_speech",
        )
        event_id, _envelope, _admitted = AdmissionService(database).admit(observation)
        run_id, _created = RunService(database).create(
            event_id,
            "g2af-search-attestation-canary-20260904",
            strategy_version="research.v1",
            admission_origin="manual",
        )
        SnapshotService(database).freeze_for_run(run_id, event_id)
        RunService(database).claim_for_execution(run_id)

        request_hash = hashlib.sha256(f"{run_id}:g2af-search".encode()).hexdigest()
        session_id, request_id = DshSessionLinkService.deterministic_ids(
            run_id, request_hash, 1
        )
        submit = DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=request_hash,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=now + timedelta(seconds=120),
            model_step_timeout_ms=20_000,
            max_tool_calls=24,
            generation=1,
        )
        links = DshSessionLinkService(database)
        links.reserve(
            submit,
            DshUpstreamIdentity(
                source_commit="0" * 40,
                source_version="canary",
                package_versions={"dsh": "canary"},
                plugin_build_hash="0" * 64,
            ),
        )

        fact_pack = CryptoMacroFactPack.from_pack(ROOT / "packs/crypto_macro")
        requirements = {
            item.requirement_id: item for item in fact_pack.all_contract_requirements()
        }
        inner = ResearchCapabilityGatewayService(
            _manifests(),
            [
                WebSearchResearchAdapter(
                    DshNativeWebSearchTransport.from_env(), capability_id="web.search"
                ),
                HttpDocumentResearchAdapter(
                    capability_id="web.fetch",
                    kind="web",
                    authority="verified_web",
                    source_id="web-fetch",
                ),
                OfficialDocumentResearchAdapter(),
            ],
            enabled_capabilities=("web.search", "web.fetch", "official.macro"),
        )
        evidence_service = ResearchEvidenceService(database)
        observability = ResearchObservabilityService(database)
        gateway = DurableResearchCapabilityGateway(
            inner,
            links,
            evidence_service,
            observability,
            requirements=requirements,
        )

        failures: list[dict[str, object]] = []
        search_result: Any | None = None
        try:
            search_result = await gateway.execute(
                _query(
                    request_id="g2af-native-search",
                    capability_id="web.search",
                    requirement_id="event_identity",
                    session_id=session_id,
                    now=now,
                )
            )
        except (ResearchCapabilityError, ValueError) as exc:
            failures.append({"stage": "search", **_error_payload(exc)})

        fetched = None
        official = None
        locators: list[dict[str, object]] = []
        if search_result is not None:
            for item in search_result.evidence_candidates:
                locators.append(
                    {
                        "title": item.excerpt[:160],
                        "source_domain": CANARY_DOMAIN,
                        "url": str(item.source_url),
                        "content_hash": item.content_hash,
                    }
                )
            if search_result.evidence_candidates:
                target = search_result.evidence_candidates[0].source_url
                try:
                    fetched = await gateway.execute(
                        _query(
                            request_id="g2af-native-fetch",
                            capability_id="web.fetch",
                            requirement_id="event_identity",
                            session_id=session_id,
                            now=now,
                            target_url=str(target),
                        )
                    )
                except (ResearchCapabilityError, ValueError) as exc:
                    failures.append({"stage": "fetch", **_error_payload(exc)})
                try:
                    official = await gateway.execute(
                        _query(
                            request_id="g2af-native-official",
                            capability_id="official.macro",
                            requirement_id="event_identity",
                            session_id=session_id,
                            now=now,
                            target_url=str(target),
                        )
                    )
                except (ResearchCapabilityError, ValueError) as exc:
                    failures.append({"stage": "official", **_error_payload(exc)})

        ledger = evidence_service.list_run_evidence(run_id)
        coverage = assess_evidence_sufficiency(
            requirements.values(), ledger, cutoff_at=now + timedelta(seconds=120)
        )
        trace = observability.list_trace(run_id)
        accepted_official = [
            item
            for item in ledger
            if item.authority == "official"
            and item.quality == "accepted"
            and item.requirement_id == "event_identity"
        ]
        status = (
            "passed"
            if search_result is not None
            and bool(search_result.evidence_candidates)
            and fetched is not None
            and official is not None
            and bool(accepted_official)
            and coverage.status == "insufficient"  # other critical facts are intentionally absent
            and "event_identity" in coverage.covered_requirement_ids
            and len(trace) >= 6
            else "failed"
        )
        return {
            "schema_version": "g2af-search-attestation-canary.v1",
            "status": status,
            "scope": "temporary_run_dsh_native_search_fetch_gateway",
            "provider": "dsh-native-web-search",
            "model": os.getenv("DECISION_HUB_DSH_SEARCH_MODEL", "deepseek-v4-flash"),
            "run_id": run_id,
            "dsh_session_id": session_id,
            "search_candidates": len(search_result.evidence_candidates) if search_result else 0,
            "fetch_evidence": len(fetched.evidence_candidates) if fetched else 0,
            "official_evidence": len(official.evidence_candidates) if official else 0,
            "ledger_evidence": len(ledger),
            "accepted_official_event_identity": len(accepted_official),
            "event_identity_covered": "event_identity" in coverage.covered_requirement_ids,
            "hard_coverage_ratio": coverage.hard_coverage_ratio,
            "trace_events": len(trace),
            "cost_usd": sum(
                value
                for value in (
                    search_result.cost_usd if search_result else None,
                    fetched.cost_usd if fetched else None,
                    official.cost_usd if official else None,
                )
                if value is not None
            ),
            "latency_ms": round((datetime.now(UTC) - started_at).total_seconds() * 1000),
            "locators": locators,
            "failures": failures,
            "remaining_hard_gaps": [
                item.requirement_id
                for item in coverage.gaps
                if item.importance == "hard"
            ],
            "note": (
                "This canary proves native Search -> Hub Gateway attestation for event identity "
                "only; it does not claim full crypto_macro sufficiency or product-ready trading "
                "output."
            ),
        }


def main() -> int:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
