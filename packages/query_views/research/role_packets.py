"""Build durable, typed specialist packets for the Decision Desk.

The packets are a public evidence projection.  They make role coverage and
claim changes inspectable without exposing a model's private chain-of-thought
or treating a role's prose as an accepted fact.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from packages.contracts_py.decision_hub_contracts import (
    CausalCase,
    CoverageAssessment,
    EvidenceCandidate,
    ResearchClaimTransition,
    ResearchReview,
    ResearchRolePacket,
    ResearchRoundView,
    ResearchTraceEvent,
)

_ROLE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "market_structure": frozenset({"market_regime_context", "crypto_spot_confirmation"}),
    "derivatives_crowding": frozenset({"derivatives_crowding"}),
    "macro_calendar": frozenset(
        {"event_identity", "policy_or_data_delta", "macro_transmission", "expectation_pricing"}
    ),
    "cross_asset_risk": frozenset({"cross_asset_confirmation"}),
    "narrative_source_critic": frozenset({"market_narrative"}),
    "counter_thesis": frozenset({"counter_thesis"}),
}


def build_role_packets(
    *,
    run_id: str,
    rounds: list[ResearchRoundView],
    evidence: list[EvidenceCandidate],
    coverage: CoverageAssessment | None,
    causal_case: CausalCase | None,
    action_summary: Any,
    reviews: list[ResearchReview],
    trace: list[ResearchTraceEvent],
) -> tuple[list[ResearchRolePacket], list[ResearchClaimTransition]]:
    """Return role packets and explicit round-to-round claim transitions."""

    now = _latest_time(rounds, trace)
    gaps = list(coverage.gaps) if coverage is not None else []
    current_direction = _direction(action_summary, causal_case)
    transitions = _transitions(run_id, rounds, trace, current_direction, now)
    transition_ids = [item.transition_id for item in transitions]
    packets: list[ResearchRolePacket] = []
    for role, requirement_ids in _ROLE_REQUIREMENTS.items():
        refs = [
            item.evidence_id
            for item in evidence
            if item.requirement_id in requirement_ids and item.quality in {"accepted", "candidate"}
        ]
        role_gaps = [item for item in gaps if item.requirement_id in requirement_ids]
        claims = [
            f"{item.source_id}: {item.excerpt[:360]}"
            for item in evidence
            if item.requirement_id in requirement_ids and item.quality in {"accepted", "candidate"}
        ][:20]
        if role == "counter_thesis" and causal_case is not None:
            claims = [item.statement[:500] for item in causal_case.opposite_chain][:20] or claims
            cited = {ref for item in causal_case.opposite_chain for ref in item.evidence_refs}
            # Counter arguments often cite market or macro evidence; they do
            # not require a second copy tagged with the counter role's name.
            cited_evidence = [
                item for item in evidence
                if item.evidence_id in cited and item.quality == "accepted"
            ]
            refs = list(dict.fromkeys([*refs, *(item.evidence_id for item in cited_evidence)]))
            cited_requirements = requirement_ids | {
                item.requirement_id for item in cited_evidence
            }
            role_gaps = [item for item in gaps if item.requirement_id in cited_requirements]
        packets.append(
            _packet(
                run_id=run_id,
                role=role,
                round_number=_latest_round(rounds),
                refs=refs,
                gap_ids=[item.requirement_id for item in role_gaps],
                transition_ids=transition_ids,
                claims=claims,
                now=now,
                conclusion=_conclusion(role, refs, role_gaps),
            )
        )

    overall_refs = list(
        dict.fromkeys(
            item.evidence_id for item in evidence if item.quality in {"accepted", "candidate"}
        )
    )
    all_gap_ids = [item.requirement_id for item in gaps]
    packets.extend(
        [
            _packet(
                run_id=run_id,
                role="manager",
                round_number=_latest_round(rounds),
                refs=overall_refs,
                gap_ids=all_gap_ids,
                transition_ids=transition_ids,
                claims=[
                    f"研究覆盖状态：{coverage.status if coverage is not None else 'unavailable'}",
                    f"当前方向：{current_direction or 'unresolved'}",
                ],
                now=now,
                conclusion="经理只汇总已验证证据、缺口和观点变化，不替证据补写事实。",
            ),
            _packet(
                run_id=run_id,
                role="synthesizer",
                round_number=_latest_round(rounds),
                refs=list(getattr(causal_case, "evidence_refs", []) or overall_refs),
                gap_ids=all_gap_ids,
                transition_ids=transition_ids,
                claims=[
                    *(item.statement for item in (causal_case.main_chain if causal_case else [])),
                    *(
                        item.statement
                        for item in (causal_case.opposite_chain if causal_case else [])
                    ),
                ][:20]
                or [getattr(action_summary, "headline", "尚未形成因果综合")],
                now=now,
                conclusion=(
                    getattr(causal_case, "thesis", None)
                    or getattr(action_summary, "headline", "尚未形成可审计综合")
                ),
            ),
            _packet(
                run_id=run_id,
                role="data_quality",
                round_number=_latest_round(rounds),
                refs=overall_refs,
                gap_ids=all_gap_ids,
                transition_ids=transition_ids,
                claims=[
                    f"硬覆盖：{coverage.hard_coverage_ratio:.0%}" if coverage else "硬覆盖：不可用",
                    f"软覆盖：{coverage.soft_coverage_ratio:.0%}" if coverage else "软覆盖：不可用",
                ],
                now=now,
                conclusion=_conclusion("data_quality", overall_refs, gaps),
            ),
        ]
    )
    if reviews:
        packets.append(
            _packet(
                run_id=run_id,
                role="reviewer",
                round_number=_latest_round(rounds),
                refs=list(dict.fromkeys(ref for review in reviews for ref in review.evidence_refs)),
                gap_ids=all_gap_ids,
                transition_ids=transition_ids,
                claims=[f"{review.resolution}: {review.challenge[:360]}" for review in reviews],
                now=now,
                conclusion="；".join(f"{review.role}={review.resolution}" for review in reviews),
            )
        )
    if coverage is None:
        packets = [
            item.model_copy(update={
                "status": "partial" if item.evidence_refs else "blocked",
                "conclusion": "原始证据要求无法核验；已保留材料，不能据此认定没有缺口。",
            })
            for item in packets
        ]
    return packets, transitions


def _packet(
    *,
    run_id: str,
    role: str,
    round_number: int,
    refs: Iterable[str],
    gap_ids: Iterable[str],
    transition_ids: Iterable[str],
    claims: Iterable[str],
    now: datetime,
    conclusion: str,
) -> ResearchRolePacket:
    refs = list(dict.fromkeys(ref for ref in refs if ref))
    gap_ids = list(dict.fromkeys(item for item in gap_ids if item))
    status = "sufficient" if refs and not gap_ids else "partial" if refs else "blocked"
    packet_seed = f"{run_id}:{role}:{round_number}:{','.join(refs)}:{','.join(gap_ids)}"
    packet_id = f"packet_{hashlib.sha256(packet_seed.encode()).hexdigest()[:24]}"
    return ResearchRolePacket(
        schema_version="role-packet.v1",
        packet_id=packet_id,
        role=role,
        round=max(1, round_number),
        status=status,
        claims=list(dict.fromkeys(item.strip() for item in claims if item and item.strip()))[:20],
        evidence_refs=refs,
        gap_ids=gap_ids,
        transition_ids=list(dict.fromkeys(transition_ids)),
        conclusion=conclusion[:2000] or "没有可发布的结构化结论。",
        created_at=now,
    )


def _transitions(
    run_id: str,
    rounds: list[ResearchRoundView],
    trace: list[ResearchTraceEvent],
    current_direction: str | None,
    now: datetime,
) -> list[ResearchClaimTransition]:
    rows = [item for item in trace if item.event_type == "round_completed"]
    if not rows:
        rows = [item for item in trace if item.event_type == "replan"]
    if not rows:
        rows = []
        for item in rounds:
            rows.append(
                type(
                    "RoundMarker",
                    (),
                    {
                        "reference_id": str(item.round),
                        "summary": f"Round {item.round} coverage recorded.",
                        "occurred_at": item.finished_at,
                    },
                )()
            )
    transitions: list[ResearchClaimTransition] = []
    rounds_by_number = {item.round: item for item in rounds}
    for index, row in enumerate(rows, 1):
        round_number = _positive_int(getattr(row, "reference_id", None), index)
        summary = str(getattr(row, "summary", "研究轮次完成"))
        round_record = rounds_by_number.get(round_number)
        round_refs = list(getattr(round_record, "new_evidence_refs", []) or [])
        round_gaps = (
            [item.requirement_id for item in round_record.coverage.gaps]
            if round_record is not None and round_record.coverage is not None
            else []
        )
        explicit_change = "changed" in summary.lower() or "改变" in summary or "反转" in summary
        seed = f"{run_id}:{round_number}:{index}:{summary}"
        transitions.append(
            ResearchClaimTransition(
                schema_version="claim-transition.v1",
                transition_id=f"transition_{hashlib.sha256(seed.encode()).hexdigest()[:24]}",
                round=round_number,
                from_direction=None,
                to_direction=current_direction,
                changed=explicit_change,
                reason=summary[:2000],
                evidence_refs=round_refs,
                gap_ids=round_gaps,
                created_at=getattr(row, "occurred_at", now) or now,
            )
        )
    return transitions


def _direction(action_summary: Any, causal_case: CausalCase | None) -> str | None:
    outlook = getattr(getattr(action_summary, "research_outlook", None), "outlook", None)
    if outlook in {"bullish", "bearish", "range", "unresolved"}:
        return outlook
    return "unresolved" if causal_case is not None else None


def _conclusion(role: str, refs: list[str], gaps: list[Any]) -> str:
    if not refs and gaps:
        return (
            f"{role} 没有足够可接受证据；缺口："
            f"{', '.join(item.requirement_id for item in gaps)}。"
        )
    if gaps:
        return (
            f"{role} 已有 {len(refs)} 条证据，但仍需补齐："
            f"{', '.join(item.requirement_id for item in gaps)}。"
        )
    if not refs:
        return f"{role} 尚未关联可核验的证据，不能据此认定职责充分。"
    return f"{role} 已关联 {len(refs)} 条证据；没有该职责范围内的开放缺口。"


def _latest_round(rounds: list[ResearchRoundView]) -> int:
    return max((item.round for item in rounds), default=1)


def _latest_time(rounds: list[ResearchRoundView], trace: list[ResearchTraceEvent]) -> datetime:
    values = [item.finished_at for item in rounds] + [item.occurred_at for item in trace]
    return max(values, default=datetime.now(UTC))


def _positive_int(value: Any, fallback: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    return result if result > 0 else fallback
