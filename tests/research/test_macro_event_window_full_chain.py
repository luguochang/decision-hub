from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.research_mcp.main import load_capability_manifests
from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceRequirement,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.decision.sufficiency import assess_evidence_sufficiency
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter
from packages.provider_adapters.macro_market import (
    ExpectationPricingResearchAdapter,
    IntradayMacroResearchAdapter,
)
from packages.provider_adapters.research import CryptoMacroFactPack
from packages.provider_adapters.routing import ProviderCapabilityRouter
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)

ROOT = Path(__file__).resolve().parents[2]
EVENT_AT = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
NOW = EVENT_AT + timedelta(minutes=2)
OFFSETS = ("t-5m", "t+1m")


def _requirement(requirement_id: str) -> EvidenceRequirement:
    requirement = CryptoMacroFactPack.from_pack(
        ROOT / "packs" / "crypto_macro"
    ).contract_requirement(requirement_id)
    assert requirement is not None
    return requirement


def _manifest(
    capability_id: str,
    provider_id: str,
    *,
    allowed_domains: list[str],
) -> ResearchCapabilityManifest:
    return ResearchCapabilityManifest.model_validate(
        {
            "schema_version": "research-capability-manifest.v1",
            "capability_id": capability_id,
            "version": "1.0.0-test",
            "kind": "provider",
            "implementation_ref": f"adapter://test/{provider_id}",
            "input_schema_ref": "research-capability-query.v1",
            "output_schema_ref": "research-capability-result.v1",
            "permissions": ["network:https"],
            "allowed_domains": allowed_domains,
            "timeout_seconds": 5,
            "cost_policy_ref": "test-known-free.v1",
            "freshness_policy_ref": "crypto_macro.v1",
            "license_status": "approved",
            "audit_status": "approved",
            "replay_policy": "archive_required",
            "secret_policy": "none",
            "provider_routes": [
                {
                    "provider_id": provider_id,
                    "adapter_ref": f"adapter://test/{provider_id}",
                    "route_role": "primary",
                    "priority": 10,
                    "service_tier": "licensed_live",
                    "allowed_domains": allowed_domains,
                    "timeout_seconds": 5,
                    "cost_policy_ref": "test-known-free.v1",
                    "license_status": "approved",
                    "audit_status": "approved",
                    "requires_event_window": True,
                }
            ],
        }
    )


def _setup(tmp_path: Path, requirement: EvidenceRequirement):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'macro-chain.sqlite3'}")
    database.create_all()
    run_id, _ = RunService(database, clock=lambda: NOW).create("event-macro-chain")
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
            deadline_at=NOW + timedelta(minutes=5),
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
    watches = EventWatchService(database, clock=lambda: NOW)
    watches.ensure_watch(
        event_id="event-macro-chain",
        source_id="official-calendar",
        event_family="central_bank_speech",
        scheduled_at=EVENT_AT,
        window_offsets=OFFSETS,
    )
    return database, run_id, session_id, watches, {requirement.requirement_id: requirement}


async def _execute(
    tmp_path: Path,
    *,
    requirement_id: str,
    capability_id: str,
    provider_id: str,
    adapter: ResearchCapabilityAdapter,
    allowed_domains: list[str],
    symbols: list[str],
    fields: list[str],
):
    requirement = _requirement(requirement_id)
    database, run_id, session_id, watches, requirements = _setup(tmp_path, requirement)
    manifest = _manifest(capability_id, provider_id, allowed_domains=allowed_domains)
    assert manifest.provider_routes
    router = ProviderCapabilityRouter(
        capability_id=capability_id,
        routes=manifest.provider_routes,
        adapters={provider_id: adapter},
        clock=lambda: NOW,
    )
    gateway = DurableResearchCapabilityGateway(
        ResearchCapabilityGatewayService(
            [manifest],
            [router],
            enabled_capabilities=[capability_id],
            clock=lambda: NOW,
        ),
        DshSessionLinkService(database, clock=lambda: NOW),
        ResearchEvidenceService(database),
        ResearchObservabilityService(database, clock=lambda: NOW),
        requirements=requirements,
        event_watches=watches,
        clock=lambda: NOW,
    )
    result = await gateway.execute(
        ResearchCapabilityQuery(
            schema_version="research-capability-query.v1",
            request_id=f"call-{capability_id}",
            capability_id=capability_id,
            requirement_id=requirement_id,
            query="event-relative macro fixture",
            target_url=None,
            symbols=symbols,
            fields=fields,
            allowed_domains=[],
            max_results=20,
            max_cost_usd=1.0,
            research_session_id=session_id,
            round=1,
            mode="live",
            observed_at=NOW,
            cutoff_at=NOW,
            event_id="event-macro-chain",
            requested_event_offsets=list(OFFSETS),
        )
    )
    evidence = ResearchEvidenceService(database).list_run_evidence(run_id)
    facts = ResearchFactStore(database).list_run_facts(run_id)
    coverage = assess_evidence_sufficiency([requirement], evidence, facts=facts, cutoff_at=NOW)
    return result, evidence, facts, coverage


def _macro_fetcher(*, missing_baseline: bool = False):
    async def fetch(url: str) -> Mapping[str, object]:
        symbol = parse_qs(urlsplit(url).query)["symbol"][0]
        is_rate = symbol == "US2Y"
        source_id = "rates-feed" if is_rate else "usd-feed"
        source_url = "https://rates.example/us2y" if is_rate else "https://usd.example/dxy"
        points = [
            {
                "timestamp": (EVENT_AT - timedelta(minutes=5)).isoformat(),
                "event_offset": "t-5m",
                "source_id": source_id,
                "independence_group": source_id,
                "source_url": source_url,
                "values": {
                    "level": "4.25" if is_rate else "103.1",
                    "event_return": "0",
                },
            },
            {
                "timestamp": (EVENT_AT + timedelta(minutes=1)).isoformat(),
                "event_offset": "t+1m",
                "source_id": source_id,
                "independence_group": source_id,
                "source_url": source_url,
                "values": {
                    "level": "4.31" if is_rate else "103.4",
                    "event_return": "6" if is_rate else "0.291",
                },
            },
        ]
        return {"data": points[1:] if missing_baseline else points}

    return fetch


def _expectation_fetcher(*, missing_baseline: bool = False):
    async def fetch(_url: str) -> Mapping[str, object]:
        points = [
            {
                "timestamp": (EVENT_AT - timedelta(minutes=5)).isoformat(),
                "event_offset": "t-5m",
                "values": {"level": "0.34", "delta": "0"},
            },
            {
                "timestamp": (EVENT_AT + timedelta(minutes=1)).isoformat(),
                "event_offset": "t+1m",
                "values": {"level": "0.41", "delta": "0.07"},
            },
        ]
        return {"data": points[1:] if missing_baseline else points}

    return fetch


@pytest.mark.asyncio
async def test_realtime_intraday_macro_full_chain_is_semantically_sufficient(
    tmp_path: Path,
) -> None:
    adapter = IntradayMacroResearchAdapter(
        provider_id="macro-live",
        base_url="https://gateway.example/series",
        delay_class="realtime",
        authority="exchange",
        estimated_cost_usd=0.0,
        fetcher=_macro_fetcher(),
    )
    result, evidence, facts, coverage = await _execute(
        tmp_path,
        requirement_id="macro_transmission",
        capability_id="macro.cross_asset_intraday",
        provider_id="macro-live",
        adapter=adapter,
        allowed_domains=["gateway.example", "rates.example", "usd.example"],
        symbols=["US2Y", "DXY"],
        fields=["level", "event_return"],
    )

    assert result.provider == "macro-live"
    assert len(evidence) == 4
    assert {fact.metric_family for fact in facts} == {"macro.rates", "macro.usd"}
    assert {fact.event_offset for fact in facts} == set(OFFSETS)
    assert coverage.status == "sufficient"


@pytest.mark.asyncio
async def test_delayed_intraday_macro_full_chain_stays_research_only(tmp_path: Path) -> None:
    adapter = IntradayMacroResearchAdapter(
        provider_id="macro-proxy",
        base_url="https://gateway.example/series",
        delay_class="delayed",
        authority="exchange",
        estimated_cost_usd=0.0,
        fetcher=_macro_fetcher(),
    )
    _result, _evidence, _facts, coverage = await _execute(
        tmp_path,
        requirement_id="macro_transmission",
        capability_id="macro.cross_asset_intraday",
        provider_id="macro-proxy",
        adapter=adapter,
        allowed_domains=["gateway.example", "rates.example", "usd.example"],
        symbols=["US2Y", "DXY"],
        fields=["level", "event_return"],
    )

    assert coverage.status == "insufficient"
    assert coverage.gaps[0].reason_code == "semantic_mismatch"


@pytest.mark.asyncio
async def test_realtime_expectation_full_chain_is_semantically_sufficient(tmp_path: Path) -> None:
    adapter = ExpectationPricingResearchAdapter(
        provider_id="expectation-live",
        base_url="https://pricing.example/fed",
        delay_class="realtime",
        authority="exchange",
        estimated_cost_usd=0.0,
        fetcher=_expectation_fetcher(),
    )
    _result, _evidence, facts, coverage = await _execute(
        tmp_path,
        requirement_id="expectation_pricing",
        capability_id="macro.expectation_pricing",
        provider_id="expectation-live",
        adapter=adapter,
        allowed_domains=["pricing.example"],
        symbols=["SEP26"],
        fields=["level", "delta"],
    )

    assert {fact.metric_family for fact in facts} == {"macro.policy_expectation"}
    assert {fact.event_offset for fact in facts} == set(OFFSETS)
    assert coverage.status == "sufficient"


@pytest.mark.asyncio
async def test_expectation_missing_baseline_is_not_replaced_by_current_data(tmp_path: Path) -> None:
    adapter = ExpectationPricingResearchAdapter(
        provider_id="expectation-live",
        base_url="https://pricing.example/fed",
        delay_class="realtime",
        authority="exchange",
        estimated_cost_usd=0.0,
        fetcher=_expectation_fetcher(missing_baseline=True),
    )
    _result, _evidence, _facts, coverage = await _execute(
        tmp_path,
        requirement_id="expectation_pricing",
        capability_id="macro.expectation_pricing",
        provider_id="expectation-live",
        adapter=adapter,
        allowed_domains=["pricing.example"],
        symbols=["SEP26"],
        fields=["level", "delta"],
    )

    assert coverage.status == "insufficient"
    assert coverage.gaps[0].reason_code == "no_baseline"


@pytest.mark.asyncio
async def test_wrong_macro_family_cannot_substitute_for_expectation_pricing(
    tmp_path: Path,
) -> None:
    adapter = IntradayMacroResearchAdapter(
        provider_id="macro-live",
        base_url="https://gateway.example/series",
        delay_class="realtime",
        authority="exchange",
        estimated_cost_usd=0.0,
        fetcher=_macro_fetcher(),
    )
    _result, _evidence, _facts, coverage = await _execute(
        tmp_path,
        requirement_id="expectation_pricing",
        capability_id="macro.cross_asset_intraday",
        provider_id="macro-live",
        adapter=adapter,
        allowed_domains=["gateway.example", "rates.example", "usd.example"],
        symbols=["US2Y", "DXY"],
        fields=["level", "event_return"],
    )

    assert coverage.status == "insufficient"
    assert coverage.gaps[0].reason_code == "semantic_mismatch"


@pytest.mark.asyncio
async def test_unapproved_production_macro_capabilities_stop_before_provider_call() -> None:
    manifests = {
        item.capability_id: item
        for item in load_capability_manifests()
        if item.capability_id
        in {
            "macro.cross_asset_intraday",
            "macro.expectation_pricing",
        }
    }
    for capability_id, manifest in manifests.items():
        adapter = (
            IntradayMacroResearchAdapter(
                provider_id="unconfigured",
                base_url=None,
            )
            if capability_id == "macro.cross_asset_intraday"
            else ExpectationPricingResearchAdapter(
                provider_id="unconfigured",
                base_url=None,
            )
        )
        gateway = ResearchCapabilityGatewayService(
            [manifest],
            [adapter],
            enabled_capabilities=[capability_id],
            clock=lambda: NOW,
        )
        with pytest.raises(ResearchCapabilityError) as raised:
            await gateway.execute(
                ResearchCapabilityQuery(
                    schema_version="research-capability-query.v1",
                    request_id=f"blocked-{capability_id}",
                    capability_id=capability_id,
                    requirement_id=(
                        "macro_transmission"
                        if capability_id == "macro.cross_asset_intraday"
                        else "expectation_pricing"
                    ),
                    query="must stop before adapter",
                    target_url=None,
                    symbols=["US2Y"],
                    fields=["level"],
                    allowed_domains=[],
                    max_results=10,
                    max_cost_usd=1.0,
                    research_session_id="blocked-session",
                    round=1,
                    mode="live",
                    observed_at=NOW,
                    cutoff_at=NOW,
                )
            )
        assert raised.value.error_code == "research_capability_not_audited"
