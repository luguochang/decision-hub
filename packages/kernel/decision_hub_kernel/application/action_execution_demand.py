"""Registered opening, pending execution and persistent protection quote demand."""

from datetime import timedelta

from sqlalchemy import func, select

from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionEpisodeHeadRecord,
    ExecutionIntentRecord,
    ExecutionProtocolRecord,
    ExecutionReceiptRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
    ExecutionRepository,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, spot_scope


class ExecutionQuoteDemandReader:
    def __init__(self, sessions, *, action_demand=None):
        self.sessions = sessions
        self.action_demand = action_demand

    def scopes(self, as_of):
        now = aware_utc(as_of)
        result = (
            {value.scope_id: value for value in self.action_demand(now)}
            if self.action_demand
            else {}
        )
        with self.sessions() as session:
            if session.connection().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN")
            repository = ExecutionRepository(session)
            # Protocols have no free-form enabled field; registration itself is
            # the prospective internal command. No protocol means no T0 demand.
            protocols = session.scalars(
                select(ExecutionProtocolRecord.protocol_id)
                .where(
                    func.json_extract(ExecutionProtocolRecord.payload_json, "$.schema_version")
                    == "action-execution-protocol.v1",
                    ExecutionProtocolRecord.start_at <= now,
                    ExecutionProtocolRecord.start_at >= now - timedelta(seconds=60),
                )
                .order_by(ExecutionProtocolRecord.protocol_id)
                .limit(33)
            ).all()
            if len(protocols) > 32:
                raise ValueError("execution_opening_demand_budget_exceeded")
            for protocol_id in protocols:
                protocol = repository.protocol(protocol_id)
                for identity in protocol.scopes:
                    scope = spot_scope(identity.split(":")[3])
                    if scope.scope_id != identity:
                        raise ValueError("execution_demand_scope_mismatch")
                    result[identity] = scope
            for instrument in ("BTC-USDT", "ETH-USDT"):
                scope = spot_scope(instrument)
                intent_id = session.scalar(
                    select(ExecutionIntentRecord.intent_id)
                    .outerjoin(
                        ExecutionReceiptRecord,
                        ExecutionReceiptRecord.intent_id == ExecutionIntentRecord.intent_id,
                    )
                    .where(
                        ExecutionIntentRecord.scope_id == scope.scope_id,
                        ExecutionReceiptRecord.intent_id.is_(None),
                        ExecutionIntentRecord.eligible_at <= now + timedelta(seconds=5),
                        ExecutionIntentRecord.quote_deadline >= now,
                    )
                    .order_by(ExecutionIntentRecord.eligible_at)
                    .limit(1)
                )
                if intent_id:
                    intent = repository.intent(intent_id)
                    if intent.quote_deadline >= now:
                        result[scope.scope_id] = scope
                portfolio = session.scalar(
                    select(ExecutionEpisodeHeadRecord.portfolio_id)
                    .where(
                        ExecutionEpisodeHeadRecord.scope_id == scope.scope_id,
                        ExecutionEpisodeHeadRecord.active.is_(True),
                        ExecutionEpisodeHeadRecord.remaining_nonzero.is_(True),
                    )
                    .order_by(ExecutionEpisodeHeadRecord.portfolio_id)
                    .limit(1)
                )
                if portfolio:
                    episode = repository.open_episode(portfolio)
                    if episode is None:
                        raise ValueError("execution_demand_episode_original_missing")
                    result[scope.scope_id] = scope
        if len(result) > 2:
            raise ValueError("execution_demand_scope_budget_exceeded")
        return tuple(result[key] for key in sorted(result))
