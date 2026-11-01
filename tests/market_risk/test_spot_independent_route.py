"""Real SQLite/Gateway/router/adapters; controlled public bodies, no HTTP/model.

The V3 installation and minute bars are explicit engineering fixtures. This
proves legal exact-asset routing, not current public availability or prediction.
"""

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from apps.research_mcp.main import load_capability_manifests
from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityInvocation,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context_selection_v4 import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    ResearchExecutionPreparationService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.provider_adapters.market import (
    PublicSpotMarketResearchAdapter,
    parse_gate_spot,
    parse_huobi_spot,
)
from packages.provider_adapters.research.fact_pack import CryptoMacroFactPack
from packages.provider_adapters.routing import ProviderCapabilityRouter
from packages.runtime_adapters.dsh_runtime.profile import (
    PROMPT_POLICY_HASH,
    build_trusted_web_research_prompt,
)
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway
from tests.dsh_native.test_spot_prompt_asset_scope import _spot_request
from tests.market_risk.test_recipe_selection_v3 import installation_fixture, manifest
from tests.market_risk.test_spot_research_v3_integration import (
    OWNER,
    spot_component_consumer,
    spot_context_provider,
    spot_event_text,
    spot_inputs,
)
from tests.market_risk.test_spot_scope_admission import prepared
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert all(
    (
        installation_fixture,
        manifest,
        prepared,
        spot_inputs,
        spot_component_consumer,
        spot_context_provider,
        spot_event_text,
        forbid_external_connections,
    )
)
ROOT = Path(__file__).resolve().parents[2]
CAP = "market.crypto_derivatives"
REQ = "crypto_spot_confirmation"


def _pack(instrument, version="1.2.0"):
    return CryptoMacroFactPack.from_pack(
        ROOT / "packs/crypto_macro_prepared" / version, instrument=instrument
    )


def _router():
    manifest = next(item for item in load_capability_manifests() if item.capability_id == CAP)
    calls = []

    async def gate(url):
        calls.append(url)
        asset = "ETH" if "ETH_USDT" in url else "BTC"
        return {
            "payload": [{"currency_pair": f"{asset}_USDT", "last": "3000", "base_volume": "125"}]
        }

    async def huobi(url):
        calls.append(url)
        asset = "eth" if "ethusdt" in url else "btc"
        return {
            "payload": {
                "status": "ok",
                "ch": f"market.{asset}usdt.detail.merged",
                "tick": {"close": 3001, "amount": 126},
            }
        }

    adapters = {
        "gateio-public": PublicSpotMarketResearchAdapter(
            provider_id="gateio-public",
            base_url="https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT",
            parser=parse_gate_spot,
            fetcher=gate,
        ),
        "huobi-public": PublicSpotMarketResearchAdapter(
            provider_id="huobi-public",
            base_url="https://api.huobi.pro/market/detail/merged?symbol=btcusdt",
            parser=parse_huobi_spot,
            fetcher=huobi,
        ),
    }
    routes = [route for route in manifest.provider_routes if route.provider_id in adapters]
    assert len(routes) == 2 and all(route.audit_status == "approved" for route in routes)
    return (
        manifest,
        ProviderCapabilityRouter(
            capability_id=CAP,
            routes=routes,
            adapters=adapters,
            minimum_successes_by_requirement={REQ: 2},
            max_attempts_per_provider=1,
        ),
        calls,
    )


@pytest.fixture
def setup_diagnostic(tmp_path):
    observed = []

    def trace(frame, event, argument):
        if frame.f_code.co_name != "_refresh":
            return None
        if event == "exception" and not issubclass(
            argument[0], (StopIteration, StopAsyncIteration)
        ):
            observed.append(
                {
                    "line": frame.f_lineno,
                    "type": argument[0].__name__,
                    "message": str(argument[1])[:200],
                }
            )
        return trace

    sys.settrace(trace)
    try:
        yield lambda: sys.settrace(None)
    finally:
        sys.settrace(None)
        (tmp_path / "setup-errors.json").write_text(json.dumps(observed), encoding="utf-8")


@pytest.fixture
def actual_execution(setup_diagnostic, spot_inputs):
    setup_diagnostic()
    source, event_id = spot_inputs
    selected = ContextSelectionService(source.database).select(
        run_id=source.request.run_id, graph_round=1, expected_lease_owner=OWNER
    )
    digest = research_request_hash(selected.request)
    sid, rid = DshSessionLinkService.deterministic_ids(source.request.run_id, digest)
    submit = source.submit.model_copy(
        update={
            "request_hash": digest,
            "deterministic_session_id": sid,
            "deterministic_request_id": rid,
        }
    )
    with source.database.session() as session:
        bundle = read_research_bundle_in_session(session, source.request.run_id)
    prompt = (
        render_prepared_research_policy(bundle, selected.request.allowed_capabilities)
        + "\n\n"
        + build_trusted_web_research_prompt(selected.request)
    )
    source = replace(source, request=selected.request, submit=submit, prompt=prompt)
    ResearchExecutionPreparationService(source.database).prepare(
        source.request,
        submit,
        source.upstream,
        prompt,
        runtime_id="dsh",
        runtime_version=f"dsh-web-0.1.0+{source.upstream.plugin_build_hash[:12]}",
        profile_ref="decision-research.web.v1",
        prompt_policy_hash=PROMPT_POLICY_HASH,
        selection_reference=selected.reference,
        expected_lease_owner=OWNER,
    )
    yield source, event_id
    source.database.engine.dispose()


async def test_saved_eth_execution_reaches_real_gateway_router_and_two_spot_adapters(
    actual_execution,
):
    source, _ = actual_execution
    manifest, router, calls = _router()
    service = DurableResearchCapabilityGateway(
        ResearchCapabilityGatewayService([manifest], [router], enabled_capabilities=[CAP]),
        DshSessionLinkService(source.database),
        ResearchEvidenceService(source.database),
        ResearchObservabilityService(source.database),
        requirements={},
    )
    original = source.request.model_dump_json()
    intent = ResearchCapabilityInvocation(
        schema_version="research-capability-invocation.v1",
        request_id="legal-second-spot",
        capability_id=CAP,
        requirement_id=REQ,
        query="Exact ETH spot confirmation",
        target_url=None,
        symbols=["ETH-USDT"],
        fields=["spot_price", "spot_volume"],
        allowed_domains=["api.gateio.ws", "api.huobi.pro"],
        max_results=4,
        max_cost_usd=0.0,
        research_session_id=source.submit.deterministic_session_id,
    )
    result = await service.invoke_live(intent)
    assert len(calls) == 2 and "ETH_USDT" in calls[0] and "ethusdt" in calls[1]
    assert len(result.facts) == 4
    assert {fact.instrument for fact in result.facts} == {"ETH-USDT"}
    assert {fact.metric_family for fact in result.facts} == {"crypto.spot"}
    assert {fact.venue for fact in result.facts} == {"gateio", "huobi"}
    assert {fact.unit for fact in result.facts if fact.field == "volume"} == {"eth"}
    assert all(item.quality == "accepted" for item in result.evidence_candidates)
    assert {item.source_id for item in result.evidence_candidates} == {
        "gateio-public",
        "huobi-public",
    }
    for index, fields in enumerate(([], ["funding_rate"], ["spot_price", "open_interest"])):
        with pytest.raises(ResearchCapabilityError) as error:
            await service.invoke_live(
                intent.model_copy(
                    update={
                        "request_id": f"bad-spot-fields-{index}",
                        "fields": fields,
                    }
                )
            )
        assert error.value.error_code == "research_execution_binding_invalid"
        assert error.value.cause_code == "research_execution_spot_fields_invalid"
    with pytest.raises(ResearchCapabilityError) as error:
        await service.invoke_live(
            intent.model_copy(
                update={
                    "request_id": "opposite-asset",
                    "symbols": ["BTC-USDT"],
                }
            )
        )
    assert error.value.error_code == "research_task_asset_mismatch"
    assert len(calls) == 2 and source.request.model_dump_json() == original
    with source.database.session() as session:
        from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord

        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=source.request.run_id, role="research_execution_method")
            .one()
        )
        row.payload_hash = "0" * 64
    with pytest.raises(ResearchCapabilityError):
        await service.invoke_live(intent.model_copy(update={"request_id": "bad-original"}))
    assert len(calls) == 2


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
async def test_two_approved_spot_venues_return_exact_asset_and_cover_hard_slot(asset):
    _, router, calls = _router()
    now = datetime.now(UTC)
    query = ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id=f"two-spot-{asset.lower()}",
        capability_id=CAP,
        requirement_id=REQ,
        query=f"exact {asset} spot",
        target_url=None,
        symbols=[f"{asset}-USDT"],
        fields=["spot_price", "spot_volume"],
        allowed_domains=["api.gateio.ws", "api.huobi.pro"],
        research_session_id=f"two-spot-{asset.lower()}",
        round=1,
        mode="live",
        observed_at=now,
        cutoff_at=now,
        max_results=4,
        max_cost_usd=0.0,
    )

    result = await router.execute(query)

    assert len(calls) == 2
    assert f"{asset}_USDT" in calls[0]
    assert f"{asset.lower()}usdt" in calls[1]
    assert {fact.instrument for fact in result.facts} == {f"{asset}-USDT"}
    assert {fact.venue for fact in result.facts} == {"gateio", "huobi"}
    assert result.provider_attempts is not None
    assert [attempt.status for attempt in result.provider_attempts] == [
        "succeeded", "succeeded",
    ]
    requirement = next(
        item
        for item in _pack(f"{asset}-USDT").contract_requirements_for_event(
            "crypto_market_snapshot"
        )
        if item.requirement_id == REQ
    )
    from packages.kernel.decision_hub_kernel.decision.sufficiency import (
        assess_evidence_sufficiency,
    )

    coverage = assess_evidence_sufficiency(
        [requirement],
        [
            item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
            for item in result.evidence_candidates
        ],
        cutoff_at=result.completed_at,
        facts=[fact.model_copy(update={"quality": "accepted"}) for fact in result.facts],
    )
    assert coverage.covered_requirement_ids == [REQ]


async def test_second_spot_timeout_returns_first_venue_facts_and_attempt_trail():
    manifest = next(item for item in load_capability_manifests() if item.capability_id == CAP)

    async def gate(_url):
        return {
            "payload": [
                {"currency_pair": "BTC_USDT", "last": "70000", "base_volume": "125"}
            ]
        }

    async def huobi(_url):
        await asyncio.Event().wait()
        raise AssertionError("local Router deadline must cancel HTX/Huobi")

    adapters = {
        "gateio-public": PublicSpotMarketResearchAdapter(
            provider_id="gateio-public",
            base_url="https://api.gateio.ws/api/v4/spot/tickers",
            parser=parse_gate_spot,
            fetcher=gate,
        ),
        "huobi-public": PublicSpotMarketResearchAdapter(
            provider_id="huobi-public",
            base_url="https://api.huobi.pro/market/detail/merged",
            parser=parse_huobi_spot,
            fetcher=huobi,
        ),
    }
    routes = [route for route in manifest.provider_routes if route.provider_id in adapters]
    router = ProviderCapabilityRouter(
        capability_id=CAP,
        routes=routes,
        adapters=adapters,
        minimum_successes_by_requirement={REQ: 2},
    )
    now = datetime.now(UTC)
    result = await router.execute(
        ResearchCapabilityQuery(
            schema_version="research-capability-query.v1",
            request_id="partial-spot",
            capability_id=CAP,
            requirement_id=REQ,
            query="exact BTC spot",
            target_url=None,
            symbols=["BTC-USDT"],
            fields=["spot_price", "spot_volume"],
            allowed_domains=["api.gateio.ws", "api.huobi.pro"],
            research_session_id="partial-spot",
            round=1,
            mode="live",
            observed_at=now,
            cutoff_at=now,
            deadline_at=now + timedelta(milliseconds=50),
            max_results=4,
            max_cost_usd=0.0,
        )
    )

    assert result.provider == "gateio-public"
    assert {fact.venue for fact in result.facts} == {"gateio"}
    assert {fact.field for fact in result.facts} == {"price", "volume"}
    assert result.provider_attempts is not None
    assert [attempt.status for attempt in result.provider_attempts] == [
        "succeeded", "failed",
    ]
    assert result.provider_attempts[-1].error_code == "provider_timeout"


async def test_real_route_domains_and_same_venue_do_not_create_independence():
    _, router, calls = _router()
    now = datetime.now(UTC)
    query = ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="route-scope",
        capability_id=CAP,
        requirement_id=REQ,
        query="exact spot",
        target_url=None,
        symbols=["BTC-USDT"],
        fields=["spot_price", "spot_volume"],
        allowed_domains=["api.gateio.ws"],
        research_session_id="isolated-route",
        round=1,
        mode="live",
        observed_at=now,
        cutoff_at=now,
        max_results=4,
        max_cost_usd=0.0,
    )
    result = await router.execute(query)
    assert len(calls) == 1 and "BTC_USDT" in calls[0]
    requirement = next(
        item
        for item in _pack("BTC-USDT").contract_requirements_for_event("crypto_market_snapshot")
        if item.requirement_id == REQ
    )
    from packages.kernel.decision_hub_kernel.decision.sufficiency import (
        assess_evidence_sufficiency,
    )

    coverage = assess_evidence_sufficiency(
        [requirement],
        [
            item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
            for item in result.evidence_candidates
        ],
        cutoff_at=result.completed_at,
        facts=[*result.facts, *result.facts],
    )
    assert REQ not in coverage.covered_requirement_ids
    assert coverage.gaps[0].reason_code in {"insufficient_sources", "semantic_mismatch"}
    with pytest.raises(ResearchCapabilityError, match="no approved provider route"):
        await router.execute(query.model_copy(update={"allowed_domains": ["unknown.test"]}))
    assert len(calls) == 1


def test_frozen_manifest_and_prompt_allow_only_declared_own_asset_spot_fallback():
    old_raw = yaml.safe_load(
        (ROOT / "packs/crypto_macro_prepared/1.1.0/evidence/source_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    old_spot = next(
        item for item in old_raw["requirements"] if item["requirement_id"] == "crypto.spot"
    )
    assert old_spot["allowed_fallbacks"] == []
    for instrument in ("BTC-USDT", "ETH-USDT"):
        requirements = list(
            _pack(instrument).contract_requirements_for_event("crypto_market_snapshot")
        )
        requirement = next(item for item in requirements if item.requirement_id == REQ)
        assert requirement.preferred_capabilities == ["spot_market_facts"]
        assert requirement.allowed_fallbacks == [CAP]
        assert (requirement.minimum_venues, requirement.minimum_independence_groups) == (2, 2)
        request = _spot_request(instrument).model_copy(
            update={"evidence_requirements": requirements}
        )
        original = request.model_dump_json()
        prompt = build_trusted_web_research_prompt(request)
        assert "public-SPOT fallback" in prompt and f'symbols=["{instrument}"]' in prompt
        assert "a repeated venue cannot satisfy" in prompt
        assert request.model_dump_json() == original
        undeclared = requirement.model_copy(update={"allowed_fallbacks": []})
        old = request.model_copy(update={"evidence_requirements": [undeclared]})
        assert "public-SPOT fallback" not in build_trusted_web_research_prompt(old)
    old_derivatives = next(
        item for item in old_raw["requirements"] if item["requirement_id"] == "crypto.derivatives"
    )
    new_raw = yaml.safe_load(
        (ROOT / "packs/crypto_macro_prepared/1.2.0/evidence/source_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert (
        next(
            item
            for item in new_raw["requirements"]
            if item["requirement_id"] == "crypto.derivatives"
        )
        == old_derivatives
    )
