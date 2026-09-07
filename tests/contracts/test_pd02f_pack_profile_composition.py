from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from apps.research_mcp.main import compose_capability_adapters, load_capability_manifests
from packages.contracts_py.decision_hub_contracts import DomainPackManifest, RoleProfile
from packages.provider_adapters.research import CryptoMacroFactPack, ReplayResearchArchive
from packages.provider_adapters.routing import ProviderCapabilityRouter

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"


def _yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_pack_binding_role_and_composition_capabilities_are_closed() -> None:
    pack = DomainPackManifest.model_validate(_yaml(PACK_ROOT / "pack.yaml"))
    manifests = load_capability_manifests()
    manifest_ids = {item.capability_id for item in manifests}
    assert manifest_ids == set(pack.capability_refs)

    profiles = [
        RoleProfile.model_validate(_yaml(PACK_ROOT / relative_ref))
        for relative_ref in pack.role_profile_refs
    ]
    requirement_ids = {
        item.requirement_id
        for item in CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements()
    }
    for profile in profiles:
        unknown = set(profile.allowed_tools) - manifest_ids
        assert not unknown, f"{profile.profile_id} references unknown capability(s): {unknown}"
        assert set(profile.required_capabilities) <= requirement_ids | {"data_quality"}

    adapters = compose_capability_adapters(manifests, ReplayResearchArchive({}, {}))
    by_capability: dict[str, list[object]] = {}
    for adapter in adapters:
        by_capability.setdefault(adapter.capability_id, []).append(adapter)
    routed = {item.capability_id for item in manifests if item.provider_routes}
    assert routed <= set(by_capability)
    for capability_id in routed:
        assert len(by_capability[capability_id]) == 1
        assert isinstance(by_capability[capability_id][0], ProviderCapabilityRouter)


def test_candidate_macro_routes_are_not_default_executable() -> None:
    manifests = load_capability_manifests()
    candidate_ids = {
        item.capability_id
        for item in manifests
        if item.audit_status == "candidate" or item.license_status == "review_required"
    }
    assert candidate_ids == {
        "macro.cross_asset_intraday",
        "macro.expectation_pricing",
    }
    launcher = (ROOT / "infra/dsh/run-product.sh").read_text(encoding="utf-8")
    match = re.search(
        r'research_capabilities="\$\{DECISION_HUB_RESEARCH_CAPABILITIES:-([^}]*)\}"',
        launcher,
    )
    assert match is not None
    default_live = {item for item in match.group(1).split(",") if item}
    approved = {
        item.capability_id
        for item in manifests
        if item.audit_status == "approved" and item.license_status == "approved"
    }
    assert default_live <= approved
    assert candidate_ids.isdisjoint(default_live)


def test_dsh_preset_has_one_hub_research_tool_and_keeps_native_web_tools() -> None:
    preset = (ROOT / "infra/dsh/presets/decision-research/agent.cordis.yml").read_text(
        encoding="utf-8"
    )
    assert preset.count("@decision-hub/dsh-plugin/research-tool") == 1
    assert "@deepseek-ai/dsh-tool-web" in preset
    assert "search: true" in preset
    assert "fetch: true" in preset


@pytest.mark.parametrize(
    "capability_id", ["macro.cross_asset_intraday", "macro.expectation_pricing"]
)
def test_candidate_manifest_requires_explicit_approval(capability_id: str) -> None:
    manifest = next(
        item for item in load_capability_manifests() if item.capability_id == capability_id
    )
    assert manifest.audit_status == "candidate"
    assert manifest.license_status == "review_required"
    assert manifest.provider_routes
    assert all(route.requires_event_window for route in manifest.provider_routes)


def test_routed_capability_deadline_can_execute_one_retryable_fallback() -> None:
    """A declared fallback must fit inside the outer Gateway deadline.

    This is a composition invariant rather than a provider implementation
    detail. Without it, a slow primary consumes the entire capability timeout
    and the fallback can never execute in a real Run.
    """

    for manifest in load_capability_manifests():
        routes = [
            route
            for route in manifest.provider_routes or []
            if route.audit_status == "approved"
            and route.license_status == "approved"
            and not route.requires_event_window
        ]
        if len(routes) < 2:
            continue
        ordered = sorted(routes, key=lambda route: (route.priority, route.provider_id))
        assert manifest.timeout_seconds >= sum(
            route.timeout_seconds for route in ordered[:2]
        ), manifest.capability_id
