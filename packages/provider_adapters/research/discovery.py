from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from packages.contracts_py.decision_hub_contracts.models import SourceManifest, TextEnvelope


# Closed national BLS monthly-price-release forms. These prefixes are part of
# the existing immutable policy snapshot; they are not arbitrary regex config.
_BLS_PRICE_RELEASE_PREFIXES = frozenset(
    f"{name} {separator}"
    for name in ("consumer price index", "producer price index", "producer price indexes")
    for separator in ("-", "–", "—")
)
_BLS_PRICE_RELEASE_MONTH = re.compile(
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december) [12]\d{3}"
)
_BLS_PRICE_RELEASE_NAME = re.compile(
    r"^(?:consumer price index|producer price index(?:es)?)(?!\w)"
)


class CryptoMacroDiscoveryPolicy:
    """Deterministic low-cost admission policy for the crypto-macro Pack.

    Discovery only decides whether a separate research candidate should be
    queued. It never decides a direction, confidence, or publish status.
    """

    def __init__(
        self,
        *,
        source_ids: Iterable[str],
        high_impact_terms: Iterable[str],
        research_strategy: str = "research.v1",
        max_daily_research: int = 12,
        max_study_research_per_hour: int = 2,
        include_baseline: bool = True,
        policy_version: str | None = None,
        source_title_prefixes: dict[str, list[str]] | None = None,
    ) -> None:
        self.source_ids = frozenset(source_ids)
        self.high_impact_terms = tuple(
            term.casefold().strip() for term in high_impact_terms if term.strip()
        )
        self._term_patterns = tuple(
            re.compile(rf"(?<!\w){re.escape(term)}(?!\w)") for term in self.high_impact_terms
        )
        self.research_strategy = research_strategy
        self.max_daily_research = max_daily_research
        self.max_study_research_per_hour = max_study_research_per_hour
        self.include_baseline = include_baseline
        self.record_only_allowed = not include_baseline
        default_version = "discovery-policy.v2" if include_baseline else "discovery-policy.v3"
        if policy_version is not None and re.fullmatch(r"discovery-policy\.v(?:[2-9]|[1-9][0-9]+)", policy_version) is None:
            raise ValueError("discovery_policy_version_invalid")
        self.policy_version = policy_version or default_version
        prefixes = {} if source_title_prefixes is None else source_title_prefixes
        if not isinstance(prefixes, dict) or len(prefixes) > 16:
            raise ValueError("discovery_release_families_invalid")
        self._source_title_prefixes: dict[str, tuple[str, ...]] = {}
        for source_id, values in prefixes.items():
            if source_id not in self.source_ids or not isinstance(values, list) or not 1 <= len(values) <= 16:
                raise ValueError("discovery_release_families_invalid")
            if any(not isinstance(value, str) or not 5 <= len(value.strip()) <= 200 or (not value.strip().endswith((",", "(")) and not (source_id == "bls-releases" and value.casefold().strip() in _BLS_PRICE_RELEASE_PREFIXES)) or "\n" in value or "\r" in value for value in values):
                raise ValueError("discovery_release_families_invalid")
            self._source_title_prefixes[source_id] = tuple(value.casefold().strip() for value in values)

    def source_title_prefixes_snapshot(self) -> dict[str, list[str]]:
        """Return detached normalized rules for immutable recovery contracts."""
        return {
            source_id: list(prefixes)
            for source_id, prefixes in sorted(self._source_title_prefixes.items())
        }

    @classmethod
    def from_pack(
        cls, pack_root: Path, *, include_baseline: bool | None = None
    ) -> CryptoMacroDiscoveryPolicy:
        path = pack_root / "discovery.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("discovery_policy_invalid")
        source_ids = raw.get("source_ids", [])
        terms = raw.get("high_impact_terms", [])
        if not isinstance(source_ids, list) or not isinstance(terms, list):
            raise ValueError("discovery_policy_invalid")
        selected_baseline = bool(raw.get("include_legacy_baseline", False)) if include_baseline is None else include_baseline
        version = raw.get("version")
        # Preserve the explicit legacy v2 override for existing v3 packs.
        if version == "discovery-policy.v3" and selected_baseline:
            version = "discovery-policy.v2"
        if version is not None and not isinstance(version, str):
            raise ValueError("discovery_policy_version_invalid")
        return cls(
            source_ids=source_ids,
            high_impact_terms=terms,
            max_daily_research=int(raw.get("max_daily_research", 12)),
            max_study_research_per_hour=int(raw.get("max_study_research_per_hour", 2)),
            include_baseline=selected_baseline,
            policy_version=version,
            source_title_prefixes=raw.get("source_title_prefixes"),
        )

    def __call__(self, manifest: SourceManifest, envelope: TextEnvelope) -> tuple[str, ...]:
        baseline = ("baseline.v1",) if self.include_baseline else ()
        return baseline + (
            (self.research_strategy,) if self.explain(manifest, envelope)["selected"] else ()
        )

    def explain(self, manifest: SourceManifest, envelope: TextEnvelope) -> dict:
        return self.explain_title(manifest, envelope.raw_text)

    def accepts_title(self, manifest: SourceManifest, title: str) -> bool:
        return self.explain_title(manifest, title)["selected"]

    def explain_title(self, manifest: SourceManifest, text: str) -> dict:
        title = next(
            (line.strip().casefold() for line in text.splitlines() if line.strip()),
            "",
        )
        matched = [
            term
            for term, pattern in zip(self.high_impact_terms, self._term_patterns, strict=True)
            if pattern.search(title)
        ]
        families = [
            prefix for prefix in self._source_title_prefixes.get(manifest.source_id, ())
            if title.startswith(prefix)
            and (
                manifest.source_id != "bls-releases"
                or prefix not in _BLS_PRICE_RELEASE_PREFIXES
                or _BLS_PRICE_RELEASE_MONTH.fullmatch(title[len(prefix):].strip()) is not None
            )
        ]
        # When the Pack opts into this closed price-release family, a regional
        # or explanatory price title cannot bypass its monthly shape via CPI
        # or inflation keywords. Other source/title rules retain their behavior.
        if (manifest.source_id == "bls-releases"
                and any(prefix in _BLS_PRICE_RELEASE_PREFIXES
                        for prefix in self._source_title_prefixes.get("bls-releases", ()))
                and _BLS_PRICE_RELEASE_NAME.match(title)):
            matched = []
        reason = "high_impact_title" if matched or families else "no_high_impact_title_match"
        if manifest.source_id not in self.source_ids:
            reason = "source_outside_research_policy"
        selected = bool(matched or families) and manifest.source_id in self.source_ids
        explanation = {
            "selected": selected,
            "reason": reason,
            "matched_terms": matched,
            "policy_version": self.policy_version,
            "classifier": "deterministic_title_and_release_family_rules"
            if self._source_title_prefixes else "deterministic_title_rules",
        }
        if self._source_title_prefixes:
            explanation["matched_release_families"] = families
        return explanation
