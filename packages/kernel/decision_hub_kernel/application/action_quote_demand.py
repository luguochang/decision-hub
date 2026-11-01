"""Read current committed prospective Action demand; no invented positions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.ports.action_monitor import ConditionEvaluationV2
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPathScope,
    aware_utc,
    spot_scope,
)


class ActionQuoteDemandReader:
    def __init__(self, sessions: Callable[[], Session]):
        self.sessions = sessions

    def scopes(self, as_of: datetime) -> tuple[MarketPathScope, ...]:
        now = aware_utc(as_of)
        scopes: list[MarketPathScope] = []
        with self.sessions() as session:
            connection = session.connection()
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
            repository = ActionRepository(session)
            for instrument in ("BTC-USDT", "ETH-USDT"):
                scope = spot_scope(instrument)
                plan_id = session.scalar(
                    select(ActionPlanHeadRecord.plan_id)
                    .join(
                        ActionPlanRevisionRecord,
                        ActionPlanRevisionRecord.revision_id
                        == ActionPlanHeadRecord.current_revision_id,
                    )
                    .join(
                        DecisionOpportunityRecord,
                        DecisionOpportunityRecord.opportunity_id
                        == ActionPlanRevisionRecord.opportunity_id,
                    )
                    .where(
                        DecisionOpportunityRecord.scope_id == scope.scope_id,
                        DecisionOpportunityRecord.cohort == "prospective",
                        DecisionOpportunityRecord.registered_at <= now,
                        ActionPlanHeadRecord.state == "triggered",
                        ActionPlanHeadRecord.valid_until > now,
                        ActionPlanRevisionRecord.published_at <= now,
                    )
                    .order_by(ActionPlanHeadRecord.plan_id)
                    .limit(1)
                )
                if plan_id is None:
                    continue
                head = repository.get_head(plan_id)
                assert head is not None
                revision = repository.get_revision(head.current_revision_id)
                if revision is None or revision.scope_id != scope.scope_id:
                    raise ValueError("action_quote_demand_revision_invalid")
                evaluation = repository.get_latest_evaluation(
                    plan_id, revision_id=revision.revision_id
                )
                if (
                    evaluation is None
                    or not isinstance(evaluation, ConditionEvaluationV2)
                    or evaluation.state_after != "triggered"
                ):
                    raise ValueError("action_quote_demand_trigger_original_missing")
                scopes.append(scope)
        # Later open paper episodes must also contribute demand past plan expiry;
        # that future authenticated source is not emulated by an old Report here.
        return tuple(scopes)
