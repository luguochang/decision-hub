"""Finite original-reading skill status surface, never a source of qualification."""

from __future__ import annotations

from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ResearchSkillUseEventRecord,
)
from sqlalchemy import select

from .research_skill_package import decoded
from .research_skill_use import event_ref


class SkillWorkflowService:
    def __init__(self, consumption):
        self.consumption, self.nodes, self.use = consumption, consumption.nodes, consumption.use

    def list_in_session(self, session, *, run_id, generation, execution_session_id):
        result = self.use.list_in_session(
            session, run_id=run_id, generation=generation, execution_session_id=execution_session_id
        )
        selection, _, _ = self.use._selection(session, run_id, generation, execution_session_id)
        rows = session.scalars(
            select(ResearchSkillUseEventRecord)
            .where(
                ResearchSkillUseEventRecord.run_id == run_id,
                ResearchSkillUseEventRecord.use_id == selection["use_id"],
            )
            .order_by(
                ResearchSkillUseEventRecord.first_known_at, ResearchSkillUseEventRecord.event_id
            )
            .limit(9)
            .execution_options(populate_existing=True)
        ).all()
        if len(rows) > 8:
            raise ValueError("skill_workflow_stage_census_limit")
        for row in rows:
            reference = {"event_id": row.event_id, "payload_hash": row.payload_hash}
            if row.stage == "selected":
                # The exact selection reader above authenticates this same
                # selected event original and every row identity/hash binding.
                body = decoded(row.payload_json)
                if row.event_id != selection["use_id"] + "_selected":
                    raise ValueError("skill_workflow_extra_selection_event")
            elif row.stage == "loaded":
                body = self.use.read_loaded_in_session(session, run_id=run_id, event_ref=reference)
            elif row.stage == "consumed":
                body = self.consumption.read_consumed_in_session(
                    session, run_id=run_id, event_ref=reference
                )
            elif row.stage == "executed":
                reader = {
                    "spot_evidence": self.nodes.read_gateway_in_session,
                    "features": self.nodes.read_component_in_session,
                    "hypothesis": self.consumption.read_executed_in_session,
                }.get(row.node_id)
                if reader is None:
                    raise ValueError("skill_workflow_node_not_installed")
                body = reader(session, run_id=run_id, event_ref=reference)
            else:
                raise ValueError("skill_workflow_stage_reader_not_installed")
            result["stage_receipts"].append(
                dict(
                    stage=body["stage"],
                    node_id=body["node_id"],
                    event_ref=event_ref(body),
                    first_known_at=body["first_known_at"],
                )
            )
        self.use.packages.contracts.check("list_response_v1", result)
        return result
