"""Bounded evaluation discovery and immutable non-trade denominator receipts.

These receipts record what was observed, not fill permission or fictitious P&L.
The pending execution-intent queue is independent of this discovery cursor.
"""

from __future__ import annotations

import json
from datetime import timedelta

from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionObservationHeadRecord as Head,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionObservationRecord as Record,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionPortfolioCursorRecord as Cursor,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
    ExecutionRepository,
    canonical,
)
from packages.kernel.decision_hub_kernel.persistence.action_paper_records import AccountHead
from sqlalchemy import and_, func, or_, select

from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionConditionEvaluationRecord as Evaluation,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionPlanRevisionRecord as Revision,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    DecisionOpportunityRecord as Opportunity,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    begin_immediate,
    prepare_originals,
)
from packages.kernel.decision_hub_kernel.ports.action_monitor import ConditionEvaluationV2
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def opportunity_basis(value):
    return {
        key: item
        for key, item in value.model_dump(mode="json").items()
        if key not in {"status", "reason"}
    }


class ExecutionObservationService:
    def __init__(self, execution):
        self.execution = execution
        self.database = execution.database

    def _source(self, session, portfolio_id, source_kind, source_id):
        repository = ExecutionRepository(session)
        account = repository.account(portfolio_id)
        identity = account.portfolio_identity
        protocol = repository.protocol(identity.protocol_ref)
        actions = ActionRepository(session)
        revision = evaluation = None
        if source_kind == "evaluation":
            row = session.get(Evaluation, source_id, populate_existing=True)
            if row is None:
                raise ValueError("execution_observation_evaluation_missing")
            evaluation = actions.get_evaluation_for_tick(
                row.plan_id, revision_id=row.revision_id, tick_at=as_utc(row.tick_at)
            )
            if evaluation is None or evaluation.id != source_id:
                raise ValueError("execution_observation_evaluation_mismatch")
            revision = actions.get_revision(row.revision_id)
            if revision is None:
                raise ValueError("execution_observation_revision_missing")
            opportunity = actions.get_opportunity(revision.opportunity_id)
            source_hash = evaluation.evaluation_hash
        elif source_kind == "opportunity":
            opportunity = actions.get_opportunity(source_id)
            if opportunity is None:
                raise ValueError("execution_observation_opportunity_missing")
            source_hash = canonical_hash(opportunity_basis(opportunity))
        else:
            raise ValueError("execution_observation_kind_invalid")
        if opportunity is None:
            raise ValueError("execution_observation_opportunity_missing")
        if (
            opportunity.scope_id,
            opportunity.horizon,
            opportunity.recipe_hash,
            opportunity.target_spec_hash,
        ) != (identity.scope_id, identity.horizon, identity.method_hash, identity.target_spec_hash):
            raise ValueError("execution_observation_portfolio_scope_mismatch")
        if not protocol.T0 <= opportunity.registered_at < protocol.T1:
            raise ValueError("execution_observation_opportunity_outside_cohort")
        return account, protocol, opportunity, revision, evaluation, source_hash

    def read_in_session(self, session, observation_id):
        row = session.get(Record, observation_id, populate_existing=True)
        if row is None:
            raise ValueError("execution_observation_missing")
        body = json.loads(row.payload_json)
        if (
            canonical(body) != row.payload_json
            or body.get("payload_hash") != row.payload_hash
            or canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
            != row.payload_hash
        ):
            raise ValueError("execution_observation_hash_mismatch")
        if (
            body["id"],
            body["portfolio_id"],
            body["source_kind"],
            body["source_id"],
            body["version"],
            body["status"],
            body["observed_at"],
        ) != (
            row.observation_id,
            row.portfolio_id,
            row.source_kind,
            row.source_id,
            row.version,
            row.status,
            as_utc(row.observed_at).isoformat(),
        ):
            raise ValueError("execution_observation_projection_mismatch")
        _, _, opportunity, _, _, source_hash = self._source(
            session, row.portfolio_id, row.source_kind, row.source_id
        )
        if body["source_hash"] != source_hash or opportunity.id != row.opportunity_id:
            raise ValueError("execution_observation_original_changed")
        if body["intent_ref"]:
            intent = ExecutionRepository(session).intent(body["intent_ref"].removeprefix("intent:"))
            if (
                intent.payload_hash != body["intent_hash"]
                or intent.portfolio_id != row.portfolio_id
                or intent.evaluation_ref != row.source_id
            ):
                raise ValueError("execution_observation_intent_mismatch")
        return body

    def read(self, observation_id):
        prepared = prepare_originals(
            self.database, lambda session: self.read_in_session(session, observation_id)
        )
        return {
            "observation": prepared.value,
            "available_at": self.execution._now(),
            "is_trade_outcome": False,
        }

    def observe(self, portfolio_id, *, source_kind, source_id, checkpoint=None):
        now = self.execution._now()
        with self.database.session() as session:
            old = session.get(Head, (portfolio_id, source_kind, source_id))
            if old and (old.next_due_at is None or as_utc(old.next_due_at) > now):
                return self.read_in_session(session, old.observation_id)
        prepared = prepare_originals(
            self.database,
            lambda session: self._source(session, portfolio_id, source_kind, source_id),
        )
        account, protocol, opportunity, revision, evaluation, source_hash = prepared.value
        status = "not_actionable"
        reason = "no_new_condition_action"
        intent = None
        retry = False
        if source_kind == "opportunity":
            if now < opportunity.time_end:
                raise ValueError("execution_observation_opportunity_not_due")
            with self.database.session() as session:
                any_plan = session.scalar(
                    select(Revision.revision_id)
                    .where(Revision.opportunity_id == opportunity.id)
                    .limit(1)
                )
            if any_plan:
                raise ValueError("execution_observation_plan_exists")
            status = "no_plan"
            reason = "opportunity_ended_without_published_plan"
        elif not isinstance(evaluation, ConditionEvaluationV2):
            status = "blocked"
            reason = "monitor_v2_original_required"
        else:
            if revision is None or evaluation is None:
                raise ValueError("execution_observation_revision_evaluation_required")
            identity = account.portfolio_identity
            with self.database.session() as session:
                episode = ExecutionRepository(session).open_episode(portfolio_id)
            selected = revision.position_context == (
                "long" if identity.scenario == "existing_long" else "flat"
            )
            if (
                identity.scenario == "cash"
                and revision.position_context == "long"
                and episode is not None
            ):
                selected = revision.published_at > episode.entry_at
            if not selected:
                reason = "position_branch_not_applicable_at_publication"
            elif evaluation.state_before == evaluation.state_after:
                reason = "no_new_condition_action"
                if identity.scenario == "existing_long" and episode is None:
                    try:
                        self.execution.activate_inherited(
                            portfolio_id,
                            plan_id=revision.plan_id,
                            revision_id=revision.revision_id,
                            evaluation_id=evaluation.id,
                            checkpoint=checkpoint,
                        )
                    except ValueError as error:
                        status = "blocked"
                        reason = str(error)
            else:
                try:
                    if identity.scenario == "existing_long" and episode is None:
                        self.execution.activate_inherited(
                            portfolio_id,
                            plan_id=revision.plan_id,
                            revision_id=revision.revision_id,
                            evaluation_id=evaluation.id,
                            checkpoint=checkpoint,
                        )
                    intent = self.execution.observe_evaluation(
                        portfolio_id,
                        plan_id=revision.plan_id,
                        revision_id=revision.revision_id,
                        evaluation_id=evaluation.id,
                        checkpoint=checkpoint,
                    )
                    status = "intent_created"
                    reason = "authenticated_condition_intent"
                except ValueError as error:
                    reason = str(error)
                    if reason == "action_execution_no_new_condition_action":
                        status = "not_actionable"
                    elif reason == "execution_exact_owner_display_required" and now < min(
                        revision.valid_until, opportunity.time_end
                    ):
                        status = "waiting_display"
                        retry = True
                    else:
                        status = (
                            "unknown"
                            if any(
                                word in reason
                                for word in (
                                    "hash",
                                    "original",
                                    "missing",
                                    "complete_21h",
                                    "mismatch",
                                )
                            )
                            else "blocked"
                        )
        # Do not trust a caller-supplied status/intent. All fields above originate
        # in this identity-only read and the execution service's authenticated seam.
        with self.database.session() as session:
            begin_immediate(session)
            old = session.get(Head, (portfolio_id, source_kind, source_id), populate_existing=True)
            if old and (old.next_due_at is None or as_utc(old.next_due_at) > now):
                return self.read_in_session(session, old.observation_id)
            # Account may legitimately change in activate_inherited / intent
            # processing; recheck immutable source identity, never stale account.
            actual = self._source(session, portfolio_id, source_kind, source_id)
            if actual[-1] != source_hash:
                raise ValueError("execution_observation_source_changed")
            if intent:
                persisted = ExecutionRepository(session).intent(intent.id)
                if persisted != intent:
                    raise ValueError("execution_observation_intent_changed")
            version = old.version + 1 if old else 1
            observed = self.execution._now()
            previous = self.read_in_session(session, old.observation_id) if old else None
            body = dict(
                schema_version="action-execution-observation.v1",
                id="execution_observation_"
                + canonical_hash([portfolio_id, source_kind, source_id, version]),
                portfolio_id=portfolio_id,
                source_kind=source_kind,
                source_id=source_id,
                source_hash=source_hash,
                opportunity_id=opportunity.id,
                version=version,
                status=status,
                reason=reason[:2048],
                observed_at=observed.isoformat(),
                previous_ref=previous["id"] if previous else None,
                previous_hash=previous["payload_hash"] if previous else None,
                intent_ref="intent:" + intent.id if intent else None,
                intent_hash=intent.payload_hash if intent else None,
                is_trade_outcome=False,
            )
            body["payload_hash"] = canonical_hash(body)
            session.add(
                Record(
                    observation_id=body["id"],
                    portfolio_id=portfolio_id,
                    source_kind=source_kind,
                    source_id=source_id,
                    opportunity_id=opportunity.id,
                    version=version,
                    status=status,
                    observed_at=observed,
                    payload_json=canonical(body),
                    payload_hash=body["payload_hash"],
                )
            )
            values = dict(
                version=version,
                observation_id=body["id"],
                payload_hash=body["payload_hash"],
                next_due_at=min(
                    observed + timedelta(seconds=10), revision.valid_until, opportunity.time_end
                )
                if retry and revision is not None
                else None,
            )
            if old:
                for key, value in values.items():
                    setattr(old, key, value)
            else:
                session.add(
                    Head(
                        portfolio_id=portfolio_id,
                        source_kind=source_kind,
                        source_id=source_id,
                        **values,
                    )
                )
            cursor = session.get(Cursor, portfolio_id)
            if cursor:
                cursor.last_observation_id = body["id"]
            else:
                session.add(
                    Cursor(
                        portfolio_id=portfolio_id,
                        polled_at=observed,
                        last_observation_id=body["id"],
                    )
                )
            session.flush()
            return self.read_in_session(session, body["id"])

    def process_due(self, *, max_observations=8, max_portfolios=4, checkpoint=None):
        if not self.execution.enabled:
            return {"observed": 0, "errors": []}
        if not 1 <= max_observations <= 16 or not 1 <= max_portfolios <= 4:
            raise ValueError("execution_observation_budget_invalid")
        now = self.execution._now()
        with self.database.session() as session:
            portfolios = session.scalars(
                select(AccountHead.account_id)
                .outerjoin(Cursor, Cursor.portfolio_id == AccountHead.account_id)
                .order_by(Cursor.polled_at.nullsfirst(), AccountHead.account_id)
                .limit(max_portfolios)
            ).all()
        processed = 0
        errors = []
        for portfolio in portfolios:
            if processed >= max_observations:
                break
            with self.database.session() as session:
                repository = ExecutionRepository(session)
                identity = repository.account(portfolio).portfolio_identity
                protocol = repository.protocol(identity.protocol_ref)
                query = (
                    select(Evaluation.evaluation_id)
                    .join(Revision, Revision.revision_id == Evaluation.revision_id)
                    .join(Opportunity, Opportunity.opportunity_id == Revision.opportunity_id)
                    .outerjoin(
                        Head,
                        and_(
                            Head.portfolio_id == portfolio,
                            Head.source_kind == "evaluation",
                            Head.source_id == Evaluation.evaluation_id,
                        ),
                    )
                    .where(
                        Opportunity.scope_id == identity.scope_id,
                        Opportunity.horizon == identity.horizon,
                        Opportunity.recipe_hash == identity.method_hash,
                        Opportunity.target_hash == identity.target_spec_hash,
                        Opportunity.registered_at >= protocol.T0,
                        Opportunity.registered_at < protocol.T1,
                        Evaluation.tick_at <= now,
                        or_(Head.observation_id.is_(None), Head.next_due_at <= now),
                    )
                    .order_by(Evaluation.tick_at, Evaluation.evaluation_id)
                    .limit(max_observations - processed)
                )
                identities = [("evaluation", value) for value in session.scalars(query)]
                if not identities:
                    no_plan = (
                        select(Opportunity.opportunity_id)
                        .outerjoin(Revision, Revision.opportunity_id == Opportunity.opportunity_id)
                        .outerjoin(
                            Head,
                            and_(
                                Head.portfolio_id == portfolio,
                                Head.source_kind == "opportunity",
                                Head.source_id == Opportunity.opportunity_id,
                            ),
                        )
                        .where(
                            Opportunity.scope_id == identity.scope_id,
                            Opportunity.horizon == identity.horizon,
                            Opportunity.recipe_hash == identity.method_hash,
                            Opportunity.target_hash == identity.target_spec_hash,
                            Opportunity.registered_at >= protocol.T0,
                            Opportunity.registered_at < protocol.T1,
                            Opportunity.end_at <= now,
                            Revision.revision_id.is_(None),
                            Head.observation_id.is_(None),
                        )
                        .order_by(Opportunity.registered_at, Opportunity.opportunity_id)
                        .limit(max_observations - processed)
                    )
                    identities = [("opportunity", value) for value in session.scalars(no_plan)]
            for kind, source in identities:
                if checkpoint:
                    checkpoint()
                try:
                    self.observe(
                        portfolio, source_kind=kind, source_id=source, checkpoint=checkpoint
                    )
                except ValueError as error:
                    errors.append(
                        {"portfolio_id": portfolio, "source_id": source, "reason": str(error)}
                    )
                processed += 1
            with self.database.session() as session:
                begin_immediate(session)
                cursor = session.get(Cursor, portfolio)
                if cursor:
                    cursor.polled_at = self.execution._now()
                else:
                    session.add(
                        Cursor(
                            portfolio_id=portfolio,
                            polled_at=self.execution._now(),
                            last_observation_id=None,
                        )
                    )
        return {"observed": processed, "errors": errors}

    def opportunities(self, portfolio_id, *, after=None, limit=50):
        """One scope/horizon/method denominator; never aggregate diagnostic capital."""
        if not 1 <= limit <= 100:
            raise ValueError("execution_denominator_page_invalid")
        with self.database.session() as session:
            repository = ExecutionRepository(session)
            identity = repository.account(portfolio_id).portfolio_identity
            protocol = repository.protocol(identity.protocol_ref)
            query = select(Opportunity).where(
                Opportunity.scope_id == identity.scope_id,
                Opportunity.horizon == identity.horizon,
                Opportunity.recipe_hash == identity.method_hash,
                Opportunity.target_hash == identity.target_spec_hash,
                Opportunity.registered_at >= protocol.T0,
                Opportunity.registered_at < protocol.T1,
            )
            if after:
                if session.scalar(query.where(Opportunity.opportunity_id == after)) is None:
                    raise ValueError("execution_denominator_cursor_scope_mismatch")
                query = query.where(Opportunity.opportunity_id > after)
            rows = session.scalars(
                query.order_by(Opportunity.opportunity_id).limit(limit + 1)
            ).all()
            items = []
            for row in rows[:limit]:
                opportunity = ActionRepository(session).get_opportunity(row.opportunity_id)
                records = session.scalars(
                    select(Record)
                    .join(Head, Head.observation_id == Record.observation_id)
                    .where(
                        Record.portfolio_id == portfolio_id,
                        Record.opportunity_id == row.opportunity_id,
                    )
                    .order_by(Record.observation_id)
                    .limit(51)
                ).all()
                count = session.scalar(
                    select(func.count())
                    .select_from(Record)
                    .join(Head, Head.observation_id == Record.observation_id)
                    .where(
                        Record.portfolio_id == portfolio_id,
                        Record.opportunity_id == row.opportunity_id,
                    )
                )
                values = [
                    self.read_in_session(session, item.observation_id) for item in records[:50]
                ]
                has_plan = (
                    session.scalar(
                        select(Revision.revision_id)
                        .where(Revision.opportunity_id == row.opportunity_id)
                        .limit(1)
                    )
                    is not None
                )
                items.append(
                    {
                        "opportunity": opportunity,
                        "has_plan": has_plan,
                        "observations": values,
                        "observation_count": count,
                        "observation_next_cursor": records[49].observation_id
                        if len(records) > 50
                        else None,
                        "category": "observed"
                        if values
                        else "not_observed"
                        if has_plan
                        else "no_plan",
                        "is_trade_outcome": False,
                    }
                )
            return {
                "portfolio_id": portfolio_id,
                "items": items,
                "next_cursor": rows[limit - 1].opportunity_id if len(rows) > limit else None,
                "financial_qualified": False,
            }

    def observations(self, portfolio_id, opportunity_id, *, after=None, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError("execution_observation_page_invalid")
        with self.database.session() as session:
            self._source(session, portfolio_id, "opportunity", opportunity_id)
            query = (
                select(Record)
                .join(Head, Head.observation_id == Record.observation_id)
                .where(Record.portfolio_id == portfolio_id, Record.opportunity_id == opportunity_id)
            )
            if after:
                cursor = self.read_in_session(session, after)
                if (
                    cursor["portfolio_id"] != portfolio_id
                    or cursor["opportunity_id"] != opportunity_id
                ):
                    raise ValueError("execution_observation_cursor_scope_mismatch")
                query = query.where(Record.observation_id > after)
            rows = session.scalars(query.order_by(Record.observation_id).limit(limit + 1)).all()
            return {
                "items": [
                    self.read_in_session(session, row.observation_id) for row in rows[:limit]
                ],
                "next_cursor": rows[limit - 1].observation_id if len(rows) > limit else None,
                "is_trade_outcome": False,
            }
