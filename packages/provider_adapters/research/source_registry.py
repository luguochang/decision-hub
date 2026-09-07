from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml

from packages.contracts_py.decision_hub_contracts import ResearchSourcePolicy
from packages.contracts_py.decision_hub_contracts import (
    ResearchSourceRegistry as ResearchSourceRegistryContract,
)

from .fact_pack import CryptoMacroFactPack

Usage = Literal["search", "fetch", "evidence"]


class ResearchSourceRegistryError(ValueError):
    """Stable, user-readable source admission failure."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class SourceLocatorDecision:
    status: Literal["approved_locator", "discovery_only", "unknown"]
    source_ref: str | None
    allow_fetch: bool
    allow_evidence: bool


class ResearchSourceRegistry:
    """Resolve URL locators against a versioned Domain Pack source policy.

    This registry is read-only. It does not fetch URLs, persist candidates, or
    decide evidence quality; those remain adapter and Kernel responsibilities.
    """

    def __init__(
        self,
        contract: ResearchSourceRegistryContract,
        *,
        requirement_aliases: Mapping[str, str] | None = None,
    ) -> None:
        self.contract = contract
        self.entries = tuple(contract.sources)
        self._by_ref: dict[str, ResearchSourcePolicy] = {}
        self._canonical_by_requirement: dict[str, str] = {}
        self._pack_by_requirement: dict[str, str] = {}
        for pack_id, canonical_id in (requirement_aliases or {}).items():
            if not pack_id or not canonical_id:
                raise ResearchSourceRegistryError(
                    "research_source_requirement_alias_invalid",
                    "requirement aliases must be non-empty",
                )
            for alias in (pack_id, canonical_id):
                existing = self._pack_by_requirement.get(alias)
                if existing is not None and existing != pack_id:
                    raise ResearchSourceRegistryError(
                        "research_source_requirement_alias_ambiguous",
                        "requirement alias resolves to more than one Pack requirement",
                    )
                self._pack_by_requirement[alias] = pack_id
                self._canonical_by_requirement[alias] = canonical_id
        for entry in self.entries:
            if entry.source_ref in self._by_ref:
                raise ResearchSourceRegistryError(
                    "research_source_duplicate", "source_ref must be unique"
                )
            self._by_ref[entry.source_ref] = entry

    @classmethod
    def from_pack(cls, pack_root: Path) -> ResearchSourceRegistry:
        path = pack_root / "evidence" / "source_registry.yaml"
        if not path.exists():
            raise ResearchSourceRegistryError(
                "research_source_registry_missing", "source registry file is missing"
            )
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            contract = ResearchSourceRegistryContract.model_validate(payload)
        except Exception as exc:
            raise ResearchSourceRegistryError(
                "research_source_registry_invalid", "source registry contract is invalid"
            ) from exc
        try:
            aliases = CryptoMacroFactPack.from_pack(pack_root).requirement_aliases
        except ValueError as exc:
            raise ResearchSourceRegistryError(
                "research_source_requirement_alias_invalid",
                "source manifest requirement aliases are invalid",
            ) from exc
        return cls(contract, requirement_aliases=aliases)

    def canonical_requirement_id(self, requirement_id: str) -> str:
        """Resolve either a Pack-local or canonical ID without guessing syntax."""

        return self._canonical_by_requirement.get(requirement_id, requirement_id)

    def classify_locator(self, url: str) -> SourceLocatorDecision:
        matches = self._matches(url)
        if not matches:
            return SourceLocatorDecision("unknown", None, False, False)
        if len(matches) > 1:
            raise ResearchSourceRegistryError(
                "research_source_ambiguous", "URL matches more than one source policy"
            )
        source = matches[0]
        approved = source.license_status == "approved" and source.audit_status == "approved"
        return SourceLocatorDecision(
            "approved_locator" if approved else "discovery_only",
            source.source_ref,
            approved and source.allow_fetch,
            approved and source.allow_evidence,
        )

    def require(
        self,
        url: str,
        *,
        usage: Usage,
        requirement_id: str,
    ) -> ResearchSourcePolicy:
        matches = self._matches(url)
        if not matches:
            raise ResearchSourceRegistryError(
                "research_source_unknown", "URL is not present in the source registry"
            )
        if len(matches) > 1:
            raise ResearchSourceRegistryError(
                "research_source_ambiguous", "URL matches more than one source policy"
            )
        source = matches[0]
        if source.license_status != "approved" or source.audit_status != "approved":
            raise ResearchSourceRegistryError(
                "research_source_not_approved",
                "source license and audit must be approved",
            )
        pack_requirement_id = self._pack_by_requirement.get(requirement_id, requirement_id)
        if pack_requirement_id not in source.requirement_ids:
            raise ResearchSourceRegistryError(
                "research_source_requirement_denied",
                "source is not approved for this requirement",
            )
        allowed = {
            "search": source.allow_search,
            "fetch": source.allow_fetch,
            "evidence": source.allow_evidence,
        }
        if not allowed[usage]:
            raise ResearchSourceRegistryError(
                "research_source_not_approved", f"source is not approved for {usage}"
            )
        return source

    def _matches(self, url: str) -> list[ResearchSourcePolicy]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path or "/"
        if parsed.scheme not in {"http", "https"} or not host:
            return []
        return [
            source
            for source in self.entries
            if any(_domain_matches(host, domain) for domain in source.domains)
            and any(_path_matches(path, prefix) for prefix in source.allowed_paths)
        ]


def _domain_matches(host: str, allowed: str) -> bool:
    normalized = allowed.strip().lower().rstrip(".").removeprefix(".")
    return host == normalized or host.endswith(f".{normalized}")


def _path_matches(path: str, prefix: str) -> bool:
    normalized = prefix.strip() or "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized == "/":
        return True
    normalized = normalized.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")
