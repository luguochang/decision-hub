"""Bounded Action views from published originals; no model or remote IO."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionConditionEvaluationRecord,
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
    DecisionSnapshotRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionPlanRevision,
    DecisionSnapshot,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    aware_utc,
    canonical_hash,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchActionSynthesisSidecar
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v1 import (
    DecisionUiBranchV1,
    DecisionUiBranchV2,
    DecisionUiCurrentViewV1,
    DecisionUiCurrentViewV2,
    DecisionUiPlanViewV1,
    DecisionUiTrackingItemV1,
    DecisionUiTrackingViewV1,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    DecisionUiReviewViewV1,
)


def _snapshot(session: Session) -> None:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if connection.dialect.name == "sqlite" and driver is not None and not driver.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _wire(value: Any) -> Any:
    return value.model_dump(mode="json") if value is not None else None


class DecisionProductQuery:
    def __init__(
        self, database: Database, *, enabled: bool, clock: Callable[[], datetime] = utcnow
    ):
        self.database, self.enabled, self.clock = database, enabled, clock

    @staticmethod
    def _scope(scope_id: str, horizon: str) -> None:
        if scope_id not in {spot_scope(asset).scope_id for asset in ("BTC-USDT", "ETH-USDT")}:
            raise KeyError("action_scope_unknown")
        if horizon not in {"30m", "24h", "72h"}:
            raise ValueError("action_horizon_invalid")

    def _tracking(
        self,
        session: Session,
        repository: ActionRepository,
        scope_id: str,
        horizon: str,
        *,
        now: datetime,
        cursor: str | None = None,
    ) -> tuple[int, list[DecisionUiTrackingItemV1], str | None]:
        head, revision, opportunity = (
            ActionPlanHeadRecord,
            ActionPlanRevisionRecord,
            DecisionOpportunityRecord,
        )
        base = (
            select(head.plan_id, head.valid_until)
            .join(revision, revision.revision_id == head.current_revision_id)
            .join(opportunity, opportunity.opportunity_id == revision.opportunity_id)
            .where(
                opportunity.scope_id == scope_id,
                opportunity.horizon == horizon,
                opportunity.cohort == "prospective",
                opportunity.registered_at <= now,
                revision.published_at <= now,
                head.state == "triggered",
            )
        )
        count = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        if cursor is not None:
            anchor = repository.get_head(cursor)
            prior = repository.get_revision(anchor.current_revision_id) if anchor else None
            anchor_opportunity = repository.get_opportunity(prior.opportunity_id) if prior else None
            if (
                anchor is None
                or prior is None
                or prior.scope_id != scope_id
                or prior.horizon != horizon
                or anchor_opportunity is None
                or anchor_opportunity.cohort != "prospective"
                or anchor_opportunity.registered_at > now
                or prior.published_at > now
            ):
                raise ValueError("action_view_cursor_invalid")
            base = base.where(
                or_(
                    head.valid_until > anchor.valid_until,
                    (head.valid_until == anchor.valid_until) & (head.plan_id > anchor.plan_id),
                )
            )
        rows = session.execute(base.order_by(head.valid_until, head.plan_id).limit(11)).all()
        items: list[DecisionUiTrackingItemV1] = []
        publications: dict[str, list[ActionPlanRevision]] = {}
        for row in rows[:10]:
            actual = repository.get_head(row.plan_id)
            current = repository.get_revision(actual.current_revision_id) if actual else None
            if actual is None or current is None:
                raise ValueError("action_view_tracking_original_missing")
            decision = repository.get_snapshot(current.decision_id)
            if decision is None:
                raise ValueError("action_view_decision_missing")
            if decision.id not in publications:
                publications[decision.id] = self._publication(repository, decision, now=now)
                self._sidecar(session, decision)
            if current not in publications[decision.id]:
                raise ValueError("action_view_revision_not_published")
            latest = repository.get_latest_evaluation(
                actual.plan_id, revision_id=current.revision_id
            )
            if latest is not None and latest.evaluated_at > now:
                raise ValueError("action_view_future_evaluation")
            items.append(
                DecisionUiTrackingItemV1.model_validate(
                    {
                        "plan_id": actual.plan_id,
                        "revision_id": current.revision_id,
                        "opportunity_id": current.opportunity_id,
                        "action": current.action,
                        "position_context": current.position_context,
                        "state": actual.state,
                        "monitor_state": actual.monitor_state,
                        "valid_until": current.valid_until,
                        "last_evaluated_at": latest.evaluated_at if latest else None,
                    }
                )
            )
        return count, items, rows[9].plan_id if len(rows) > 10 else None

    def tracking(
        self, scope_id: str, *, horizon: str = "24h", cursor: str | None = None
    ) -> DecisionUiTrackingViewV1:
        self._scope(scope_id, horizon)
        now = aware_utc(self.clock())
        with self.database.session() as session:
            _snapshot(session)
            count, items, following = self._tracking(
                session, ActionRepository(session), scope_id, horizon, now=now, cursor=cursor
            )
            return DecisionUiTrackingViewV1.model_validate(
                {
                    "schema_version": "decision-tracking-view.v1",
                    "scope_id": scope_id,
                    "horizon": horizon,
                    "as_of": now,
                    "count": count,
                    "items": [_wire(value) for value in items],
                    "next_cursor": following,
                }
            )

    @staticmethod
    def _publication(
        repository: ActionRepository, decision: DecisionSnapshot, *, now: datetime
    ) -> list[ActionPlanRevision]:
        opportunity = repository.get_opportunity(decision.opportunity_id)
        receipt = repository.get_command(
            "system:action-publication:v1",
            "publish_" + canonical_hash([decision.run_id, decision.opportunity_id]),
        )
        if (
            receipt is None
            or receipt.operation != "publish"
            or opportunity is None
            or opportunity.status != "decided"
            or decision.known_at > now
        ):
            raise ValueError("action_view_publication_missing")
        expected = {
            "status": "published",
            "opportunity_id": decision.opportunity_id,
            "decision_id": decision.id,
            "snapshot_hash": decision.snapshot_hash,
            "sidecar_ref": decision.source_result_ref,
            "sidecar_hash": decision.source_result_hash,
        }
        if (
            any(receipt.response.get(key) != value for key, value in expected.items())
            or receipt.accepted_at != decision.known_at
        ):
            raise ValueError("action_view_publication_mismatch")
        branches = receipt.response.get("branches")
        if not isinstance(branches, list) or len(branches) != 3:
            raise ValueError("action_view_published_branches_incomplete")
        if receipt.response.get("review_schema") == "action-review-publication.v1":
            from packages.kernel.decision_hub_kernel.application.action_revision import (
                read_review_result,
            )

            read_review_result(repository.session, receipt.request_id, now=now)
        revisions: list[ActionPlanRevision] = []
        for branch in branches:
            if not isinstance(branch, dict) or not isinstance(branch.get("revision_id"), str):
                raise ValueError("action_view_publication_mismatch")
            revision = repository.get_revision(branch["revision_id"])
            if (
                revision is None
                or (revision.decision_id != decision.id and branch.get("disposition") != "retained")
                or revision.opportunity_id != decision.opportunity_id
                or revision.plan_id != branch.get("plan_id")
                or revision.payload_hash != branch.get("revision_hash")
                or revision.position_context != branch.get("position_context")
                or (
                    revision.published_at != receipt.accepted_at
                    and branch.get("disposition") != "retained"
                )
            ):
                raise ValueError("action_view_publication_mismatch")
            if branch.get("disposition") == "retained":
                if receipt.response.get("review_schema") != "action-review-publication.v1":
                    raise ValueError("action_view_retained_receipt_invalid")
                from packages.kernel.decision_hub_kernel.application.action_review import (
                    read_published_branch_in_session,
                )

                read_published_branch_in_session(repository.session, revision, now)
            revisions.append(revision)
        if {value.position_context for value in revisions} != {
            "flat",
            "long",
            "unknown",
        } or receipt.plan_id != revisions[0].plan_id:
            raise ValueError("action_view_published_positions_invalid")
        return revisions

    @staticmethod
    def _sidecar(session: Session, decision: DecisionSnapshot) -> ResearchActionSynthesisSidecar:
        row = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.output_id == decision.source_result_ref,
                RunRoleOutputRecord.run_id == decision.run_id,
            )
        )
        if row is None:
            raise ValueError("action_view_source_original_missing")
        body = json.loads(row.payload_json)
        sidecar = ResearchActionSynthesisSidecar.model_validate(body)
        unsigned = {
            key: value
            for key, value in sidecar.model_dump(mode="json").items()
            if key != "payload_hash"
        }
        if (
            canonical_hash(body) != row.payload_hash
            or canonical_hash(unsigned) != sidecar.payload_hash
            or sidecar.payload_hash != decision.source_result_hash
            or sidecar.scope_id != decision.scope_id
            or sidecar.horizon != decision.horizon
            or sidecar.run_id != decision.run_id
            or sidecar.producer_cutoff != decision.decision_cutoff_at
            or row.schema_version != sidecar.schema_version
        ):
            raise ValueError("action_view_source_original_mismatch")
        return sidecar

    @staticmethod
    def _first_receipt(
        session: Session,
        repository: ActionRepository,
        revision: ActionPlanRevision,
        owner_id: str | None,
        operation: str,
    ) -> datetime | None:
        if owner_id is None:
            return None
        row = session.scalar(
            select(ActionCommandReceiptRecord)
            .where(
                ActionCommandReceiptRecord.owner_id == owner_id,
                ActionCommandReceiptRecord.plan_id == revision.plan_id,
                ActionCommandReceiptRecord.operation == operation,
                func.json_extract(
                    ActionCommandReceiptRecord.response_json, "$.response.head.current_revision_id"
                )
                == revision.revision_id,
            )
            .order_by(ActionCommandReceiptRecord.accepted_at, ActionCommandReceiptRecord.request_id)
            .limit(1)
        )
        if row is None:
            return None
        receipt = repository.get_command(owner_id, row.request_id)
        if receipt is None:
            raise ValueError("action_view_receipt_missing")
        return receipt.accepted_at

    def _branch(
        self,
        session: Session,
        repository: ActionRepository,
        revision: ActionPlanRevision,
        decision: DecisionSnapshot,
        sidecar: ResearchActionSynthesisSidecar,
        *,
        owner_id: str | None,
        now: datetime,
    ) -> DecisionUiBranchV1:
        head = repository.get_head(revision.plan_id)
        if head is None or revision.decision_id != decision.id:
            raise ValueError("action_view_head_missing_or_mismatched")
        matched = [
            item
            for item in sidecar.synthesis.action_intents
            if item.scope_id == revision.scope_id
            and item.horizon == revision.horizon
            and item.position_context == revision.position_context
        ]
        candidate = matched[0] if len(matched) == 1 else None
        if revision.position_context != "unknown" and candidate is None:
            raise ValueError("action_view_position_candidate_missing")
        body = (
            candidate.model_dump(mode="json")
            if candidate
            else {
                "position_context": "unknown",
                "action": "wait",
                "source": sidecar.payload_hash,
            }
        )
        refs = {item.ref: item.hash for item in revision.basis_refs}
        if refs.get(decision.source_result_ref) != sidecar.payload_hash or refs.get(
            decision.source_result_ref + "#position=" + revision.position_context
        ) != canonical_hash(body):
            raise ValueError("action_view_branch_original_mismatch")
        mechanisms = [
            item
            for item in sidecar.synthesis.mechanism_reviews
            if candidate is not None
            and item.review_id == candidate.mechanism_review_ref
            and item.scope_id == revision.scope_id
            and item.horizon == revision.horizon
        ]
        if candidate is not None and len(mechanisms) != 1:
            raise ValueError("action_view_mechanism_missing_or_ambiguous")
        mechanism = mechanisms[0] if mechanisms else None
        steps = []
        if mechanism is not None:
            nodes = {item.id: item for item in mechanism.nodes}
            for edge in mechanism.edges:
                if edge.from_node not in nodes or edge.to_node not in nodes:
                    raise ValueError("action_view_mechanism_node_missing")
                steps.append(
                    {
                        "from_description": nodes[edge.from_node].description,
                        "hypothesis": edge.hypothesis,
                        "to_description": nodes[edge.to_node].description,
                        "support_refs": edge.support_refs,
                        "counter_refs": edge.counter_refs,
                    }
                )
        latest = repository.get_latest_evaluation(
            revision.plan_id, revision_id=revision.revision_id
        )
        if latest is not None and latest.evaluated_at > now:
            raise ValueError("action_view_future_evaluation")
        return DecisionUiBranchV1.model_validate(
            {
                "head": _wire(head),
                "revision": _wire(revision),
                "probability": _wire(candidate.probability) if candidate else None,
                "causal_steps": steps,
                "unresolved": mechanism.unresolved if mechanism else [],
                "priced_in_status": mechanism.priced_in_status if mechanism else "unknown",
                "next_review_at": candidate.next_review_at if candidate else None,
                "latest_evaluation": _wire(latest),
                "expired": now >= revision.valid_until,
                "first_display_at": self._first_receipt(
                    session, repository, revision, owner_id, "display"
                ),
                "first_ack_at": self._first_receipt(session, repository, revision, owner_id, "ack"),
            }
        )

    def reviews(self, opportunity_id: str) -> DecisionUiReviewViewV1:
        """Latest committed analysis, including suggestions not applied to protection."""
        from packages.kernel.decision_hub_kernel.application.action_review import (
            read_published_branch_in_session,
        )
        from packages.kernel.decision_hub_kernel.application.action_revision import (
            latest_review_result,
        )

        now = aware_utc(self.clock())
        with self.database.session() as session:
            _snapshot(session)
            repository = ActionRepository(session)
            opportunity = repository.get_opportunity(opportunity_id)
            if opportunity is None:
                raise KeyError("action_opportunity_not_found")
            self._scope(opportunity.scope_id, opportunity.horizon)
            result = latest_review_result(session, opportunity_id, now=now)
            if result is None:
                return DecisionUiReviewViewV1.model_validate(
                    {
                        "schema_version": "action-review-view.v1",
                        "opportunity_id": opportunity_id,
                        "scope_id": opportunity.scope_id,
                        "horizon": opportunity.horizon,
                        "as_of": now,
                        "review": None,
                    }
                )
            receipt, sidecar = result
            branches = []
            for saved in receipt.response["branches"]:
                revision = repository.get_revision(saved["revision_id"])
                if revision is None:
                    raise ValueError("action_review_result_revision_mismatch")
                read_published_branch_in_session(session, revision, now)
                candidate = next(
                    (
                        item
                        for item in sidecar.synthesis.action_intents
                        if (item.scope_id, item.horizon, item.position_context)
                        == (revision.scope_id, revision.horizon, revision.position_context)
                    ),
                    None,
                )
                mechanism = next(
                    (
                        item
                        for item in sidecar.synthesis.mechanism_reviews
                        if candidate is not None
                        and item.review_id == candidate.mechanism_review_ref
                    ),
                    None,
                )
                branches.append(
                    {
                        **saved,
                        "candidate": candidate.model_dump(mode="json") if candidate else None,
                        "mechanism": mechanism.model_dump(mode="json") if mechanism else None,
                        "current_head": _wire(repository.get_head(revision.plan_id)),
                        "applied": saved["disposition"] == "updated",
                    }
                )
            return DecisionUiReviewViewV1.model_validate(
                {
                    "schema_version": "action-review-view.v1",
                    "opportunity_id": opportunity_id,
                    "scope_id": opportunity.scope_id,
                    "horizon": opportunity.horizon,
                    "as_of": now,
                    "review": {
                        "run_id": sidecar.run_id,
                        "request_id": receipt.request_id,
                        "receipt_hash": receipt.receipt_hash,
                        "sidecar_ref": receipt.response["sidecar_ref"],
                        "sidecar_hash": sidecar.payload_hash,
                        "completed_at": receipt.accepted_at,
                        "status": receipt.response["status"],
                        "branches": branches,
                    },
                }
            )

    def current(
        self, scope_id: str, *, horizon: str = "24h", owner_id: str | None = None
    ) -> DecisionUiCurrentViewV1:
        current = self.current_v2(scope_id, horizon=horizon, owner_id=owner_id)
        body = current.model_dump(mode="json")
        body["schema_version"] = "decision-current-view.v1"
        body["branches"] = [
            {key: value for key, value in branch.items() if key != "decision"}
            for branch in body["branches"]
            if current.decision is not None
            and branch["revision"]["decision_id"] == current.decision.id
        ]
        if len(body["branches"]) != len(current.branches):
            body["gap_codes"].append("action_retained_branches_available_in_v2")
        return DecisionUiCurrentViewV1.model_validate(body)

    def current_v2(
        self, scope_id: str, *, horizon: str = "24h", owner_id: str | None = None
    ) -> DecisionUiCurrentViewV2:
        self._scope(scope_id, horizon)
        now = aware_utc(self.clock())
        base: dict[str, Any] = {
            "schema_version": "decision-current-view.v2",
            "scope_id": scope_id,
            "horizon": horizon,
            "as_of": now,
            "enabled": self.enabled,
            "availability": "unavailable" if self.enabled else "disabled",
            "latest_opportunity": None,
            "decision": None,
            "branches": [],
            "gap_codes": [] if self.enabled else ["action_product_disabled"],
        }
        # Disabled does not delete prior evidence: an existing plan remains readable.
        with self.database.session() as session:
            _snapshot(session)
            repository = ActionRepository(session)
            tracking_count, tracking_items, tracking_next = self._tracking(
                session, repository, scope_id, horizon, now=now
            )
            base.update(
                still_tracking_count=tracking_count,
                still_tracking=[_wire(item) for item in tracking_items],
                tracking_next_cursor=tracking_next,
            )
            opportunity_id = session.scalar(
                select(DecisionOpportunityRecord.opportunity_id)
                .where(
                    DecisionOpportunityRecord.scope_id == scope_id,
                    DecisionOpportunityRecord.horizon == horizon,
                    DecisionOpportunityRecord.cohort == "prospective",
                    DecisionOpportunityRecord.registered_at <= now,
                )
                .order_by(
                    DecisionOpportunityRecord.registered_at.desc(),
                    DecisionOpportunityRecord.opportunity_id.desc(),
                )
                .limit(1)
            )
            if opportunity_id is not None:
                base["latest_opportunity"] = _wire(repository.get_opportunity(opportunity_id))
            decision_id = session.scalar(
                select(DecisionSnapshotRecord.decision_id)
                .join(
                    DecisionOpportunityRecord,
                    DecisionOpportunityRecord.opportunity_id
                    == DecisionSnapshotRecord.opportunity_id,
                )
                .where(
                    DecisionOpportunityRecord.scope_id == scope_id,
                    DecisionOpportunityRecord.horizon == horizon,
                    DecisionOpportunityRecord.cohort == "prospective",
                    DecisionSnapshotRecord.known_at <= now,
                )
                .order_by(
                    DecisionSnapshotRecord.known_at.desc(),
                    DecisionSnapshotRecord.decision_id.desc(),
                )
                .limit(1)
            )
            if decision_id is None:
                base["gap_codes"] = [*base["gap_codes"], "action_decision_not_published"]
                return DecisionUiCurrentViewV2.model_validate(base)
            decision = repository.get_snapshot(decision_id)
            if decision is None:
                raise ValueError("action_view_decision_missing")
            sidecar = self._sidecar(session, decision)
            revisions = self._publication(repository, decision, now=now)
            branches = []
            for revision in revisions:
                branch_decision = repository.get_snapshot(revision.decision_id)
                if branch_decision is None:
                    raise ValueError("action_view_basis_missing")
                branch_sidecar = (
                    sidecar
                    if branch_decision.id == decision.id
                    else self._sidecar(session, branch_decision)
                )
                branch = self._branch(
                    session,
                    repository,
                    revision,
                    branch_decision,
                    branch_sidecar,
                    owner_id=owner_id,
                    now=now,
                )
                branches.append(
                    DecisionUiBranchV2.model_validate(
                        {
                            **branch.model_dump(mode="json"),
                            "decision": _wire(branch_decision),
                        }
                    )
                )
            if any(
                branch.head.current_revision_id != branch.revision.revision_id
                for branch in branches
            ):
                base["gap_codes"].append("action_published_revision_superseded")
            if opportunity_id != decision.opportunity_id:
                base["gap_codes"].append("action_latest_opportunity_unpublished")
            base.update(
                decision=_wire(decision),
                branches=[_wire(branch) for branch in branches],
                availability="available" if self.enabled else "disabled",
            )
            return DecisionUiCurrentViewV2.model_validate(base)

    def plan(
        self,
        plan_id: str,
        *,
        revision_id: str | None = None,
        cursor: str | None = None,
        owner_id: str | None = None,
    ) -> DecisionUiPlanViewV1:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            _snapshot(session)
            repository = ActionRepository(session)
            head = repository.get_head(plan_id)
            if head is None:
                raise KeyError("action_plan_not_found")
            revision = repository.get_revision(revision_id or head.current_revision_id)
            if revision is None or revision.plan_id != plan_id:
                raise KeyError("action_revision_not_found")
            decision = repository.get_snapshot(revision.decision_id)
            opportunity = repository.get_opportunity(revision.opportunity_id)
            if decision is None or opportunity is None:
                raise ValueError("action_view_basis_missing")
            revisions = self._publication(repository, decision, now=now)
            if revision not in revisions:
                raise ValueError("action_view_revision_not_published")
            sidecar = self._sidecar(session, decision)
            query = select(ActionConditionEvaluationRecord).where(
                ActionConditionEvaluationRecord.plan_id == plan_id,
                ActionConditionEvaluationRecord.revision_id == revision.revision_id,
            )
            if cursor is not None:
                anchor = session.get(ActionConditionEvaluationRecord, cursor)
                if anchor is None or (anchor.plan_id, anchor.revision_id) != (
                    plan_id,
                    revision.revision_id,
                ):
                    raise ValueError("action_view_cursor_invalid")
                checked = repository.get_evaluation_for_tick(
                    plan_id, revision_id=revision.revision_id, tick_at=as_utc(anchor.tick_at)
                )
                if checked is None or checked.id != anchor.evaluation_id:
                    raise ValueError("action_view_cursor_invalid")
                query = query.where(
                    or_(
                        ActionConditionEvaluationRecord.tick_at < anchor.tick_at,
                        (ActionConditionEvaluationRecord.tick_at == anchor.tick_at)
                        & (ActionConditionEvaluationRecord.evaluation_id < anchor.evaluation_id),
                    )
                )
            rows = session.scalars(
                query.order_by(
                    ActionConditionEvaluationRecord.tick_at.desc(),
                    ActionConditionEvaluationRecord.evaluation_id.desc(),
                ).limit(21)
            ).all()
            evaluations = [
                repository.get_evaluation_for_tick(
                    plan_id, revision_id=revision.revision_id, tick_at=as_utc(row.tick_at)
                )
                for row in rows[:20]
            ]
            return DecisionUiPlanViewV1.model_validate(
                {
                    "schema_version": "decision-plan-view.v1",
                    "as_of": now,
                    "branch": _wire(
                        self._branch(
                            session,
                            repository,
                            revision,
                            decision,
                            sidecar,
                            owner_id=owner_id,
                            now=now,
                        )
                    ),
                    "decision": _wire(decision),
                    "opportunity": _wire(opportunity),
                    "evaluations": [_wire(value) for value in evaluations],
                    "next_cursor": rows[19].evaluation_id if len(rows) > 20 else None,
                }
            )
