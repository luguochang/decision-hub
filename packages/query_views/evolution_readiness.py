"""Local ledger inventory and configured evaluation input checks; no execution."""

from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import and_, case, func, or_, select, union
from sqlalchemy.orm import Session

from packages.evals.fixture_audit import (
    FixtureSplitAudit,
    audit_fixture_splits,
    default_evolution_fixtures,
)
from packages.kernel.decision_hub_kernel.application.live_observation import NEGATIVE_FEEDBACK
from packages.kernel.decision_hub_kernel.application.model_dispatch import (
    CHILD_ROLE,
    ModelDispatchService,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import ROLE as MODEL_ROLE
from packages.kernel.decision_hub_kernel.application.research_binding import (
    COMPILED_ROLE,
    PACK_SCHEMA,
    TASK_ROLE,
    TASK_SCHEMA,
    has_prepared_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    EXECUTION_SCHEMA,
    INTEGRITY_SCHEMA,
    METHOD_SCHEMA,
    method_v2_view,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
)
from packages.kernel.decision_hub_kernel.application.research_pack import ROLE as PACK_ROLE
from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_policy import policy_prompt_status
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    EvolutionJobRecord,
    FeedbackRecord,
    ProspectiveArmRecord,
    ProspectiveLabelRecord,
    ProspectivePairRecord,
    ProspectiveTrialRecord,
    RunRoleOutputRecord,
    as_utc,
)


class PairedExecutionSummary(BaseModel):
    registered_trials: int
    pairs: int
    predictions_saved: int
    waiting: int
    incomplete: int
    excluded: int
    outcome_scoring_available: bool = True
    labels_completed: int
    labels_waiting: int
    labels_retry: int


def paired_execution_summary(session: Session) -> PairedExecutionSummary:
    rows = (
        session.query(
            ProspectivePairRecord.exclusion_reason,
            func.count(ProspectiveArmRecord.arm),
            func.sum(case((ProspectiveArmRecord.status == "succeeded", 1), else_=0)),
            func.sum(case((ProspectiveArmRecord.status.in_(["pending", "running"]), 1), else_=0)),
        )
        .outerjoin(
            ProspectiveArmRecord, ProspectiveArmRecord.pair_id == ProspectivePairRecord.pair_id
        )
        .group_by(
            ProspectivePairRecord.pair_id,
            ProspectivePairRecord.exclusion_reason,
        )
        .all()
    )
    saved = sum(reason is None and arms == successes == 2 for reason, arms, successes, _ in rows)
    waiting = sum(reason is None and bool(pending) for reason, _, _, pending in rows)
    excluded = sum(reason is not None for reason, _, _, _ in rows)
    label_counts: dict[str, int] = {
        status: count
        for status, count in session.query(
            ProspectiveLabelRecord.status,
            func.count(),
        )
        .group_by(ProspectiveLabelRecord.status)
        .all()
    }
    return PairedExecutionSummary(
        registered_trials=session.query(func.count(ProspectiveTrialRecord.trial_id)).scalar(),
        pairs=len(rows),
        predictions_saved=saved,
        waiting=waiting,
        excluded=excluded,
        incomplete=len(rows) - saved - waiting - excluded,
        labels_completed=label_counts.get("completed", 0),
        labels_retry=label_counts.get("retry", 0),
        labels_waiting=max(
            0,
            len(rows) - excluded - label_counts.get("completed", 0) - label_counts.get("retry", 0),
        ),
    )


class ModelDispatchReadiness(TypedDict):
    status: Literal["not_recorded", "verified", "invalid"]
    binding: dict[str, Any] | None  # Validated binding serialized as public JSON.


class ChildModelDispatchReadiness(TypedDict):
    status: Literal["not_recorded", "recorded", "invalid"]
    bindings: list[dict[str, Any]]


class MethodReadiness(TypedDict):
    recorded_receipts: int
    deployment_connected: bool
    latest_receipt: dict[str, Any] | None  # Original Method's public JSON projection.
    model_dispatch: ModelDispatchReadiness
    child_model_dispatch: ChildModelDispatchReadiness
    request_pack_status: Literal["not_recorded", "verified", "invalid"]
    research_policy_status: Literal["not_recorded", "prepared", "verified", "invalid"]
    latest_receipt_status: Literal["not_recorded", "verified", "invalid"]
    incomplete_execution_run_id: NotRequired[str]
    latest_receipt_unavailable_reason: NotRequired[
        Literal["research_history_recovered_reader_not_terminal"]
    ]
    research_policy_execution_status: NotRequired[Literal["not_available"]]
    research_policy_activation: NotRequired[Literal["prepared"]]


class EvolutionReadiness(BaseModel):
    schema_version: str = "evolution-readiness.v1"
    observed_at: AwareDatetime
    feedback_count: int
    actionable_feedback: int
    unregistered_feedback: int
    jobs: int
    candidates: int
    configured_samples: FixtureSplitAudit
    prospective_collector_available: bool = False
    paired_execution: PairedExecutionSummary
    research_method: MethodReadiness


def _required_utc(value: datetime | None) -> datetime:
    normalized = as_utc(value)
    if normalized is None:
        raise ValueError("research_receipt_original_time_missing")
    return normalized


def evolution_readiness(database: Database, now: datetime) -> EvolutionReadiness:
    with database.session() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        latest_method = (
            session.query(RunRoleOutputRecord)
            .filter_by(role=METHOD_ROLE)
            .order_by(
                RunRoleOutputRecord.created_at.desc(),
                RunRoleOutputRecord.output_id.desc(),
            )
            .first()
        )
        method: MethodReadiness = {
            "recorded_receipts": session.query(func.count(RunRoleOutputRecord.output_id))
            .filter_by(role=METHOD_ROLE)
            .scalar(),
            "deployment_connected": False,
            "latest_receipt": None,
            "model_dispatch": {"status": "not_recorded", "binding": None},
            "child_model_dispatch": {"status": "not_recorded", "bindings": []},
            "request_pack_status": "not_recorded",
            "research_policy_status": "not_recorded",
            "latest_receipt_status": "not_recorded",
        }
        execution_runs = union(
            select(DshSessionLinkRecord.run_id),
            select(DshSessionPromptRecord.run_id),
            select(RunRoleOutputRecord.run_id).where(
                or_(
                    RunRoleOutputRecord.role.startswith("dsh_execution_request:"),
                    RunRoleOutputRecord.role.startswith("dsh_prompt_integrity:"),
                    RunRoleOutputRecord.role.startswith("context_execution_link:"),
                )
            ),
        )
        # Recognize original V2 rows, including partial bundles. Role prefixes
        # alone also occur in V1 and cannot upgrade historical execution.
        latest_orphan = (
            session.query(RunRoleOutputRecord)
            .filter(
                or_(
                    RunRoleOutputRecord.role == COMPILED_ROLE,
                    and_(
                        RunRoleOutputRecord.role == PACK_ROLE,
                        RunRoleOutputRecord.schema_version == PACK_SCHEMA,
                    ),
                    and_(
                        RunRoleOutputRecord.role == TASK_ROLE,
                        RunRoleOutputRecord.schema_version == TASK_SCHEMA,
                    ),
                    and_(
                        RunRoleOutputRecord.role.startswith("dsh_execution_request:"),
                        RunRoleOutputRecord.schema_version.in_(
                            (EXECUTION_SCHEMA, "dsh-execution-request.v3")
                        ),
                    ),
                    and_(
                        RunRoleOutputRecord.role.startswith("dsh_prompt_integrity:"),
                        RunRoleOutputRecord.schema_version == INTEGRITY_SCHEMA,
                    ),
                ),
                ~RunRoleOutputRecord.run_id.in_(
                    select(RunRoleOutputRecord.run_id).where(
                        RunRoleOutputRecord.role == METHOD_ROLE,
                    )
                ),
                RunRoleOutputRecord.run_id.in_(execution_runs),
            )
            .order_by(
                RunRoleOutputRecord.created_at.desc(),
                RunRoleOutputRecord.output_id.desc(),
            )
            .first()
        )
        if latest_orphan and (
            latest_method is None or latest_orphan.created_at >= latest_method.created_at
        ):
            # A prepared bundle alone is legitimately pre-submission. Existing
            # execution rows without its Method are a broken preparation.
            method["latest_receipt_status"] = "invalid"
            method["request_pack_status"] = "invalid"
            method["research_policy_status"] = "invalid"
            method["incomplete_execution_run_id"] = latest_orphan.run_id
            latest_method = None
        if latest_method:
            try:
                prepared = None
                from packages.kernel.decision_hub_kernel.application.research_observability import (
                    requires_tool_admission_proof,
                )

                if (
                    requires_tool_admission_proof(session, latest_method.run_id)
                    or latest_method.schema_version == METHOD_SCHEMA
                    or has_prepared_bundle_in_session(
                        session,
                        latest_method.run_id,
                    )
                    or session.query(RunRoleOutputRecord)
                    .filter(
                        RunRoleOutputRecord.run_id == latest_method.run_id,
                        or_(
                            and_(
                                RunRoleOutputRecord.role.startswith("dsh_execution_request:"),
                                RunRoleOutputRecord.schema_version.in_(
                                    (EXECUTION_SCHEMA, "dsh-execution-request.v3")
                                ),
                            ),
                            and_(
                                RunRoleOutputRecord.role.startswith("dsh_prompt_integrity:"),
                                RunRoleOutputRecord.schema_version == INTEGRITY_SCHEMA,
                            ),
                        ),
                    )
                    .first()
                    is not None
                ):
                    prepared = read_prepared_execution_in_session(session, latest_method.run_id)
                    method["latest_receipt"] = method_v2_view(prepared.method)
                    method["request_pack_status"] = "verified"
                    method["research_policy_status"] = "verified"
                    method["research_policy_execution_status"] = "not_available"
                    method["research_policy_activation"] = (
                        prepared.method.definition.policy_binding.checker_activation
                    )
                else:
                    # V1 remains an explicit original-row read; no V2 downgrade.
                    method["latest_receipt"] = ResearchMethodService._read(  # pyright: ignore[reportPrivateUsage]
                        latest_method,
                    )
                method["latest_receipt_status"] = "verified"
                model_row = (
                    session.query(RunRoleOutputRecord)
                    .filter_by(
                        run_id=latest_method.run_id,
                        role=MODEL_ROLE,
                    )
                    .one_or_none()
                )
                if model_row:
                    method["model_dispatch"]["status"] = "invalid"
                    try:
                        binding = ModelDispatchService._read(  # pyright: ignore[reportPrivateUsage]
                            model_row,
                        )
                        link = session.get(DshSessionLinkRecord, latest_method.run_id)
                        receipt = method["latest_receipt"]
                        if (
                            link
                            and binding.dsh_session_id == link.dsh_session_id
                            and binding.request_hash == link.request_hash == receipt["request_hash"]
                            and binding.plugin_build_hash
                            == link.plugin_build_hash
                            == receipt["definition"]["upstream_identity"]["plugin_build_hash"]
                            and _required_utc(model_row.created_at)
                            >= _required_utc(latest_method.created_at)
                        ):
                            method["model_dispatch"] = {
                                "status": "verified",
                                "binding": binding.model_dump(mode="json"),
                            }
                    except (ValueError, KeyError, TypeError):
                        pass
                child_rows = (
                    session.query(RunRoleOutputRecord)
                    .filter(
                        RunRoleOutputRecord.run_id == latest_method.run_id,
                        RunRoleOutputRecord.role.startswith(CHILD_ROLE),
                    )
                    .order_by(RunRoleOutputRecord.created_at, RunRoleOutputRecord.output_id)
                    .all()
                )
                if child_rows:
                    method["child_model_dispatch"]["status"] = "invalid"
                    try:
                        children = [
                            ModelDispatchService._read(row)  # pyright: ignore[reportPrivateUsage]
                            for row in child_rows
                        ]
                        root = method["model_dispatch"]["binding"]
                        known = {c.execution_session_id for c in children}
                        if root and all(
                            c.dsh_session_id == root["dsh_session_id"]
                            and c.request_hash == root["request_hash"]
                            and c.plugin_build_hash == root["plugin_build_hash"]
                            and c.parent_session_id in known | {root["dsh_session_id"]}
                            for c in children
                        ):
                            method["child_model_dispatch"] = {
                                "status": "recorded",
                                "bindings": [c.model_dump(mode="json") for c in children],
                            }
                    except (ValueError, KeyError, TypeError):
                        pass
                pack_hash = method["latest_receipt"]["definition"].get("request_pack_hash")
                if pack_hash and prepared is None:
                    if method["latest_receipt"]["definition"].get("research_policy_prompt_hash"):
                        method["research_policy_status"] = "invalid"
                    method["request_pack_status"] = "invalid"
                    pack_row = (
                        session.query(RunRoleOutputRecord)
                        .filter_by(run_id=latest_method.run_id, role=PACK_ROLE)
                        .one_or_none()
                    )
                    try:
                        pack = ResearchPackService.read(pack_row) if pack_row else None
                        if (
                            pack is not None
                            and pack_row is not None
                            and pack["definition_hash"] == pack_hash
                            and _required_utc(pack_row.created_at)
                            <= _required_utc(latest_method.created_at)
                        ):
                            method["request_pack_status"] = "verified"
                            prompts = (
                                session.query(DshSessionPromptRecord)
                                .filter_by(run_id=latest_method.run_id)
                                .all()
                            )
                            policy_status = policy_prompt_status(
                                method["latest_receipt"]["definition"],
                                pack["definition"],
                                [prompt.prompt for prompt in prompts],
                            )
                            match policy_status:
                                case "not_recorded" | "prepared" | "verified" | "invalid":
                                    method["research_policy_status"] = policy_status
                                case _:
                                    raise ValueError("research_policy_status_unknown")
                    except ValueError:
                        pass
            except PermissionError as error:
                if str(error) != "research_history_recovered_reader_not_terminal":
                    raise
                # The retained reader still requires a completed/degraded Run
                # with its original artifact. Report that read gap without
                # recovering the receipt or granting execution permission.
                method["latest_receipt"] = None
                method["latest_receipt_status"] = "invalid"
                method["request_pack_status"] = "invalid"
                method["research_policy_status"] = "invalid"
                method["incomplete_execution_run_id"] = latest_method.run_id
                method["latest_receipt_unavailable_reason"] = (
                    "research_history_recovered_reader_not_terminal"
                )
            except (ValueError, KeyError, TypeError):
                method["latest_receipt_status"] = "invalid"
        registered = (
            select(EvolutionJobRecord.job_id)
            .where(EvolutionJobRecord.trigger_key == "feedback:" + FeedbackRecord.feedback_id)
            .exists()
        )
        return EvolutionReadiness(
            observed_at=_required_utc(now),
            feedback_count=session.query(func.count(FeedbackRecord.feedback_id)).scalar(),
            actionable_feedback=session.query(func.count(FeedbackRecord.feedback_id))
            .filter(FeedbackRecord.verdict.in_(NEGATIVE_FEEDBACK))
            .scalar(),
            unregistered_feedback=session.query(func.count(FeedbackRecord.feedback_id))
            .filter(FeedbackRecord.verdict.in_(NEGATIVE_FEEDBACK), ~registered)
            .scalar(),
            jobs=session.query(func.count(EvolutionJobRecord.job_id)).scalar(),
            candidates=session.query(func.count(CandidateVersionRecord.candidate_id)).scalar(),
            configured_samples=audit_fixture_splits(default_evolution_fixtures()),
            paired_execution=paired_execution_summary(session),
            research_method=method,
        )
