"""A prompt must not ask the model to violate its frozen gateway query scope."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceGap,
    EvidenceRequirement,
    ExecutionBudget,
    ResearchInputEvidence,
    ResearchSessionRequest,
)
from packages.runtime_adapters.dsh_runtime.profile import (
    build_research_prompt,
    build_trusted_web_research_prompt,
)

NOW = datetime(2026, 10, 3, 2, 41, 46, tzinfo=UTC)
ROUTES_PREFIX = (
    "Audited web routes declared by this frozen request (requirement_id to capability_ids): "
)
OFFICIAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
SENTIMENT_URL = "https://api.alternative.me/fng/?limit=3"


def _request(
    *,
    narrative_present: bool = False,
    preferred: tuple[str, ...] = ("web.search",),
    fallback: tuple[str, ...] = (),
    allowed_web: tuple[str, ...] = ("web.fetch",),
    narrative_gap: bool = True,
    current_round: int = 1,
) -> ResearchSessionRequest:
    requirements = [
        EvidenceRequirement(
            requirement_id="crypto_spot_confirmation",
            description="Verify current BTC spot prices from independent exchange venues.",
            importance="hard",
            source_priority=["exchange"],
            authority_floor="exchange",
            preferred_capabilities=["market.crypto_derivatives"],
            freshness_seconds=600,
            minimum_independent_sources=2,
            allowed_fallbacks=[],
            confidence_cap=0.5,
        )
    ]
    if narrative_present:
        requirements.append(
            EvidenceRequirement(
                requirement_id="market_narrative",
                description="Find current approved macro or market sentiment context.",
                importance="soft",
                source_priority=["official", "audited_aggregator", "verified_web"],
                authority_floor="verified_web",
                preferred_capabilities=list(preferred),
                freshness_seconds=86400,
                minimum_independent_sources=1,
                allowed_fallbacks=list(fallback),
                confidence_cap=0.65,
            )
        )
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id="research-request:prompt-query-scope",
        run_id="run-prompt-query-scope",
        event_id="event-prompt-query-scope",
        trigger_snapshot_id="snapshot-prompt-query-scope",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode="live",
        research_mode="current_decision",
        pit_cutoff_at=NOW + timedelta(minutes=30),
        current_round=current_round,
        evidence_refs=["evidence-input"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="evidence-input",
                kind="document",
                authority="unverified",
                source_id="manual",
                source_url=None,
                published_at=None,
                observed_at=NOW,
                received_at=NOW,
                content_hash="0" * 64,
                excerpt="Research the current BTC snapshot.",
            )
        ],
        evidence_requirements=requirements,
        target_gaps=[
            EvidenceGap(
                requirement_id="market_narrative",
                importance="soft",
                reason_code="insufficient_sources",
                query_hint="Find an approved source and fetch the exact source.",
                attempted_capabilities=[],
                blocks_directional_output=False,
            )
        ]
        if narrative_gap
        else [],
        allowed_capabilities=["market.crypto_derivatives", *allowed_web],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=10,
            max_tool_calls=100,
            max_subagents=32,
            total_deadline_seconds=1800,
            per_tool_timeout_seconds=120,
            per_model_step_timeout_seconds=600,
            max_structured_repairs=3,
            max_estimated_cost_usd=10,
        ),
        deadline_at=NOW + timedelta(minutes=30),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


def _render(request: ResearchSessionRequest, legacy: bool) -> str:
    before = request.model_dump_json()
    prompt = (
        build_research_prompt(request, "original-runtime-session")
        if legacy
        else build_trusted_web_research_prompt(request)
    )
    assert request.model_dump_json() == before, "Rendering changed the frozen request"
    return prompt


def _routes(prompt: str) -> dict[str, list[str]]:
    line = next(line for line in prompt.splitlines() if line.startswith(ROUTES_PREFIX))
    body = line.removeprefix(ROUTES_PREFIX).split(". The global", 1)[0]
    return json.loads(body)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("narrative_present", [False, True])
def test_scope_excluded_narrative_preserves_gap_without_requesting_rejected_fetches(
    legacy: bool, narrative_present: bool,
) -> None:
    # The real default Run kept this soft target gap after the no-search
    # projection removed its narrative requirement. Global web.fetch remained.
    request = _request(narrative_present=narrative_present)
    prompt = _render(request, legacy)
    assert _routes(prompt) == {}
    assert OFFICIAL_URL not in prompt and SENTIMENT_URL not in prompt
    assert "no executable audited acquisition route" in prompt
    assert "Preserve the declared market_narrative soft gap" in prompt
    assert "without claiming that a provider was attempted or failed" in prompt
    assert "no executable audited web.fetch route" in prompt
    assert "with web.fetch for each relevant locator" not in prompt


@pytest.mark.parametrize("legacy", [False, True])
def test_declared_narrative_fetch_is_rendered_without_inventing_search_permission(
    legacy: bool,
) -> None:
    request = _request(narrative_present=True, fallback=("web.fetch",))
    prompt = _render(request, legacy)
    assert _routes(prompt) == {"market_narrative": ["web.fetch"]}
    assert OFFICIAL_URL in prompt and SENTIMENT_URL in prompt
    assert "capability_id=web.search exactly once" not in prompt
    assert "no executable audited acquisition route" not in prompt


def test_global_allowlist_cannot_authorize_a_fetch_missing_from_the_requirement() -> None:
    request = _request(narrative_present=True, allowed_web=("web.search", "web.fetch"))
    prompt = _render(request, False)
    assert _routes(prompt) == {"market_narrative": ["web.search"]}
    assert "capability_id=web.search exactly once" in prompt
    assert OFFICIAL_URL not in prompt and SENTIMENT_URL not in prompt
    assert "no executable audited web.fetch route" in prompt


def test_requirement_fetch_route_cannot_override_the_global_allowlist() -> None:
    request = _request(
        narrative_present=True, preferred=("web.fetch",), allowed_web=(),
    )
    prompt = _render(request, False)
    assert _routes(prompt) == {}
    assert OFFICIAL_URL not in prompt and SENTIMENT_URL not in prompt
    assert "no executable audited acquisition route" in prompt


def test_legal_search_then_fetch_routes_keep_both_acquisition_steps() -> None:
    request = _request(
        narrative_present=True, fallback=("web.fetch",),
        allowed_web=("web.search", "web.fetch"),
    )
    prompt = _render(request, False)
    assert _routes(prompt) == {"market_narrative": ["web.fetch", "web.search"]}
    assert "capability_id=web.search exactly once" in prompt
    assert OFFICIAL_URL in prompt and SENTIMENT_URL in prompt
    assert "Native discovery never adds a requirement or an audited capability route" in prompt


def test_later_round_without_narrative_target_does_not_invent_a_narrative_gap() -> None:
    request = _request(narrative_gap=False, current_round=2)
    prompt = _render(request, False)
    assert OFFICIAL_URL not in prompt and SENTIMENT_URL not in prompt
    assert "Preserve the declared market_narrative soft gap" not in prompt
    assert _routes(prompt) == {}
