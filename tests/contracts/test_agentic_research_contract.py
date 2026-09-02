from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    DomainPackManifest,
    ExecutionBudget,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSnapshotManifest,
    RoleProfile,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"


def _yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_crypto_macro_pack_and_all_references_are_contract_valid() -> None:
    manifest = DomainPackManifest.model_validate(_yaml(PACK_ROOT / "pack.yaml"))

    assert manifest.pack_id == "crypto_macro"
    assert manifest.product_extension_ref == "decision.v1"
    assert manifest.horizons == ["30m", "24h", "72h"]
    assert manifest.execution_budget == ExecutionBudget(
        max_evidence_rounds=3,
        max_tool_calls=12,
        max_subagents=6,
        total_deadline_seconds=480,
        per_tool_timeout_seconds=20,
        per_model_step_timeout_seconds=150,
        max_structured_repairs=1,
        max_estimated_cost_usd=1.0,
    )

    for relative_ref in (
        manifest.doctrine_ref,
        manifest.evidence_policy_ref,
        manifest.gate_policy_ref,
        manifest.evaluation_policy_ref,
        *manifest.role_profile_refs,
    ):
        assert (PACK_ROOT / relative_ref).is_file(), relative_ref

    profiles = [
        RoleProfile.model_validate(_yaml(PACK_ROOT / relative_ref))
        for relative_ref in manifest.role_profile_refs
    ]
    assert {profile.profile_id for profile in profiles} == {
        "crypto_macro.manager",
        "crypto_macro.counter_thesis",
        "crypto_macro.data_quality",
    }

    bindings = _yaml(PACK_ROOT / "tools" / "bindings.yaml")
    assert isinstance(bindings, dict)
    capabilities = [
        ResearchCapabilityManifest.model_validate(item) for item in bindings["capabilities"]
    ]
    assert {item.capability_id for item in capabilities} == set(manifest.capability_refs)
    approved = {
        item.capability_id
        for item in capabilities
        if item.audit_status == "approved" and item.license_status == "approved"
    }
    assert approved == {
        "market.cross_asset",
        "market.crypto_derivatives",
        "official.macro",
        "replay.research",
        "web.search",
    }
    assert all(
        item.audit_status == "candidate"
        for item in capabilities
        if item.capability_id not in approved
    )

    from packages.provider_adapters.research import CryptoMacroFactPack

    requirements = list(CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements())
    requirement_ids = {item.requirement_id for item in requirements}
    assert {
        "event_identity",
        "policy_or_data_delta",
        "expectation_pricing",
        "macro_transmission",
        "crypto_spot_confirmation",
        "derivatives_crowding",
        "counter_thesis",
    } <= requirement_ids


def test_canonical_models_reject_unknown_fields_and_unbounded_budget() -> None:
    valid = _yaml(PACK_ROOT / "pack.yaml")
    assert isinstance(valid, dict)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        DomainPackManifest.model_validate({**valid, "runtime": "dsh"})

    requirement = {
        "requirement_id": "event_identity",
        "description": "Verify the event identity.",
        "importance": "hard",
        "source_priority": ["official"],
        "freshness_seconds": 3600,
        "minimum_independent_sources": 1,
        "allowed_fallbacks": ["web.search"],
        "confidence_cap": 0.45,
    }
    with pytest.raises(ValidationError, match="Field required"):
        from packages.contracts_py.decision_hub_contracts import EvidenceRequirement

        EvidenceRequirement.model_validate(requirement)

    invalid_budget = {
        **valid,
        "execution_budget": {
            **valid["execution_budget"],
            "max_evidence_rounds": 11,
        },
    }
    with pytest.raises(ValidationError, match="less_than_equal"):
        DomainPackManifest.model_validate(invalid_budget)

    query = ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "request-1",
            "capability_id": "replay.research",
            "requirement_id": "event_identity",
            "query": "archived event identity",
            "target_url": None,
            "symbols": [],
            "fields": [],
            "allowed_domains": [],
            "max_results": 3,
            "max_cost_usd": 0,
            "research_session_id": "session-1",
            "round": 1,
            "mode": "replay",
            "observed_at": "2026-08-29T00:00:00Z",
            "cutoff_at": "2026-08-29T00:01:00Z",
        }
    )
    assert query.mode == "replay"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchSnapshotManifest.model_validate(
            {
                "schema_version": "research-snapshot-manifest.v1",
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "event_id": "event-1",
                "snapshot_type": "trigger",
                "generation": 1,
                "parent_snapshot_id": None,
                "cutoff_at": "2026-08-29T00:00:00Z",
                "snapshot_hash": "0" * 64,
                "evidence_refs": ["evidence-1"],
                "pack_version": "crypto_macro.v1",
                "created_at": "2026-08-29T00:00:00Z",
                "uncontracted": True,
            }
        )


def test_research_session_receives_bounded_readable_evidence_and_round_context() -> None:
    evidence = ResearchInputEvidence(
        evidence_id="evidence-transcript",
        kind="transcript",
        authority="unverified",
        source_id="manual-transcript",
        source_url=None,
        published_at=datetime(2026, 8, 29, tzinfo=UTC),
        observed_at=datetime(2026, 8, 29, 0, 0, 1, tzinfo=UTC),
        received_at=datetime(2026, 8, 29, 0, 0, 2, tzinfo=UTC),
        content_hash="0" * 64,
        excerpt="The official said inflation risks remain elevated.",
    )
    request = ResearchSessionRequest.model_validate(
        {
            "schema_version": "research-session-request.v1",
            "request_id": "request-1",
            "run_id": "run-1",
            "event_id": "event-1",
            "trigger_snapshot_id": "snapshot-1",
            "domain_pack_ref": "crypto_macro.v1",
            "role_profile_ref": "crypto_macro.manager.v1",
            "execution_mode": "replay",
            "pit_cutoff_at": datetime(2026, 8, 29, 0, 0, 2, tzinfo=UTC),
            "current_round": 1,
            "evidence_refs": [evidence.evidence_id],
            "input_evidence": [evidence],
            "evidence_requirements": [
                {
                    "requirement_id": "event_identity",
                    "description": "Verify event identity.",
                        "importance": "hard",
                        "source_priority": ["official"],
                        "authority_floor": "official",
                        "preferred_capabilities": ["replay.research"],
                        "freshness_seconds": 3600,
                    "minimum_independent_sources": 1,
                    "allowed_fallbacks": ["web.search"],
                    "confidence_cap": 0.45,
                }
            ],
            "target_gaps": [],
            "allowed_capabilities": ["replay.research"],
            "execution_budget": {
                "max_evidence_rounds": 3,
                "max_tool_calls": 12,
                "max_subagents": 6,
                "total_deadline_seconds": 180,
                "per_tool_timeout_seconds": 20,
                "per_model_step_timeout_seconds": 60,
                "max_structured_repairs": 1,
                "max_estimated_cost_usd": 1.0,
            },
            "deadline_at": datetime(2026, 8, 29, 0, 3, 2, tzinfo=UTC),
            "output_schema_ref": "research-session-result.v1",
            "repair_instructions": None,
        }
    )

    assert request.input_evidence[0].excerpt.startswith("The official")
    assert request.current_round == 1
    assert request.target_gaps == []

    with pytest.raises(ValidationError, match="string_too_long"):
        ResearchInputEvidence.model_validate(
            evidence.model_dump(mode="json") | {"excerpt": "x" * 4001}
        )


def test_agentic_contracts_are_exposed_only_through_stable_public_packages() -> None:
    python_public = (ROOT / "packages/contracts_py/decision_hub_contracts/__init__.py").read_text(
        encoding="utf-8"
    )
    typescript_public = (ROOT / "packages/contracts_ts/src/index.ts").read_text(encoding="utf-8")

    for name in (
        "DomainPackManifest",
        "ResearchSessionRequest",
        "ResearchSessionResult",
        "ResearchSynthesisCandidate",
        "ResearchTraceEvent",
        "ResearchRunView",
        "ResearchCapabilityQuery",
        "ResearchCapabilityResult",
        "ResearchSnapshotManifest",
    ):
        assert f'"{name}"' in python_public
        assert name in typescript_public
