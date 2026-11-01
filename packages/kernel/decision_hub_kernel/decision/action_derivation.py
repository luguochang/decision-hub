"""Deterministic research-to-plan projection, with no publication authority.

The application authenticates sidecar/evidence originals in its read set. These
plain values cannot authorize a write; no HTTP route accepts them. Economic
outcomes, current feature truth, and financial qualification are not inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from ..ports.action_plan import ActionPlanHead, ActionPlanRevision, DecisionSnapshot, Opportunity
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_signal import ResearchActionCandidate, ResearchActionSynthesisSidecar
from .action_features import ACTION_MONITOR_MANIFEST_REF, action_feature_manifest
from .action_research_policy import research_action_catalog, validate_action_catalog_candidate


@dataclass(frozen=True)
class ActionBasisEvidence:
    ref: str
    payload_hash: str
    kind: Literal["evidence", "claim", "skill"]
    known_at: datetime
    independence_key: str
    eligible: bool
    original_content_hash: str | None = None
    assertion_hash: str | None = None
    input_known_through: datetime | None = None
    consumer_input_known_at: datetime | None = None


@dataclass(frozen=True)
class DerivedActionBranch:
    revision: ActionPlanRevision
    head: ActionPlanHead


@dataclass(frozen=True)
class DerivedActionPublication:
    snapshot: DecisionSnapshot
    branches: tuple[DerivedActionBranch, ...]


def _seal(model: Any, value: dict[str, Any], field: str):
    parsed = model.model_validate(value | {field: "0" * 64})
    wire = parsed.model_dump(mode="json")
    wire[field] = canonical_hash({k: v for k, v in wire.items() if k != field})
    return model.model_validate(wire)


def _identity(prefix: str, *values: str) -> str:
    return prefix + canonical_hash(list(values))[:40]


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _index_evidence(items: tuple[ActionBasisEvidence, ...]) -> dict[str, ActionBasisEvidence]:
    output = {}
    for row in items:
        if (
            not isinstance(row, ActionBasisEvidence)  # pyright: ignore[reportUnnecessaryIsInstance]
            or not _nonempty_text(row.ref)
            or len(row.ref) > 2048
            or row.kind not in {"evidence", "claim", "skill"}
            or type(row.eligible) is not bool
            or not _nonempty_text(row.independence_key)
            or not _nonempty_text(row.payload_hash)
            or len(row.payload_hash) != 64
            or any(c not in "0123456789abcdef" for c in row.payload_hash)
        ):
            raise ValueError("action_basis_metadata_invalid")
        aware_utc(row.known_at)
        if row.kind == "skill":
            if row.input_known_through is None or row.consumer_input_known_at is None:
                raise ValueError("action_skill_temporal_originals_required")
            if not aware_utc(row.known_at) <= aware_utc(row.consumer_input_known_at):
                raise ValueError("action_skill_temporal_originals_invalid")
            aware_utc(row.input_known_through)
        elif row.input_known_through is not None or row.consumer_input_known_at is not None:
            raise ValueError("action_non_skill_temporal_fields_forbidden")
        if row.original_content_hash is not None and (
            len(row.original_content_hash) != 64
            or any(c not in "0123456789abcdef" for c in row.original_content_hash)
        ):
            raise ValueError("action_basis_content_hash_invalid")
        if row.assertion_hash is not None and (
            len(row.assertion_hash) != 64
            or any(c not in "0123456789abcdef" for c in row.assertion_hash)
        ):
            raise ValueError("action_basis_assertion_hash_invalid")
        if row.ref in output:
            raise ValueError("action_basis_duplicate_reference")
        output[row.ref] = row
    return output


def _derive_action_publication(
    *,
    sidecar: ResearchActionSynthesisSidecar,
    sidecar_ref: str,
    opportunity: Opportunity,
    published_at: datetime,
    evidence: tuple[ActionBasisEvidence, ...],
    expected_status: str,
) -> DerivedActionPublication:
    """Project one scope/horizon with independent flat/long and unknown branches.

    Unknown p never blocks a well-supported trial. Missing entry protection
    blocks only that entry; a separately supported reduction stays available.
    Snapshot p is null because branches may have different probability claims.
    Exact branch probabilities remain in the referenced immutable sidecar.
    """
    sidecar = ResearchActionSynthesisSidecar.model_validate_json(sidecar.model_dump_json())
    opportunity = Opportunity.model_validate_json(opportunity.model_dump_json())
    now = aware_utc(published_at)
    wire = sidecar.model_dump(mode="json")
    if sidecar.payload_hash != canonical_hash(
        {k: v for k, v in wire.items() if k != "payload_hash"}
    ):
        raise ValueError("action_sidecar_hash_mismatch")
    if not sidecar_ref or len(sidecar_ref) > 200 or sidecar_ref.strip() != sidecar_ref:
        raise ValueError("action_sidecar_reference_invalid")
    instruments = {
        f"crypto:spot:okx:{asset}-USDT:trade:1m": f"{asset}-USDT" for asset in ("BTC", "ETH")
    }
    instrument = instruments.get(sidecar.scope_id)
    if instrument is None:
        raise ValueError("action_derivation_scope_unsupported")
    catalog = research_action_catalog(instrument)
    target = catalog["targets"][sidecar.horizon]
    if (opportunity.scope_id, opportunity.horizon, opportunity.target_spec_hash) != (
        sidecar.scope_id,
        sidecar.horizon,
        target["hash"],
    ):
        raise ValueError("action_opportunity_target_mismatch")
    if opportunity.status != expected_status:
        raise ValueError("action_opportunity_not_open")
    if not (
        opportunity.registered_at <= sidecar.producer_cutoff <= sidecar.cutoff <= now
        and opportunity.time_start <= sidecar.producer_cutoff < opportunity.time_end
        and now < opportunity.time_end
    ):
        raise ValueError("action_publication_time_invalid")
    source = _index_evidence(evidence)
    intents = {
        item.position_context: item
        for item in sidecar.synthesis.action_intents
        if item.scope_id == sidecar.scope_id and item.horizon == sidecar.horizon
    }
    if set(intents) != {"flat", "long"}:
        raise ValueError("action_position_branches_required")
    reviews = {review.review_id: review for review in sidecar.synthesis.mechanism_reviews}
    old_horizons = [
        h for h in sidecar.synthesis.research_candidate_v1.horizons if h.horizon == sidecar.horizon
    ]
    if len(old_horizons) != 1:
        raise ValueError("action_selected_horizon_required")
    old_horizon = old_horizons[0]
    snapshot_id = _identity("decision_", opportunity.id, sidecar.payload_hash)
    branch_gaps: dict[str, list[str]] = {}

    def known(ref: str, *, kind: str | None = None) -> bool:
        row = source.get(ref)
        return (
            row is not None
            and row.eligible
            and ((row.input_known_through <= sidecar.producer_cutoff
                  and row.consumer_input_known_at <= now) if row.kind == "skill"
                 else row.known_at <= sidecar.producer_cutoff)
            and (kind is None or row.kind == kind)
        )

    def gaps_for(candidate: ResearchActionCandidate) -> list[str]:
        validate_action_catalog_candidate(candidate, catalog)
        if not now < candidate.valid_until <= opportunity.time_end:
            raise ValueError("action_candidate_expired_or_outside_opportunity")
        if any(
            c.valid_until <= now
            for c in [*candidate.entry, *candidate.exit, *candidate.invalidation]
        ):
            raise ValueError("action_condition_expired_before_publication")
        if candidate.probability.kind == "calibrated":
            # A model-authored provenance string is not a fitted calibration.
            raise ValueError("action_calibration_original_required")
        gaps = []
        claim_sources = [source[ref] for ref in candidate.claim_refs if ref in source]

        def independent_fact(ref: str) -> bool:
            if not known(ref, kind="evidence"):
                return False
            fact = source[ref]
            return not any(
                fact.independence_key == claim.independence_key
                or (fact.original_content_hash or fact.payload_hash)
                == (claim.original_content_hash or claim.payload_hash)
                for claim in claim_sources
            )

        allowed_actions = {
            "flat": {"enter_long", "wait"},
            "long": {"reduce_long", "exit_long", "hold", "wait"},
        }
        if candidate.action not in allowed_actions[candidate.position_context]:
            gaps.append("当前操作与仓位分支不一致")
        if not candidate.evidence_refs or any(
            not independent_fact(ref) for ref in candidate.evidence_refs
        ):
            gaps.append("操作依据缺少在分析时点前已核验的独立事实")
        if any(not known(ref, kind="claim") for ref in candidate.claim_refs):
            gaps.append("引用的外部观点尚未完成原文及版本核验")
        review = reviews[candidate.mechanism_review_ref]
        all_refs = {ref for node in review.nodes for ref in node.refs}
        all_refs.update(
            ref for edge in review.edges for ref in [*edge.support_refs, *edge.counter_refs]
        )
        all_refs.update([*review.alternative_refs, *review.priced_in_refs])
        if any(not known(ref) for ref in all_refs):
            gaps.append("机制链有尚未核验或晚于分析时点的引用")
        support = {
            source[ref].independence_key
            for edge in review.edges
            for ref in edge.support_refs
            if independent_fact(ref)
        }
        support_assertions = {
            source[ref].assertion_hash or source[ref].payload_hash
            for edge in review.edges
            for ref in edge.support_refs
            if independent_fact(ref)
        }
        distinct_counter = {
            source[ref].assertion_hash or source[ref].payload_hash
            for edge in review.edges
            for ref in edge.counter_refs
            if independent_fact(ref)
        }
        if not support:
            gaps.append("机制缺少独立事实支持")
        if candidate.action == "enter_long":
            if not candidate.exit or not candidate.invalidation:
                gaps.append("新增仓位缺少退出或失效保护条件")
            # One venue can supply both breakout and opposing volume facts.
            # Source diversity is not a hidden universal entry requirement.
            # The native independent reviewer must separately authenticate the
            # whole wrapper. A duplicate assertion is not another counterfact.
            if not distinct_counter - support_assertions:
                gaps.append("新增仓位尚缺与支持事实不同的已核反证")
        if candidate.action in {"enter_long", "reduce_long", "exit_long"} and not candidate.entry:
            gaps.append("操作触发条件未定义")
        # Review time schedules deeper research; it is not validity expiry and
        # must not erase still-valid risk reduction while that work is pending.
        return gaps

    for position, candidate in intents.items():
        branch_gaps[position] = gaps_for(candidate)
    # Skill declarations must be authenticated consumption receipts, not text.
    if any(
        not known(ref.consumption_receipt_ref, kind="skill")
        or source[ref.consumption_receipt_ref].payload_hash != ref.consumption_receipt_hash
        for ref in sidecar.synthesis.skill_consumption_refs
    ):
        raise ValueError("action_skill_consumption_original_required")

    review = reviews[intents["flat"].mechanism_review_ref]
    evidence_refs = sorted(
        {
            ref
            for edge in review.edges
            for ref in edge.support_refs
            if known(ref, kind="evidence") and len(ref) <= 256
        }
    )
    counter_refs = sorted(
        {
            ref
            for edge in review.edges
            for ref in edge.counter_refs
            if known(ref, kind="evidence") and len(ref) <= 256
        }
    )
    directions = {
        "long": ["long"],
        "short": ["short"],
        "neutral": ["range"],
        "no_trade": ["long", "short", "range"],
    }[old_horizon.action]
    hypotheses = [
        {
            "direction": direction,
            "evidence_refs": evidence_refs,
            "counter_refs": counter_refs,
            "rationale": (
                "待验证情景；原研究未给出方向。" if old_horizon.action == "no_trade" else ""
            )
            + "；".join(edge.hypothesis for edge in review.edges)[:7600],
        }
        for direction in directions
    ]
    snapshot = _seal(
        DecisionSnapshot,
        {
            "schema_version": "decision-snapshot.v1",
            "id": snapshot_id,
            "opportunity_id": opportunity.id,
            "run_id": sidecar.run_id,
            "source_result_ref": sidecar_ref,
            "source_result_hash": sidecar.payload_hash,
            "decision_cutoff_at": sidecar.producer_cutoff,
            "known_at": now,
            "scope_id": sidecar.scope_id,
            "horizon": sidecar.horizon,
            "hypotheses": hypotheses,
            "factor_refs": [],
            "direction_status": "unknown" if old_horizon.action == "no_trade" else "eligible",
            "path_status": "experimental",
            "probability": {
                "target_id": target["ref"],
                "kind": "unavailable",
                "value": None,
                "provenance_ref": None,
            },
            "gaps": [
                {"scope": position, "reason": gap}
                for position, gaps in branch_gaps.items()
                for gap in gaps
            ],
            "skill_refs": [
                {"version_id": ref.version_id, "hash": ref.version_hash}
                for ref in sidecar.synthesis.skill_consumption_refs
            ],
        },
        "snapshot_hash",
    )
    branches = []
    for position in ("flat", "long", "unknown"):
        candidate = intents.get(position)
        gaps = branch_gaps.get(position, ["请先选择空仓或已有多仓分支，系统未推断你的实际持仓"])
        blocked = bool(gaps)
        action = candidate.action if candidate is not None else "wait"
        branch_ref = sidecar_ref + "#position=" + position
        body = (
            candidate.model_dump(mode="json")
            if candidate
            else {
                "position_context": "unknown",
                "action": "wait",
                "source": sidecar.payload_hash,
            }
        )
        plan_id = _identity("action_", opportunity.id, position)
        revision = _seal(
            ActionPlanRevision,
            {
                "schema_version": "decision-action-plan-revision.v1",
                "revision_id": _identity("actionrev_", plan_id, sidecar.payload_hash),
                "plan_id": plan_id,
                "decision_id": snapshot.id,
                "opportunity_id": opportunity.id,
                "revision_no": 1,
                "previous_id": None,
                "scope_id": sidecar.scope_id,
                "horizon": sidecar.horizon,
                "as_of": sidecar.producer_cutoff,
                "published_at": now,
                "valid_until": candidate.valid_until
                if candidate
                else min(x.valid_until for x in intents.values()),
                "action": action,
                "position_context": position,
                "advice_qualification": "blocked" if blocked else "research_trial",
                "decision_status": "blocked"
                if blocked and position != "unknown"
                else (
                    "unknown"
                    if position == "unknown"
                    else "no_edge"
                    if action in {"wait", "hold"}
                    else "actionable"
                ),
                "entry": [c.model_dump(mode="json") for c in candidate.entry] if candidate else [],
                "exit": [c.model_dump(mode="json") for c in candidate.exit] if candidate else [],
                "invalidation": [c.model_dump(mode="json") for c in candidate.invalidation]
                if candidate
                else [],
                "size_policy": {"kind": "fixed_scenario", "amount": "10000", "unit": "USDT"}
                if position != "unknown"
                else {"kind": "unspecified", "amount": None, "unit": None},
                "cost_policy_ref": catalog["cost_policy"]["ref"],
                "cost_policy_hash": catalog["cost_policy"]["hash"],
                "target_spec_ref": target["ref"],
                "target_spec_hash": target["hash"],
                "basis_refs": [
                    {"ref": sidecar_ref, "hash": sidecar.payload_hash},
                    {"ref": branch_ref, "hash": canonical_hash(body)},
                    {"ref": catalog["size_policy"]["ref"], "hash": catalog["size_policy"]["hash"]},
                    {
                        "ref": ACTION_MONITOR_MANIFEST_REF,
                        "hash": canonical_hash(action_feature_manifest(instrument)),
                    },
                ],
                "reason": ((candidate.reason + "。") if candidate else "")
                + (
                    "未准入：" + "；".join(gaps)
                    if gaps
                    else "研究试用；条件满足前不代表已经执行。"
                    "仓位金额为独立模拟账户权益，非建议成交额。"
                ),
            },
            "payload_hash",
        )
        head = ActionPlanHead.model_validate(
            {
                "schema_version": "decision-action-plan-head.v1",
                "plan_id": plan_id,
                "current_revision_id": revision.revision_id,
                "state_version": 1,
                "state": "proposed" if blocked else "armed",
                "monitor_state": "degraded" if blocked else "active",
                "next_due": None if blocked else now,
                "valid_until": revision.valid_until,
                "lease_owner": None,
                "lease_generation": 0,
                "lease_expires_at": None,
                "last_input_watermark": None,
                "enter_streak": 0,
            }
        )
        branches.append(DerivedActionBranch(revision=revision, head=head))
    return DerivedActionPublication(snapshot=snapshot, branches=tuple(branches))


def derive_action_publication(
    *,
    sidecar: ResearchActionSynthesisSidecar,
    sidecar_ref: str,
    opportunity: Opportunity,
    published_at: datetime,
    evidence: tuple[ActionBasisEvidence, ...],
) -> DerivedActionPublication:
    return _derive_action_publication(
        sidecar=sidecar,
        sidecar_ref=sidecar_ref,
        opportunity=opportunity,
        published_at=published_at,
        evidence=evidence,
        expected_status="open",
    )


def derive_action_revision(
    *,
    sidecar: ResearchActionSynthesisSidecar,
    sidecar_ref: str,
    opportunity: Opportunity,
    published_at: datetime,
    evidence: tuple[ActionBasisEvidence, ...],
) -> DerivedActionPublication:
    return _derive_action_publication(
        sidecar=sidecar,
        sidecar_ref=sidecar_ref,
        opportunity=opportunity,
        published_at=published_at,
        evidence=evidence,
        expected_status="decided",
    )
