from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from packages.contracts_py.decision_hub_contracts.models import SourceManifest, TextEnvelope


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
    ) -> None:
        self.source_ids = frozenset(source_ids)
        self.high_impact_terms = tuple(
            term.casefold().strip() for term in high_impact_terms if term.strip()
        )
        self._term_patterns = tuple(
            re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
            for term in self.high_impact_terms
        )
        self.research_strategy = research_strategy

    @classmethod
    def from_pack(cls, pack_root: Path) -> CryptoMacroDiscoveryPolicy:
        path = pack_root / "discovery.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("discovery_policy_invalid")
        source_ids = raw.get("source_ids", [])
        terms = raw.get("high_impact_terms", [])
        if not isinstance(source_ids, list) or not isinstance(terms, list):
            raise ValueError("discovery_policy_invalid")
        return cls(source_ids=source_ids, high_impact_terms=terms)

    def __call__(self, manifest: SourceManifest, envelope: TextEnvelope) -> tuple[str, ...]:
        baseline = ("baseline.v1",)
        if manifest.source_id not in self.source_ids:
            return baseline
        title = next(
            (
                line.strip().casefold()
                for line in envelope.raw_text.splitlines()
                if line.strip()
            ),
            "",
        )
        return (
            (*baseline, self.research_strategy)
            if any(pattern.search(title) for pattern in self._term_patterns)
            else baseline
        )
