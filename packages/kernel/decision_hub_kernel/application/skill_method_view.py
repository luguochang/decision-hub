"""Read-only user projection of authenticated method-stage originals."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..persistence.action_records import (
    ActionPlanRevisionRecord,
    DecisionSnapshotRecord,
    ResearchSkillUseEventRecord,
)
from ..persistence.action_repository import ActionRepository
from ..persistence.db import Database, DshSessionLinkRecord, RunRecord, RunRoleOutputRecord, utcnow
from ..ports.research_method_view import ResearchMethodViewV2
from .action_review import read_published_branch_in_session
from .research_graph_history_versions import read_retained_skill_method_in_session
from .research_skill_service import ResearchSkillService
from .skill_method_history import read_current_method_in_session


class StageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["selected", "loaded", "executed", "consumed"]
    node_id: str
    event_ref: dict[str, str]
    first_known_at: AwareDatetime


class MethodItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    purpose: str
    skill_version_id: str
    version_hash: str
    origin: Literal["baseline_registered"]
    financial_effect: Literal["unverified"]
    generation: int
    selected: bool
    loaded: bool
    executed: bool
    consumed: bool
    required_nodes: list[str]
    executed_nodes: list[str]
    receipts: list[StageReceipt]


class MethodView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["research-method-view.v1"] = "research-method-view.v1"
    run_id: str
    revision_id: str | None
    run_status: str
    current_generation: int | None
    producer_generation: int | None
    status: Literal["not_selected", "authenticated"]
    adoption: Literal["not_bound_to_action", "adopted", "not_adopted", "generation_unverified"]
    methods: list[MethodItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class SkillMethodQuery:
    def __init__(
        self,
        database: Database,
        service: ResearchSkillService,
        *,
        clock: Callable[[], datetime] = utcnow,
        trial_projection=None,
        generation_reader=None,
    ):
        self.database, self.service, self.clock = database, service, clock
        self.trial_projection, self.generation_reader = trial_projection, generation_reader

    def frontier(self, run_id: str, revision_id: str | None = None) -> str | None:
        """Return a cheap freshness key, or ``None`` before method selection.

        The full retained-original verification intentionally remains strict and
        expensive.  Public polling uses this key to share exactly one physical
        verification until a Run, stage receipt, or adopted revision changes.
        """

        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise ValueError("method_run_missing")
            selection = session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == "research_skill_selection",
                )
            )
            if selection is None:
                return None
            link = session.get(DshSessionLinkRecord, run_id)
            outputs = session.execute(
                select(
                    RunRoleOutputRecord.output_id,
                    RunRoleOutputRecord.role,
                    RunRoleOutputRecord.schema_version,
                    RunRoleOutputRecord.payload_hash,
                )
                .where(RunRoleOutputRecord.run_id == run_id)
                .order_by(RunRoleOutputRecord.role, RunRoleOutputRecord.output_id)
            ).all()
            stages = session.execute(
                select(
                    ResearchSkillUseEventRecord.event_id,
                    ResearchSkillUseEventRecord.generation,
                    ResearchSkillUseEventRecord.stage,
                    ResearchSkillUseEventRecord.node_id,
                    ResearchSkillUseEventRecord.payload_hash,
                )
                .where(ResearchSkillUseEventRecord.run_id == run_id)
                .order_by(
                    ResearchSkillUseEventRecord.generation,
                    ResearchSkillUseEventRecord.event_id,
                )
            ).all()
            revision = (
                session.get(ActionPlanRevisionRecord, revision_id)
                if revision_id is not None
                else None
            )
            decision = (
                session.get(DecisionSnapshotRecord, revision.decision_id)
                if revision is not None
                else None
            )
            body = {
                "run": [
                    run.run_id,
                    run.status,
                    run.error_code,
                    run.artifact_id,
                    run.updated_at.isoformat(),
                ],
                "link": None
                if link is None
                else [
                    link.dsh_session_id,
                    link.request_hash,
                    link.generation,
                    link.state,
                    link.last_seq,
                    link.result_hash,
                    link.error_code,
                    link.updated_at.isoformat(),
                ],
                "outputs": [list(row) for row in outputs],
                "stages": [list(row) for row in stages],
                "revision": None
                if revision is None
                else [
                    revision.revision_id,
                    revision.decision_id,
                    revision.payload_hash,
                    revision.published_at.isoformat(),
                ],
                "decision": None
                if decision is None
                else [decision.decision_id, decision.run_id, decision.payload_hash],
            }
            encoded = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            return hashlib.sha256(encoded).hexdigest()

    def read(
        self, run_id: str, revision_id: str | None = None
    ) -> MethodView | ResearchMethodViewV2:
        with self.database.session() as session:
            # SQLite implicit SQLAlchemy begin does not create a physical snapshot.
            session.connection().exec_driver_sql("BEGIN")
            run = session.get(RunRecord, run_id)
            if run is None:
                raise ValueError("method_run_missing")
            link = session.get(DshSessionLinkRecord, run_id)
            view = MethodView(
                run_id=run_id,
                revision_id=revision_id,
                run_status=run.status,
                current_generation=link.generation if link else None,
                producer_generation=None,
                status="not_selected",
                adoption="not_bound_to_action",
            )
            sidecar = None
            if revision_id is not None:
                repo = ActionRepository(session)
                revision = repo.get_revision(revision_id)
                decision = repo.get_snapshot(revision.decision_id) if revision else None
                if revision is None or decision is None or decision.run_id != run_id:
                    raise ValueError("method_revision_run_mismatch")
                _, sidecar, _ = read_published_branch_in_session(session, revision, self.clock())
                view.producer_generation = sidecar.generation
                view.adoption = "not_adopted"
            # Absence is distinct from an invalid original: never suppress reader failures.
            row = session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == "research_skill_selection",
                )
            )
            if row is None:
                view.gaps = ["method_selection_missing"]
                return view
            trial = None
            is_trial = row.schema_version == "research-skill-selection.v2"
            requested_generation = (
                sidecar.generation if sidecar else (link.generation if link else 1)
            )
            if requested_generation > 1 and (self.generation_reader is not None or is_trial):
                if self.generation_reader is None:
                    raise ValueError("method_generation_reader_unavailable")
                originals = self.generation_reader(
                    session,
                    run_id=run_id,
                    generation=requested_generation,
                    service=self.service,
                )
            elif is_trial:
                # Trial reads use the real selection service and compiler. The
                # legacy baseline bridge grants no trial admission permission.
                originals = read_current_method_in_session(session, run_id, self.service)
            else:
                originals = read_retained_skill_method_in_session(
                    session,
                    run_id=run_id,
                    service=self.service,
                )
                if originals is None:
                    originals = read_current_method_in_session(session, run_id, self.service)
            if is_trial:
                if self.trial_projection is None:
                    raise ValueError("method_trial_reader_unavailable")
                trial = self.trial_projection.read_in_session(
                    session,
                    run_id=run_id,
                    selection_id=row.output_id,
                )
            selection, listing = originals["selection"], originals["listing"]
            generation = selection["generation"]
            required = [n["id"] for n in originals["workflow"]["nodes"]]
            receipts = [StageReceipt.model_validate(item) for item in listing["stage_receipts"]]
            executed = sorted({r.node_id for r in receipts if r.stage == "executed"})
            stages = {r.stage for r in receipts}
            known_method = originals["version"]["skill_id"] == "causal-synthesis"
            method = MethodItem(
                name="因果链与反证研究" if known_method else "已登记研究方法",
                purpose=(
                    "获取市场事实、计算冻结特征、检验主反机制，并记录具体采用位置。"
                    if known_method
                    else "按已登记的完整方法与工作流执行研究。"
                ),
                skill_version_id=selection["skill_version_id"],
                version_hash=selection["version_hash"],
                origin="baseline_registered",
                financial_effect="unverified",
                generation=generation,
                selected="selected" in stages,
                loaded="loaded" in stages,
                executed=bool(required) and set(executed) == set(required),
                consumed="consumed" in stages,
                required_nodes=required,
                executed_nodes=executed,
                receipts=receipts,
            )
            view.status, view.methods = "authenticated", [method]
            if link is not None and link.generation != generation:
                view.gaps.append("current_generation_adoption_unverified")
            if sidecar is not None:
                if sidecar.generation != generation:
                    view.adoption = "generation_unverified"
                    view.gaps.append("action_producer_generation_mismatch")
                else:
                    consumed = {
                        (r.event_ref["event_id"], r.event_ref["payload_hash"])
                        for r in receipts
                        if r.stage == "consumed"
                    }
                    matches = [
                        r
                        for r in sidecar.synthesis.skill_consumption_refs
                        if r.version_id == method.skill_version_id
                        and r.version_hash == method.version_hash
                        and (r.consumption_receipt_ref, r.consumption_receipt_hash) in consumed
                    ]
                    if matches:
                        view.adoption = "adopted"
                    else:
                        view.gaps.append("action_consumption_reference_missing")
            if trial is not None or generation > 1:
                body = view.model_dump(mode="json")
                body["schema_version"] = "research-method-view.v2"
                item = body["methods"][0]
                item.update(
                    origin="trial_registered" if trial else "baseline_registered",
                    trial=trial.model_dump(mode="json", by_alias=True) if trial else None,
                    generation_binding_ref=originals.get("binding_ref"),
                    original_selection_ref=selection.get("original_selection_ref"),
                )
                if trial is not None:
                    item.update(
                        name="结果复盘后的试用方法",
                        purpose="参照预置方法形成候选，经行为对照后在有限范围试用；金融效果尚未验证。",
                    )
                return ResearchMethodViewV2.model_validate(body)
            return view
