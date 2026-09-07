from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts import (
    CapabilityManifest,
    CoverageAssessment,
    EvidenceGap,
    EvidenceRequirement,
    ExecutionBudget,
    ResearchInputEvidence,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.application.research_planning import (
    CapabilityCatalog,
    next_capabilities_for_gap,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    requirements_for_round,
    should_continue_after_round,
)
from packages.orchestration.langgraph.state.research import AgenticResearchState

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _manifest(
    capability_id: str,
    *,
    status: str = "enabled",
    max_cost_usd: float | None = 0.10,
) -> CapabilityManifest:
    return CapabilityManifest(
        capability_id=capability_id,
        version="1.0.0",
        capability_type="tool",
        provider=capability_id,
        license="owner-approved",
        input_schema_ref="research-capability-query.v1",
        output_schema_ref="research-capability-result.v1",
        permissions=["read_only", "network:https"],
        network_domains=["federalreserve.gov"],
        timeout_seconds=20,
        max_cost_usd=max_cost_usd,
        status=status,  # type: ignore[arg-type]
    )


def _gap(*attempted: str) -> EvidenceGap:
    return EvidenceGap(
        requirement_id="policy.delta",
        importance="hard",
        reason_code="insufficient_sources",
        query_hint="Find an independent policy source.",
        attempted_capabilities=list(attempted),
        blocks_directional_output=True,
    )


def test_catalog_returns_only_enabled_capabilities_within_budget() -> None:
    catalog = CapabilityCatalog(
        [
            _manifest("dsh.web_search"),
            _manifest("tavily.search"),
            _manifest("candidate.search", status="discovered"),
            _manifest("expensive.search", max_cost_usd=2.0),
        ]
    )

    assert catalog.eligible(
        preferred=("dsh.web_search",),
        fallbacks=("tavily.search", "candidate.search", "expensive.search"),
        max_cost_usd=0.50,
    ) == ("dsh.web_search", "tavily.search")


def test_next_capabilities_skip_attempted_primary_and_use_fallback() -> None:
    requirement = EvidenceRequirement(
        requirement_id="policy.delta",
        description="Verify policy delta.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["dsh.web_search"],
        freshness_seconds=60,
        minimum_independent_sources=1,
        allowed_fallbacks=["tavily.search"],
        confidence_cap=0.5,
    )
    catalog = CapabilityCatalog([_manifest("dsh.web_search"), _manifest("tavily.search")])

    assert next_capabilities_for_gap(
        requirement,
        _gap("dsh.web_search"),
        catalog=catalog,
        max_cost_usd=0.50,
    ) == ("tavily.search",)


def test_gap_with_remaining_capability_is_not_terminal_without_new_evidence() -> None:
    requirement = EvidenceRequirement(
        requirement_id="policy.delta",
        description="Verify policy delta.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["dsh.web_search"],
        freshness_seconds=60,
        minimum_independent_sources=1,
        allowed_fallbacks=["tavily.search"],
        confidence_cap=0.5,
    )
    catalog = CapabilityCatalog([_manifest("dsh.web_search"), _manifest("tavily.search")])

    assert next_capabilities_for_gap(
        requirement,
        _gap("dsh.web_search"),
        catalog=catalog,
        max_cost_usd=0.50,
    ) == ("tavily.search",)


def test_round_requirement_ladder_removes_attempted_primary() -> None:
    requirement = EvidenceRequirement(
        requirement_id="policy.delta",
        description="Verify policy delta.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["dsh.web_search"],
        freshness_seconds=60,
        minimum_independent_sources=1,
        allowed_fallbacks=["tavily.search"],
        confidence_cap=0.5,
    )
    updated = requirements_for_round(
        [requirement],
        {"attempted_capabilities_by_requirement": {"policy.delta": ["dsh.web_search"]}},
    )

    assert updated[0].preferred_capabilities == ["tavily.search"]


def test_round_requirement_ladder_excludes_disabled_fallback() -> None:
    requirement = EvidenceRequirement(
        requirement_id="derivatives_crowding",
        description="Observe derivatives crowding.",
        importance="hard",
        source_priority=["exchange"],
        authority_floor="exchange",
        preferred_capabilities=["market.crypto_derivatives"],
        freshness_seconds=60,
        minimum_independent_sources=1,
        allowed_fallbacks=["market.crypto_crowding"],
        confidence_cap=0.5,
    )

    updated = requirements_for_round(
        [requirement],
        {
            "attempted_capabilities_by_requirement": {
                "derivatives_crowding": ["market.crypto_derivatives"]
            }
        },
        allowed_capabilities=["market.crypto_derivatives"],
    )

    assert updated == []


def _insufficient_state(*, execution_mode: str) -> AgenticResearchState:
    request = ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id="continuation-request-1",
        run_id="continuation-run-1",
        event_id="continuation-event-1",
        trigger_snapshot_id="continuation-snapshot-1",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode=execution_mode,  # type: ignore[arg-type]
        pit_cutoff_at=NOW,
        current_round=1,
        evidence_refs=["continuation-input-1"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="continuation-input-1",
                kind="transcript",
                authority="unverified",
                source_id="continuation-source",
                source_url=None,
                published_at=NOW - timedelta(seconds=2),
                observed_at=NOW - timedelta(seconds=1),
                received_at=NOW,
                content_hash="0" * 64,
                excerpt="A bounded continuation fixture.",
            )
        ],
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="event_identity",
                description="Verify the event identity.",
                importance="hard",
                source_priority=["official"],
                authority_floor="official",
                preferred_capabilities=["replay.research"],
                freshness_seconds=3600,
                minimum_independent_sources=1,
                allowed_fallbacks=["web.search"],
                confidence_cap=0.45,
            )
        ],
        target_gaps=[],
        allowed_capabilities=["replay.research"],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=2,
            max_tool_calls=4,
            max_subagents=0,
            total_deadline_seconds=180,
            per_tool_timeout_seconds=20,
            per_model_step_timeout_seconds=60,
            max_structured_repairs=1,
            max_estimated_cost_usd=1.0,
        ),
        deadline_at=NOW + timedelta(minutes=3),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )
    gap = _gap("replay.research")
    coverage = CoverageAssessment(
        status="insufficient",
        covered_requirement_ids=[],
        gaps=[gap],
        conflicts=[],
        hard_coverage_ratio=0.0,
        soft_coverage_ratio=1.0,
        assessed_at=request.pit_cutoff_at,
    )
    return {
        "request": request.model_dump(mode="json"),
        "coverage": coverage.model_dump(mode="json"),
        "current_round": 1,
        "total_tool_calls": 1,
        "total_subagents": 0,
        "latest_result": {"status": "completed"},
        "retryable_failures": 0,
        "attempted_capabilities_by_requirement": {"event_identity": ["replay.research"]},
        "new_evidence_ids": ["evidence-1"],
    }


def test_replay_aggregate_capability_gets_bounded_continuation() -> None:
    assert should_continue_after_round(_insufficient_state(execution_mode="replay")) is True


def test_live_run_cannot_repeat_aggregate_replay_capability() -> None:
    assert should_continue_after_round(_insufficient_state(execution_mode="live")) is False
