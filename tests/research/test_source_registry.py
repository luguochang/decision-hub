from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from packages.contracts_py.decision_hub_contracts import (
    ResearchSourceRegistry as ResearchSourceRegistryContract,
)
from packages.provider_adapters.research.source_registry import (
    ResearchSourceRegistry,
    ResearchSourceRegistryError,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"


def test_pack_source_registry_covers_all_tiers_and_approved_fed_fetch() -> None:
    registry = ResearchSourceRegistry.from_pack(PACK_ROOT)

    assert {item.tier for item in registry.entries} == {"P0", "P1", "P2", "P3", "P4"}
    source = registry.require(
        "https://www.federalreserve.gov/feeds/speeches.xml",
        usage="fetch",
        requirement_id="event.identity",
    )
    assert source.source_ref == "fed.monetary_policy"
    assert source.authority == "official"
    assert source.allow_evidence is True


def test_pack_source_registry_resolves_canonical_requirement_from_manifest() -> None:
    registry = ResearchSourceRegistry.from_pack(PACK_ROOT)

    source = registry.require(
        "https://www.federalreserve.gov/newsevents/speech/example.htm",
        usage="fetch",
        requirement_id="event_identity",
    )

    assert source.source_ref == "fed.monetary_policy"
    assert registry.canonical_requirement_id("event.identity") == "event_identity"
    assert registry.canonical_requirement_id("event_identity") == "event_identity"


@pytest.mark.parametrize(
    "url",
    [
        "https://finance.yahoo.com/news/example",
        "https://x.com/example/status/1",
        "https://unknown.example/event",
    ],
)
def test_discovery_only_or_unknown_source_cannot_be_fetched(url: str) -> None:
    registry = ResearchSourceRegistry.from_pack(PACK_ROOT)

    with pytest.raises(ResearchSourceRegistryError) as raised:
        registry.require(url, usage="fetch", requirement_id="event.identity")

    assert raised.value.error_code in {
        "research_source_not_approved",
        "research_source_unknown",
    }


def test_locator_classification_never_promotes_unknown_domain() -> None:
    registry = ResearchSourceRegistry.from_pack(PACK_ROOT)

    approved = registry.classify_locator(
        "https://www.federalreserve.gov/newsevents/speech/example.htm"
    )
    unknown = registry.classify_locator("https://unknown.example/reposted-fed-speech")

    assert approved.status == "approved_locator"
    assert approved.source_ref == "fed.monetary_policy"
    assert unknown.status == "unknown"
    assert unknown.source_ref is None
    assert unknown.allow_fetch is False
    assert unknown.allow_evidence is False


def test_registry_rejects_ambiguous_source_matches(tmp_path: Path) -> None:
    pack = tmp_path / "evidence"
    pack.mkdir()
    (pack / "source_registry.yaml").write_text(
        """schema_version: research-source-registry.v1
pack_id: crypto_macro.v1
version: 1.0.0-test
sources:
  - &source
    source_ref: fed.one
    tier: P0
    publisher: Federal Reserve
    authority: official
    independence_group: federal-reserve
    domains: [federalreserve.gov]
    allowed_paths: [/feeds/]
    requirement_ids: [event.identity]
    allow_search: true
    allow_fetch: true
    allow_evidence: true
    parser_ref: official-event.v1
    license_status: approved
    retention_policy: hash_excerpt
    audit_status: approved
    redirect_policy: same_source_only
  - <<: *source
    source_ref: fed.two
""",
        encoding="utf-8",
    )

    contract = ResearchSourceRegistryContract.model_validate(
        yaml.safe_load((pack / "source_registry.yaml").read_text(encoding="utf-8"))
    )
    registry = ResearchSourceRegistry(contract)
    with pytest.raises(ResearchSourceRegistryError) as raised:
        registry.require(
            "https://www.federalreserve.gov/feeds/speeches.xml",
            usage="fetch",
            requirement_id="event.identity",
        )
    assert raised.value.error_code == "research_source_ambiguous"
