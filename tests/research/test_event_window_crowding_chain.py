from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    CryptoEventWindowObservation,
    CryptoEventWindowPayload,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EventWindowCapture,
    EvidenceRequirement,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.decision.sufficiency import assess_evidence_sufficiency
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.market.event_window import (
    CryptoEventWindowArchive,
    CryptoEventWindowResearchAdapter,
)
from packages.provider_adapters.routing import ProviderCapabilityRouter
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _manifest() -> ResearchCapabilityManifest:
    return ResearchCapabilityManifest.model_validate(
        {
            "schema_version": "research-capability-manifest.v1",
            "capability_id": "market.crypto_derivatives",
            "version": "1.1.0",
            "kind": "provider",
            "implementation_ref": "adapter://provider/market/crypto-multi-venue",
            "input_schema_ref": "research-capability-query.v1",
            "output_schema_ref": "research-capability-result.v1",
            "permissions": ["network:https"],
            "allowed_domains": ["okx.com"],
            "timeout_seconds": 20,
            "cost_policy_ref": "test.v1",
            "freshness_policy_ref": "test.v1",
            "license_status": "approved",
            "audit_status": "approved",
            "replay_policy": "archive_required",
            "secret_policy": "none",
            "provider_routes": [
                {
                    "provider_id": "event-window-archive",
                    "adapter_ref": "adapter://provider/market/event-window-archive",
                    "route_role": "primary",
                    "priority": 0,
                    "service_tier": "replay",
                    "allowed_domains": [],
                    "timeout_seconds": 5,
                    "cost_policy_ref": "no-external-cost.v1",
                    "license_status": "approved",
                    "audit_status": "approved",
                    "requires_event_window": True,
                }
            ],
        }
    )


def _requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id="derivatives_crowding",
        description="Confirm event-relative derivatives crowding.",
        importance="hard",
        source_priority=["exchange"],
        authority_floor="exchange",
        preferred_capabilities=["market.crypto_derivatives"],
        freshness_seconds=600,
        minimum_independent_sources=1,
        allowed_fallbacks=["market.crypto_crowding"],
        confidence_cap=0.5,
        accepted_metric_families=["crypto.derivatives"],
        required_metric_families=["crypto.derivatives"],
        required_fields=[
            "funding_rate",
            "open_interest",
            "open_interest_delta",
            "basis",
            "crowding_signal",
        ],
        required_event_offsets=["t-5m", "t+1m"],
        field_units={
            "funding_rate": ["rate"],
            "open_interest": ["btc"],
            "open_interest_delta": ["percent"],
            "basis": ["rate"],
            "crowding_signal": ["ratio"],
        },
        minimum_venues=1,
        venue_required=True,
        minimum_independence_groups=1,
        allowed_delay_classes=["realtime"],
        semantic_policy_ref="crypto_macro.fact_semantics.v1#derivatives_crowding",
    )


def _observation(field: str, value: str) -> CryptoEventWindowObservation:
    unit = {
        "funding_rate": "rate",
        "open_interest": "btc",
        "basis": "rate",
        "crowding_signal": "ratio",
    }[field]
    return CryptoEventWindowObservation(
        provider_id="okx-orderbook-public",
        source_id="okx-derivatives-window",
        venue="okx",
        metric_family="crypto.derivatives",
        field=field,
        value=value,
        unit=unit,
        source_url=AnyUrl("https://www.okx.com/api/v5/market/books"),
        published_at=None,
    )


def _query(
    session_id: str, event_id: str, event_at: datetime, cutoff_at: datetime
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="crowding-window-query",
        capability_id="market.crypto_derivatives",
        requirement_id="derivatives_crowding",
        query="BTC derivatives event window",
        target_url=None,
        symbols=["BTC-USDT-SWAP"],
        fields=[
            "funding_rate",
            "open_interest",
            "open_interest_delta",
            "basis",
            "crowding_signal",
        ],
        allowed_domains=[],
        max_results=20,
        max_cost_usd=0.0,
        research_session_id=session_id,
        round=1,
        mode="replay",
        observed_at=cutoff_at,
        cutoff_at=cutoff_at,
        event_id=event_id,
        event_at=event_at,
        requested_event_offsets=["t-5m", "t+1m"],
    )


def _store_capture(
    watches: EventWatchService,
    archive: CryptoEventWindowArchive,
    sample_id: str,
    payload: CryptoEventWindowPayload,
) -> None:
    payload_ref, payload_hash = archive.write(payload)
    watches.capture(
        sample_id,
        EventWindowCapture(
            schema_version="event-window-capture.v1",
            observed_at=payload.captured_at,
            received_at=payload.captured_at,
            provider_id="crypto-window-archive",
            payload_ref=payload_ref,
            payload_hash=payload_hash,
        ),
    )


@pytest.mark.asyncio
async def test_derivatives_crowding_full_chain_requires_proxy_fact(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'crowding-chain.sqlite3'}")
    database.create_all()
    clock = [NOW]
    run_id, _ = RunService(database, clock=lambda: clock[0]).create("event-crowding-chain")
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, "c" * 64, 1)
    DshSessionLinkService(database, clock=lambda: clock[0]).reserve(
        DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash="c" * 64,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=NOW + timedelta(hours=1),
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
    watches = EventWatchService(database, clock=lambda: clock[0])
    event_at = NOW + timedelta(minutes=30)
    watch = watches.ensure_watch(
        event_id="event-crowding-chain",
        source_id="calendar",
        event_family="central_bank_speech",
        scheduled_at=event_at,
        window_offsets=("t-5m", "t+1m"),
    )
    archive = CryptoEventWindowArchive(tmp_path / "event-window-archive")
    values = {
        "t-5m": {
            "funding_rate": "0.0001",
            "open_interest": "1000",
            "basis": "0.001",
            "crowding_signal": "0.20",
        },
        "t+1m": {
            "funding_rate": "0.0002",
            "open_interest": "1100",
            "basis": "0.003",
            "crowding_signal": "0.40",
        },
    }
    for offset, fields in values.items():
        sample = next(
            item for item in watches.list_samples(watch.watch_id) if item.offset == offset
        )
        clock[0] = sample.target_at
        watches.advance()
        payload = CryptoEventWindowPayload(
            schema_version="crypto-event-window-payload.v1",
            event_id=watch.event_id,
            offset=offset,
            target_at=sample.target_at,
            captured_at=sample.target_at,
            observations=[_observation(field, value) for field, value in fields.items()],
            failures=[],
        )
        _store_capture(watches, archive, sample.sample_id, payload)

    clock[0] = NOW + timedelta(minutes=31)
    requirement = _requirement()
    adapter = CryptoEventWindowResearchAdapter(watches, archive, clock=lambda: clock[0])
    manifest = _manifest()
    assert manifest.provider_routes is not None
    route = manifest.provider_routes[0]
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_derivatives",
        routes=(route,),
        adapters={"event-window-archive": adapter},
        clock=lambda: clock[0],
    )
    inner = ResearchCapabilityGatewayService(
        [manifest],
        [router],
        enabled_capabilities=["market.crypto_derivatives"],
        clock=lambda: clock[0],
    )
    durable = DurableResearchCapabilityGateway(
        inner,
        DshSessionLinkService(database, clock=lambda: clock[0]),
        ResearchEvidenceService(database),
        ResearchObservabilityService(database, clock=lambda: clock[0]),
        requirements={"derivatives_crowding": requirement},
        event_watches=watches,
        clock=lambda: clock[0],
    )

    result = await durable.execute(_query(session_id, watch.event_id, event_at, clock[0]))
    evidence = ResearchEvidenceService(database).list_run_evidence(run_id)
    facts = ResearchFactStore(database).list_run_facts(run_id)
    coverage = assess_evidence_sufficiency([requirement], evidence, facts=facts, cutoff_at=clock[0])

    assert result.provider == "event-window-archive"
    assert {fact.field for fact in facts} == {
        "funding_rate",
        "open_interest",
        "open_interest_delta",
        "basis",
        "crowding_signal",
    }
    assert {fact.event_offset for fact in facts} >= {"t-5m", "t+1m"}
    assert coverage.status == "sufficient", {
        "coverage": coverage.model_dump(),
        "evidence": [item.model_dump() for item in evidence],
        "facts": [item.model_dump() for item in facts],
    }

    without_crowding = [fact for fact in facts if fact.field != "crowding_signal"]
    degraded = assess_evidence_sufficiency(
        [requirement], evidence, facts=without_crowding, cutoff_at=clock[0]
    )
    assert degraded.status == "insufficient"
    assert degraded.gaps[0].reason_code == "semantic_mismatch"
