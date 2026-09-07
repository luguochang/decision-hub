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
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)

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
        requirement_id="crypto_spot_confirmation",
        description="Confirm event-relative BTC spot reaction.",
        importance="hard",
        source_priority=["exchange"],
        authority_floor="exchange",
        preferred_capabilities=["market.crypto_derivatives"],
        freshness_seconds=3600,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.8,
        accepted_metric_families=["crypto.spot"],
        required_metric_families=["crypto.spot"],
        required_fields=["price", "event_return"],
        required_event_offsets=["t-5m", "t+1m"],
        field_units={"price": ["usdt"], "event_return": ["percent"]},
        minimum_venues=1,
        venue_required=True,
        minimum_independence_groups=1,
        allowed_delay_classes=["realtime"],
    )


def _observation(field: str, value: str) -> CryptoEventWindowObservation:
    return CryptoEventWindowObservation(
        provider_id="okx-public",
        source_id="okx-window",
        venue="okx",
        metric_family="crypto.spot",
        field=field,
        value=value,
        unit="usdt",
        source_url=AnyUrl("https://okx.com/market/btc-usdt"),
        published_at=None,
    )


def _setup(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'full-chain.sqlite3'}"
    database = Database(database_url)
    database.create_all()
    run_id, _ = RunService(database, clock=lambda: NOW).create("event-full-chain")
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, "a" * 64, 1)
    DshSessionLinkService(database, clock=lambda: NOW).reserve(
        DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash="a" * 64,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=NOW + timedelta(hours=2),
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
    clock = [NOW]
    watches = EventWatchService(database, clock=lambda: clock[0])
    watch = watches.ensure_watch(
        event_id="event-full-chain",
        source_id="calendar",
        event_family="central_bank_speech",
        scheduled_at=NOW + timedelta(minutes=30),
        window_offsets=("t-5m", "t+1m"),
    )
    archive = CryptoEventWindowArchive(tmp_path / "event-window-archive")
    for offset, value in (("t-5m", "100"), ("t+1m", "110")):
        if offset == "t+1m":
            # Reopen the durable facade between slots to exercise the restart
            # boundary while retaining the same content-addressed archive.
            watches = EventWatchService(Database(database_url), clock=lambda: clock[0])
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
            observations=[_observation("price", value)],
            failures=[],
        )
        payload_ref, payload_hash = archive.write(payload)
        watches.capture(
            sample.sample_id,
            EventWindowCapture(
                schema_version="event-window-capture.v1",
                observed_at=sample.target_at,
                received_at=sample.target_at,
                provider_id="crypto-window-archive",
                payload_ref=payload_ref,
                payload_hash=payload_hash,
            ),
        )
    clock[0] = NOW + timedelta(hours=1)
    return database, run_id, session_id, watch, watches, archive, clock


@pytest.mark.asyncio
async def test_event_window_runs_through_router_gateway_fact_store_and_gate(tmp_path: Path) -> None:
    database, run_id, session_id, watch, watches, archive, clock = _setup(tmp_path)
    adapter = CryptoEventWindowResearchAdapter(watches, archive, clock=lambda: clock[0])
    manifest = _manifest()
    assert manifest.provider_routes is not None
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_derivatives",
        routes=(manifest.provider_routes[0],),
        adapters={"event-window-archive": adapter},
        clock=lambda: clock[0],
    )
    inner = ResearchCapabilityGatewayService(
        [manifest],
        [router],
        enabled_capabilities=["market.crypto_derivatives"],
        clock=lambda: clock[0],
    )
    evidence = ResearchEvidenceService(database)
    gateway = DurableResearchCapabilityGateway(
        inner,
        DshSessionLinkService(database, clock=lambda: clock[0]),
        evidence,
        ResearchObservabilityService(database, clock=lambda: clock[0]),
        requirements={"crypto_spot_confirmation": _requirement()},
        event_watches=watches,
        clock=lambda: clock[0],
    )
    result = await gateway.execute(
        ResearchCapabilityQuery(
            schema_version="research-capability-query.v1",
            request_id="full-chain-call",
            capability_id="market.crypto_derivatives",
            requirement_id="crypto_spot_confirmation",
            query="BTC event window",
            target_url=None,
            symbols=["BTC-USDT"],
            fields=["price", "event_return"],
            allowed_domains=[],
            max_results=20,
            max_cost_usd=0.0,
            research_session_id=session_id,
            round=1,
            mode="replay",
            observed_at=clock[0],
            cutoff_at=clock[0],
            event_id=watch.event_id,
            event_at=watch.scheduled_at,
            window_start_at=None,
            window_end_at=None,
            requested_event_offsets=["t-5m", "t+1m"],
        )
    )

    stored_evidence = evidence.list_run_evidence(run_id)
    stored_facts = ResearchFactStore(database).list_run_facts(run_id)
    coverage = assess_evidence_sufficiency(
        [_requirement()], stored_evidence, facts=stored_facts, cutoff_at=clock[0]
    )
    assert result.provider == "event-window-archive"
    assert {fact.field for fact in stored_facts} == {"price", "event_return"}
    assert len(stored_evidence) == 2
    assert coverage.status == "sufficient"
    assert coverage.gaps == []
