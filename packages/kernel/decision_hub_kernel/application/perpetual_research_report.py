"""Read/render/replay helpers for the existing report committer's V2 branch.

No writer, forecast, financial approval or Action publication lives here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewRequestV2,
)

from ..decision.gate import GateResult
from ..persistence.db import (
    ArtifactRecord,
    ExecutionPermissionRecord,
    ForecastRecord,
    GateDecisionRecord,
    PublicationDecisionRecord,
    ResearchResultRecord,
    RunRoleOutputRecord,
    as_utc,
)
from ..ports.research_action_synthesis import (
    normalize_action_synthesis,
    primary_perpetual_synthesis,
)
from .provider_input_receipts import read_provider_emission_in_session, strict_json
from .research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_perpetual_observation,
)
from .research_review import research_result_content_hash

SCHEMA = "research-publication-source.v5"
SCHEMA_V6 = "research-publication-source.v6"
PAYLOAD_FIELDS = (
    "facts",
    "inferences",
    "counter_thesis",
    "uncertainty",
    "transmission_chain",
    "citations",
    "perpetual_synthesis",
)


@dataclass(frozen=True)
class PerpetualReportContent:
    candidate: dict
    gate: GateResult
    binding: dict
    assessment_assumptions_json: str | None = None


def prepare_content(
    session, original, declared, *, purpose="active_research",
    source_schema: str = SCHEMA_V6, decision_at: datetime | None = None,
):
    """Exact native transport/provider join, independent of financial approval."""
    if (
        original.receipt.schema_version != "research-perpetual-graph-final.v1"
        or declared != original.result
    ):
        raise ValueError("perpetual_report_graph_result_mismatch")
    if declared.horizons or declared.causal_case is not None or declared.reviews:
        raise ValueError("perpetual_report_legacy_projection_forbidden")
    mapped = read_mapped_perpetual_observation(
        session, original.receipt.selected_synthesis_observation_ref
    )
    native, context = mapped.perpetual_synthesis, mapped.context
    saved = read_provider_emission_in_session(
        session,
        run_id=context.run_id,
        generation=mapped.mapping_input_generation,
        root_session_id=context.dsh_session_id,
        execution_session_id=context.dsh_session_id,
        call_id=native.selection.call_id,
    )
    if saved is None:
        raise ValueError("perpetual_report_provider_original_missing")
    emission, emission_ref = saved
    from .perpetual_research_binding import (
        read_authenticated_perpetual_execution_in_session,
    )

    request, saved_scope, prepared = read_authenticated_perpetual_execution_in_session(
        session,
        context.run_id,
        mapped.mapping_input_generation,
        purpose=purpose,
    )
    if prepared.execution.dsh_session_id != context.dsh_session_id:
        raise ValueError("perpetual_report_execution_identity_mismatch")
    raw_arguments = strict_json(emission.submit.raw_arguments_json)
    if isinstance(raw_arguments, dict) and raw_arguments.get("action") == "finalize":
        from .perpetual_research_reader import read_perpetual_provider_originals_in_session

        originals, _ = read_perpetual_provider_originals_in_session(
            session, mapped, saved_scope, known_at=original.receipt.recorded_at, purpose=purpose
        )
        # These are normalized reviewed bodies, never alleged raw Provider args.
        arguments = dict(
            candidate=strict_json(originals.synthesis_json),
            review_request=strict_json(originals.review_request_json),
        )
    else:
        arguments = raw_arguments
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"candidate", "review_request"}
            or emission.submit.tool_name != "decision_hub_synthesis_submit"
            or normalize_action_synthesis(arguments["candidate"]) != native.synthesis
            or ActionReviewRequestV2.model_validate(arguments["review_request"])
            != native.review_request
            or arguments["review_request"]["synthesis"] != arguments["candidate"]
            or observation_sha(arguments["candidate"]) != native.raw_candidate_hash
            or emission.candidate_hash != observation_sha(native.synthesis.model_dump(mode="json"))
            or emission.known_at > mapped.recorded_at
            or mapped.recorded_at > original.receipt.recorded_at
        ):
            raise ValueError("perpetual_report_provider_packet_mismatch")
    synthesis = arguments["candidate"]
    review = None
    from .perpetual_review_materials import (
        read_review_originals_in_session,
        validate_analysis_originals,
    )

    if native.authorization is not None:
        from .perpetual_research_reader import _review_originals

        originals = _review_originals(native)
        review = strict_json(originals.authorization_json)
        catalog = strict_json(originals.review_originals_json)
        read_review_originals_in_session(
            session,
            request,
            saved_scope["materials"],
            as_of=datetime.fromisoformat(catalog["as_of"].replace("Z", "+00:00")),
            selected=catalog,
        )
    else:
        catalog = read_review_originals_in_session(
            session, request, saved_scope["materials"], as_of=mapped.recorded_at
        )
    validate_analysis_originals(native.synthesis, catalog)
    binding = dict(
        schema_version="perpetual-report-binding.v1",
        mapped_ref=original.receipt.selected_synthesis_observation_ref.model_dump(mode="json"),
        provider_emission_ref=emission_ref.model_dump(mode="json"),
        synthesis_ref=arguments["review_request"]["synthesis_ref"],
        native_review_verdict=review["verdict"] if review else None,
        financial_qualification="unqualified",
    )
    findings = None
    assessment_assumptions_json = None
    if source_schema == SCHEMA_V6:
        from .perpetual_report_delivery import (
            assess_report_delivery,
            assumptions_from_authenticated_wrapper,
            findings_from_authenticated_wrapper,
        )

        findings = findings_from_authenticated_wrapper(native, review) if review else None
        wrapper_hash = None
        if review:
            if not isinstance(native.review_wrapper_json, str):
                raise ValueError("perpetual_native_review_original_missing")
            wrapper_hash = hashlib.sha256(native.review_wrapper_json.encode()).hexdigest()
            assessment_assumptions_json = assumptions_from_authenticated_wrapper(native)
        binding["native_review_wrapper_sha256"] = wrapper_hash
        delivery = assess_report_delivery(
            synthesis=synthesis, review_request=arguments["review_request"], review=review,
            findings=findings, catalog=catalog, binding=binding,
            decision_at=decision_at or original.receipt.recorded_at,
            assessment_assumptions_json=assessment_assumptions_json,
        )
        binding.update(schema_version="perpetual-report-binding.v2", report_delivery=delivery)
    elif source_schema != SCHEMA:
        raise ValueError("perpetual_report_source_version_unsupported")
    return render_perpetual_report_content(
        synthesis=synthesis,
        review_request=arguments["review_request"],
        review=review,
        catalog=catalog,
        binding=binding,
        findings=findings,
        assessment_assumptions_json=assessment_assumptions_json,
    )


def render_perpetual_report_content(
    *, synthesis, review_request, review, catalog, binding,
    findings: dict[str, Any] | None = None,
    assessment_assumptions_json: str | None = None,
):
    """Pure shared presentation only; caller must authenticate all source originals.

    This function neither persists a report nor grants financial qualification.
    """
    from packages.contracts_py.decision_hub_contracts.models import GateStatus

    from .perpetual_review_materials import validate_analysis_originals

    validate_analysis_originals(synthesis, catalog)
    original_synthesis = synthesis
    from .event_scenario_report import event_scenario_report_projection

    event_summary, event_details, event_citations = event_scenario_report_projection(
        synthesis, catalog
    )
    synthesis = primary_perpetual_synthesis(synthesis)
    estimates = [
        synthesis["event_probability"]["occurrence_distribution"],
        synthesis["event_probability"]["outcome_given_occurrence"],
        synthesis["market_distribution"]["direction_distribution"],
    ]
    labels = ["事件发生概率", "发生条件下的事件结果概率", "目标窗口市场方向概率"]
    outcomes = dict(
        up="上涨",
        down="下跌",
        neutral="区间内",
        occurs="发生",
        not_occurs="不发生",
        occurred="发生",
        not_occurred="不发生",
    )
    probabilities = [
        label
        + "："
        + (
            "不可用，保留未知"
            if estimate["probabilities"] is None
            else "、".join(
                outcomes.get(row["outcome_id"], row["outcome_id"])
                + " "
                + str(Decimal(row["value"]) * 100)
                + "%"
                for row in estimate["probabilities"]
            )
            + "（主观研究估计，未经校准）"
        )
        for label, estimate in zip(labels, estimates, strict=True)
    ]
    candidates = synthesis["candidate_set"]["candidates"]
    sides = dict(flat="空仓", long="多仓", short="空头仓位")
    verbs = dict(
        open="开仓",
        add="加仓",
        reduce="减仓",
        close="平仓",
        conditional="条件建议",
        no_change="维持",
    )
    actions = [
        f"假设当前{sides[row['position_branch']]}：{verbs[row['action']]}，目标{sides[row['target_side']]}；"
        + (
            "仅作单位金额比较，未授权实际数量。"
            if row["size_mode"] == "unit_notional"
            else "数量仍须独立执行校验。"
        )
        for row in candidates
    ]
    by_ref = {
        observation_json(ref): row
        for row in catalog["originals"]
        for ref in (row["source_ref"], row["original_ref"])
        if ref is not None
    }
    analysis = synthesis["analysis"]
    claim_labels = dict(fact="观察事实", inference="机制推断", scenario="条件情景")

    def describe(link):
        support = []
        for ref in link["evidence_refs"]:
            row = by_ref[observation_json(ref)]
            body = strict_json(row["body_json"])
            excerpt = body.get("excerpt") if isinstance(body, dict) else None
            support.append(
                (body.get("source_id", row["role"]) if isinstance(body, dict) else row["role"])
                + "："
                + (excerpt or "已冻结原件，完整内容见来源记录")
                + f" [{ref['ref']}]"
            )
        return (
            f"{claim_labels[link['claim_type']]}：{link['statement']}\n依据："
            + ("；".join(support) or "暂无可核验依据，此项保留为假设")
            + f"\n确认条件：{link['confirmation']}\n失效条件：{link['invalidation']}"
        )

    main = [describe(link) for link in analysis["main_chain"]]
    opposing = [describe(link) for link in analysis["opposite_chain"]]
    cited = {
        observation_json(ref)
        for link in analysis["main_chain"] + analysis["opposite_chain"]
        for ref in link["evidence_refs"]
    }
    citations = [
        row["role"] + " — " + row["source_ref"]["ref"]
        for row in catalog["originals"]
        if any(
            observation_json(ref) in cited
            for ref in (row["source_ref"], row["original_ref"])
            if ref is not None
        )
    ]
    packet: dict[str, Any] = dict(
        schema_version=("perpetual-research-report.v2"
                        if binding["schema_version"] == "perpetual-report-binding.v2"
                        else "perpetual-research-report.v1"),
        binding=binding,
        synthesis=original_synthesis,
        review_request=review_request,
        authorization=review,
        review_originals=catalog,
        financial_qualified=False,
    )
    if binding["schema_version"] == "perpetual-report-binding.v2":
        packet["review_findings"] = findings
    agenda_rows = [
        row
        for row in catalog["originals"]
        if row["source_kind"] == "server_material" and row["role"] == "event_agenda"
    ]
    agenda_lines = []
    if agenda_rows:
        from .perpetual_event_agenda import event_agenda_summary

        agenda_lines = event_agenda_summary(strict_json(agenda_rows[0]["body_json"])["agenda"])
    candidate = dict(
        headline=synthesis["candidate_set"]["context"]["instrument_id"].removeprefix("okx:")
        + " "
        + synthesis["candidate_set"]["context"]["horizon"]
        + " 永续研究候选",
        summary="\n".join(
            [
                (
                    "完整原生研究候选与独立审查已保留；"
                    if review
                    else "完整原生研究候选已保留，尚无匹配独立审查；"
                )
                + "金融资格由单独的原件检查与发布路径判断。",
                analysis["thesis"],
                *probabilities,
                *actions,
                *event_summary,
            ]
        ),
        facts=[
            describe(link)
            for link in analysis["main_chain"] + analysis["opposite_chain"]
            if link["claim_type"] == "fact"
        ],
        inferences=[*main, *actions, *agenda_lines, *event_details],
        counter_thesis="\n\n".join(opposing),
        uncertainty=[
            *synthesis["gaps"],
            *analysis["unresolved_questions"],
            "主观模型路径不等于真实价格、成交或校准结果；账户未知时只保留单位比较。",
        ],
        transmission_chain=main,
        citations=[*citations, *event_citations],
        perpetual_synthesis=packet,
        research_complete=(binding["schema_version"] == "perpetual-report-binding.v2"
                           and binding["report_delivery"]["status"] == "complete"),
    )
    gate = GateResult(
        status=GateStatus.research_only,
        decisions=[
            dict(
                rule_id="perpetual_native_report.v1",
                status="pass",
                reason_code="native_packet_retained_financial_review_separate",
                input_hash=observation_sha(binding),
            )
        ],
    )
    return PerpetualReportContent(candidate, gate, binding, assessment_assumptions_json)


def content_at_decision(
    candidate: dict[str, Any], binding: dict[str, Any], *, decision_at: datetime,
    assessment_assumptions_json: str | None = None,
) -> PerpetualReportContent:
    """Recompute from the same guarded originals at the existing writer's clock."""
    from .perpetual_report_delivery import assess_report_delivery

    packet = candidate["perpetual_synthesis"]
    if packet["binding"] != binding or binding["schema_version"] != "perpetual-report-binding.v2":
        raise ValueError("perpetual_report_delivery_binding_mismatch")
    original_binding = {key: value for key, value in binding.items() if key != "report_delivery"}
    original_binding["schema_version"] = "perpetual-report-binding.v1"
    delivery = assess_report_delivery(
        synthesis=packet["synthesis"], review_request=packet["review_request"],
        review=packet["authorization"], findings=packet["review_findings"],
        catalog=packet["review_originals"], binding=original_binding, decision_at=decision_at,
        assessment_assumptions_json=assessment_assumptions_json,
    )
    refreshed = {
        **original_binding, "schema_version": "perpetual-report-binding.v2",
        "report_delivery": delivery,
    }
    return render_perpetual_report_content(
        synthesis=packet["synthesis"], review_request=packet["review_request"],
        review=packet["authorization"], findings=packet["review_findings"],
        catalog=packet["review_originals"], binding=refreshed,
        assessment_assumptions_json=assessment_assumptions_json,
    )


def read_source(session, *, run, reference, declared, expected_lease_owner, candidate, gate):
    """Archived report replay only; cannot create source rows or renew permission."""
    from .commit import PUBLICATION_SOURCE_ROLE, _artifact_body
    from .research_graph_binding import read_graph_final_in_session

    source = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run.run_id,
            RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
        )
    )
    if source is None:
        raise ValueError("research_publication_source_missing")
    body = strict_json(source.payload_json)
    original = read_graph_final_in_session(
        session,
        run_id=run.run_id,
        reference=reference,
        expected_lease_owner=None,
        require_current_permission=False,
    )
    source_schema = body.get("schema_version")
    result = session.get(ResearchResultRecord, run.run_id, populate_existing=True)
    artifact = session.get(ArtifactRecord, run.artifact_id, populate_existing=True)
    decision = session.get(PublicationDecisionRecord, run.artifact_id, populate_existing=True)
    permission = session.get(ExecutionPermissionRecord, run.artifact_id, populate_existing=True)
    if artifact is None or result is None or decision is None or permission is None:
        raise ValueError("perpetual_report_committed_original_missing")
    recorded = as_utc(artifact.created_at)
    content = prepare_content(session, original, declared, purpose="frozen_read",
                              source_schema=source_schema, decision_at=recorded)
    expected = dict(
        schema_version=source_schema,
        run_id=run.run_id,
        event_id=run.event_id,
        graph_final_ref=reference.model_dump(mode="json"),
        graph_result_hash=observation_sha(original.result.model_dump(mode="json")),
        declared_result_hash=observation_sha(declared.model_dump(mode="json")),
        result_schema_version=result.schema_version,
        result_payload_hash=result.payload_hash,
        result_created_at=as_utc(result.created_at).isoformat(),
        artifact_id=artifact.artifact_id,
        artifact_hash=observation_sha(_artifact_body(artifact)),
        review_binding=None,
        calibration_binding=[],
        gate=dict(status=content.gate.status.value, decisions=content.gate.decisions),
        candidate=content.candidate,
        candidate_gate=dict(status=content.gate.status.value, decisions=content.gate.decisions),
        publication_owner=body.get("publication_owner"),
        recorded_at=recorded.isoformat(),
        perpetual_binding=content.binding,
    )
    if source_schema == SCHEMA_V6:
        expected["report_delivery"] = content.binding["report_delivery"]
    decisions = session.scalars(
        select(GateDecisionRecord)
        .where(GateDecisionRecord.artifact_id == artifact.artifact_id)
        .order_by(GateDecisionRecord.id)
    ).all()
    if (
        body != expected
        or source.payload_json != observation_json(body)
        or source.schema_version != source_schema
        or source.payload_hash != hashlib.sha256(source.payload_json.encode()).hexdigest()
        or source.output_id != "publication_" + source.payload_hash[:40]
        or not isinstance(body["publication_owner"], str)
        or not body["publication_owner"]
        or expected_lease_owner is not None
        and expected_lease_owner != body["publication_owner"]
        or run.status != ("completed" if content.candidate["research_complete"] else "degraded")
        or as_utc(run.finished_at) != recorded
        or as_utc(source.created_at) != recorded
        or artifact.run_id != run.run_id
        or artifact.event_id != run.event_id
        or artifact.gate_status != "research_only"
        or artifact.headline != content.candidate["headline"]
        or artifact.summary != content.candidate["summary"]
        or json.loads(artifact.payload_json)
        != {key: content.candidate[key] for key in PAYLOAD_FIELDS}
        or result.payload_hash != observation_sha(strict_json(result.payload_json))
        or strict_json(result.payload_json) != declared.model_dump(mode="json")
        or decision.run_id != run.run_id
        or decision.status != "research_only"
        or decision.result_hash != research_result_content_hash(declared)
        or json.loads(decision.decisions_json) != content.gate.decisions
        or json.loads(decision.published_horizons_json) != []
        or json.loads(decision.review_refs_json) != []
        or as_utc(decision.decided_at) != recorded
        or permission.allowed
        or [
            dict(
                rule_id=row.rule_id,
                status=row.status,
                reason_code=row.reason_code,
                input_hash=row.input_hash,
            )
            for row in decisions
        ]
        != content.gate.decisions
        or session.scalar(
            select(ForecastRecord.forecast_id)
            .where(ForecastRecord.artifact_id == artifact.artifact_id)
            .limit(1)
        )
        is not None
        or candidate is not None
        and candidate != content.candidate
        or gate is not None
        and gate != content.gate
    ):
        raise ValueError("perpetual_report_committed_original_mismatch")
    return artifact.artifact_id, content.gate
