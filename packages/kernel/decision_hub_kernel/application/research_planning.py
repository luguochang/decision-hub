from __future__ import annotations

from collections.abc import Iterable

from packages.contracts_py.decision_hub_contracts import (
    CapabilityManifest,
    EvidenceGap,
    EvidenceRequirement,
)


class CapabilityCatalog:
    """Small, typed view over the audited capability manifests.

    The catalog does not execute providers. It only answers which already
    audited capabilities may be proposed for a gap under the current cost
    budget. Execution and evidence attestation remain owned by the Hub
    gateway and the selected harness adapter.
    """

    def __init__(self, manifests: Iterable[CapabilityManifest]) -> None:
        self._manifests = {item.capability_id: item for item in manifests}

    def eligible(
        self,
        *,
        preferred: Iterable[str],
        fallbacks: Iterable[str],
        max_cost_usd: float | None,
    ) -> tuple[str, ...]:
        ordered = dict.fromkeys((*preferred, *fallbacks))
        result: list[str] = []
        for capability_id in ordered:
            manifest = self._manifests.get(capability_id)
            if manifest is None or manifest.status not in {"enabled", "shadow"}:
                continue
            # Unknown provider cost is not eligible under a bounded run. A
            # manifest without a cost ceiling is still usable when the run
            # itself has no monetary ceiling (for replay/local adapters).
            if max_cost_usd is not None and (
                manifest.max_cost_usd is None or manifest.max_cost_usd > max_cost_usd
            ):
                continue
            result.append(capability_id)
        return tuple(result)

    def for_gap(
        self,
        requirement: EvidenceRequirement,
        gap: EvidenceGap,
        *,
        max_cost_usd: float | None,
    ) -> tuple[str, ...]:
        candidates = self.eligible(
            preferred=requirement.preferred_capabilities,
            fallbacks=requirement.allowed_fallbacks,
            max_cost_usd=max_cost_usd,
        )
        attempted = set(gap.attempted_capabilities)
        return tuple(item for item in candidates if item not in attempted)


def next_capabilities_for_gap(
    requirement: EvidenceRequirement,
    gap: EvidenceGap,
    *,
    catalog: CapabilityCatalog,
    max_cost_usd: float | None,
) -> tuple[str, ...]:
    """Return the untried capability ladder for one unresolved requirement."""

    return catalog.for_gap(requirement, gap, max_cost_usd=max_cost_usd)
