"""Pure, versioned completeness and distinctness rules for research windows.

The explicit reference is the caller's business cutoff. This check does not
grant calibration, publication, causal independence, or execution permission.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import HorizonDecision

HORIZON_CONTRACT_VERSION = "research-horizon-contract.v1"
_REQUIRED_NAMES = frozenset({"30m", "24h", "72h"})

type HorizonCanonicalSignature = tuple[tuple[str, ...], str, str, datetime, datetime]


@dataclass(frozen=True, slots=True)
class HorizonContractCheck:
    valid: bool
    issue_codes: tuple[str, ...]
    canonical_signatures: tuple[HorizonCanonicalSignature, ...]
    contract_version: str = HORIZON_CONTRACT_VERSION


def _as_utc(value: datetime) -> datetime | None:
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        return None


def validate_horizon_contract(
    horizons: Sequence[HorizonDecision],
    reference_at: datetime,
) -> HorizonContractCheck:
    """Check the complete three-window contract without rewriting its inputs.

    Empty horizons are incomplete here; a bounded material-only caller may
    explicitly preserve them. Signatures are sorted by their own content, so
    input order, window labels, actions, and probabilities cannot add difference.
    """
    issues: list[str] = []
    signatures: list[HorizonCanonicalSignature] = []

    def issue(code: str) -> None:
        if code not in issues:
            issues.append(code)

    reference = _as_utc(reference_at)
    if reference is None:
        issue("reference_timezone_invalid")
    if not horizons:
        issue("empty_horizons")
    if len(horizons) != 3 or frozenset(item.horizon for item in horizons) != _REQUIRED_NAMES:
        issue("horizon_names_invalid")

    for item in horizons:
        trigger = item.trigger.strip()
        invalidation = item.invalidation.strip()
        if not trigger:
            issue("horizon_trigger_blank")
        if not invalidation:
            issue("horizon_invalidation_blank")
        refs_valid = bool(item.evidence_refs) and all(ref.strip() for ref in item.evidence_refs)
        if not refs_valid:
            issue("horizon_evidence_refs_invalid")
        expiry = _as_utc(item.expires_at)
        review = _as_utc(item.next_review_at)
        if expiry is None:
            issue("horizon_expiry_timezone_invalid")
        if review is None:
            issue("horizon_review_timezone_invalid")
        if reference is not None and review is not None and review <= reference:
            issue("horizon_review_not_after_reference")
        if review is not None and expiry is not None and review > expiry:
            issue("horizon_review_after_expiry")
        if trigger and invalidation and refs_valid and expiry is not None and review is not None:
            signatures.append(
                (
                    tuple(sorted(set(item.evidence_refs))),
                    trigger,
                    invalidation,
                    expiry,
                    review,
                )
            )

    if len(set(signatures)) != len(signatures):
        issue("horizon_signatures_duplicate")
    return HorizonContractCheck(
        valid=not issues,
        issue_codes=tuple(issues),
        canonical_signatures=tuple(sorted(signatures)),
    )
