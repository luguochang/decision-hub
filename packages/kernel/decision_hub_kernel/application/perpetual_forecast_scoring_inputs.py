"""Versioned v2 score inputs on the original Task delivery/experience boundary."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence

from ..persistence.db import RunRoleOutputRecord
from ..persistence.original_preparation import prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_forecast_scoring import ROLE, SCHEMA, SOURCE
from . import perpetual_forecast_experience as legacy
from .perpetual_forecast_scoring import read_scoring_experience_in_session
from .perpetual_research_binding import (
    canonical,
    read_perpetual_admission_identity_in_session,
)


def scoring_context_in_session(
    session: Any,
    *,
    owner_id: str,
    instrument_id: str,
    horizon: str,
    cutoff_at: datetime,
    limit: int = 3,
) -> tuple[ResearchInputEvidence, ...]:
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("perpetual_scoring_context_limit_invalid")
    cutoff = aware_utc(cutoff_at)
    payload = RunRoleOutputRecord.payload_json
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.role == ROLE,
            RunRoleOutputRecord.schema_version == SCHEMA,
            func.json_extract(payload, "$.owner_id") == owner_id,
            func.json_extract(payload, "$.instrument_id") == instrument_id,
            func.json_extract(payload, "$.horizon") == horizon,
            RunRoleOutputRecord.created_at <= cutoff,
        )
        .order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id.desc())
        .limit(limit)
    ).all()
    inputs = []
    for row in rows:
        original = read_scoring_experience_in_session(
            session, {"ref": row.output_id, "hash": row.payload_hash}, cutoff_at=cutoff
        )
        value = original.body
        summary = {
            key: value[key]
            for key in (
                "instrument_id",
                "horizon",
                "final_target_ref",
                "target_family_ref",
                "label_rule_ref",
                "method_ref",
                "category",
                "return_fraction",
                "multiclass_brier",
                "log_loss",
                "reliability_observations",
                "strata",
                "known_at",
                "recorded_at",
                "gaps",
            )
        }
        inputs.append(
            ResearchInputEvidence(
                evidence_id=original.ref.ref,
                kind="document",
                authority="unverified",
                source_id=SOURCE,
                source_url=None,
                published_at=None,
                observed_at=datetime.fromisoformat(value["known_at"].replace("Z", "+00:00")),
                received_at=datetime.fromisoformat(value["recorded_at"].replace("Z", "+00:00")),
                content_hash=original.ref.hash,
                excerpt="历史方向预测与实际结果；不是交易收益，不是技能指令，不授金融资格。"
                + canonical(summary),
            )
        )
    return tuple(inputs)


def prepare_perpetual_experience_inputs(database: Any, admission: dict[str, Any]) -> Any:
    def read(session: Any) -> tuple[ResearchInputEvidence, ...]:
        from .perpetual_paper_experience import paper_experience_context_in_session

        partition, context = admission["partition"], admission["context"]
        cutoff = datetime.fromisoformat(admission["admitted_at"])
        paper = paper_experience_context_in_session(
            session,
            owner_id=admission["owner_id"],
            account_kind=partition["account_kind"],
            account_id=partition["account_id"],
            instrument_id=partition["instrument_id"],
            profile_ref=context["profile_ref"],
            horizon=context["horizon"],
            cutoff_at=cutoff,
            limit=2,
        )
        arguments: dict[str, Any] = dict(
            owner_id=admission["owner_id"],
            instrument_id=partition["instrument_id"],
            horizon=context["horizon"],
            cutoff_at=cutoff,
            limit=3 - len(paper),
        )
        forecast: Sequence[ResearchInputEvidence] = (
            *legacy.perpetual_experience_context_in_session(session, **arguments),
            *scoring_context_in_session(session, **arguments),
        )
        forecast = sorted(
            forecast, key=lambda item: (item.received_at, item.evidence_id), reverse=True
        )
        return (*paper, *forecast[: 3 - len(paper)])

    return prepare_originals(database, read)


def freeze_experience_selection_in_session(
    session: Any,
    request: Any,
    candidates: Sequence[ResearchInputEvidence],
    *,
    recorded_at: datetime,
) -> None:
    if not any(item.source_id == SOURCE for item in candidates):
        legacy.freeze_experience_selection_in_session(
            session, request, candidates, recorded_at=recorded_at
        )
        return
    from ..ports.perpetual_paper_experience import SOURCE as PAPER_SOURCE

    effective = {item.evidence_id for item in request.input_evidence}
    identity = read_perpetual_admission_identity_in_session(session, request.run_id)
    if identity is None:
        raise ValueError("perpetual_scoring_selection_admission_missing")
    admission = identity[0]
    kinds = {
        SOURCE: "forecast_terminal_direction_v2",
        PAPER_SOURCE: "paper_episode_net_cashflow",
        "perpetual_forecast_experience.v1": "forecast_terminal_direction",
    }
    if any(item.source_id not in kinds for item in candidates):
        raise ValueError("perpetual_scoring_selection_source_invalid")
    references = [
        {"ref": item.evidence_id, "hash": item.content_hash, "kind": kinds[item.source_id]}
        for item in candidates
    ]
    body = dict(
        schema_version="perpetual-experience-task-selection.v3",
        run_id=request.run_id,
        request_id=request.request_id,
        task_hash=canonical_hash(request.model_dump(mode="json")),
        strategy="perpetual-history-inputs.v3",
        scope=admission["context"],
        cutoff_at=admission["admitted_at"],
        candidates=references,
        selected=[item for item in references if item["ref"] in effective],
        excluded=[
            {**item, "reason": "context_selector_excluded"}
            for item in references
            if item["ref"] not in effective
        ],
        provider_input_proven=False,
        skill_consumed=False,
        recorded_at=aware_utc(recorded_at).isoformat().replace("+00:00", "Z"),
    )
    session.add(
        RunRoleOutputRecord(
            output_id="perp_experience_selection_v3_" + canonical_hash(request.run_id),
            run_id=request.run_id,
            role="perpetual_experience_task_selection_v3",
            schema_version=body["schema_version"],
            payload_json=canonical(body),
            payload_hash=canonical_hash(body),
            created_at=recorded_at,
        )
    )
