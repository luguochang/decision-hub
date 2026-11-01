"""Perp capture adapter using existing sampling leases/receipts and normal tick."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from ..persistence.action_execution_records import (
    ExecutionEpisodeHeadRecord,
    ExecutionIntentRecord,
    ExecutionReceiptRecord,
)
from ..persistence.action_execution_repository import canonical
from ..persistence.action_quote_sampling_records import QuoteSamplingHead, QuoteSamplingReceipt
from ..persistence.db import as_utc
from ..persistence.original_preparation import begin_immediate
from ..persistence.perpetual_execution_adapter import PerpetualExecutionAdapter
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_manual_execution import (
    MANUAL_INSTRUMENTS,
    PerpetualSamplingScope,
    manual_scope,
)
from .action_quote_sampling import claim_scope, finish_scope
from .perpetual_manual_sampling_demand import prepared_manual_sampling_scopes


class PerpetualQuoteSamplingAdapter:
    def __init__(self, service, *, owner_id="perp-paper-sampler"):
        self.service, self.owner_id = service, owner_id

    def process_due(self, *, checkpoint=None):
        svc = self.service
        now = aware_utc(svc.clock())
        scopes = []
        with svc.database.session() as session:
            adapter = PerpetualExecutionAdapter(session)
            for instrument in MANUAL_INSTRUMENTS:
                scope = PerpetualSamplingScope(manual_scope(instrument), instrument)
                pending = session.scalar(
                    select(ExecutionIntentRecord.intent_id)
                    .outerjoin(
                        ExecutionReceiptRecord,
                        ExecutionReceiptRecord.intent_id == ExecutionIntentRecord.intent_id,
                    )
                    .where(
                        ExecutionIntentRecord.scope_id == scope.scope_id,
                        ExecutionReceiptRecord.intent_id.is_(None),
                        ExecutionIntentRecord.quote_deadline >= now,
                    )
                    .order_by(ExecutionIntentRecord.eligible_at, ExecutionIntentRecord.intent_id)
                    .limit(1)
                )
                held = session.scalar(
                    select(ExecutionEpisodeHeadRecord.portfolio_id)
                    .where(
                        ExecutionEpisodeHeadRecord.scope_id == scope.scope_id,
                        ExecutionEpisodeHeadRecord.active.is_(True),
                    )
                    .order_by(ExecutionEpisodeHeadRecord.portfolio_id)
                    .limit(1)
                )
                if pending:
                    intent = adapter.intent(pending)
                    svc._authority(session, intent)
                    if intent.scope_id != scope.scope_id:
                        raise ValueError("perp_manual_sampling_partition_scope_mismatch")
                if held:
                    body, _ = adapter.episode(held)
                    entry = adapter.intent(body["entry_intent_id"])
                    svc._authority(session, entry)
                    if entry.scope_id != scope.scope_id:
                        raise ValueError("perp_manual_sampling_episode_scope_mismatch")
                if pending or held:
                    scopes.append(scope)
        known = {scope.scope_id for scope in scopes}
        for scope in prepared_manual_sampling_scopes(svc, now=now):
            if scope.scope_id not in known:
                scopes.append(scope)
                known.add(scope.scope_id)
        return sum(self._capture_scope(scope, checkpoint=checkpoint) for scope in scopes)

    def _capture_scope(self, scope, *, checkpoint=None):
        svc = self.service
        now = aware_utc(svc.clock())
        with svc.database.session() as session:
            begin_immediate(session)
            claim = claim_scope(session, scope=scope, owner_id=self.owner_id, now=now)
        if claim is None:
            return 0
        if checkpoint:
            checkpoint()
        captures, quote, failure = (), None, None
        try:
            collected = asyncio.run(svc.paper.collect_bracketed_quote(inst_id=scope.instrument))
            quote = collected.capture_id
            captures = (collected.capture_id, collected.clock_measurement_id)
        except Exception as error:
            failure = (str(error) or type(error).__name__)[:160]
        with svc.database.session() as session:
            begin_immediate(session)
            finish_scope(
                session,
                claim,
                now=aware_utc(svc.clock()),
                event="failed" if failure else "captured",
                capture_ids=captures,
                quote_id=quote,
                error=failure,
            )
        return int(quote is not None)

    @staticmethod
    def observations(session, *, generation, scope_id):
        scope = scope_id
        if manual_scope(scope.removeprefix("perp-book:okx:")) != scope:
            raise ValueError("perp_manual_sampling_scope_invalid")
        head = session.get(QuoteSamplingHead, scope, populate_existing=True)
        if head is None or head.generation <= generation:
            return []
        rows = session.scalars(
            select(QuoteSamplingReceipt)
            .where(
                QuoteSamplingReceipt.scope_id == scope,
                QuoteSamplingReceipt.generation > generation,
                QuoteSamplingReceipt.generation <= head.generation,
            )
            .order_by(QuoteSamplingReceipt.generation, QuoteSamplingReceipt.receipt_id)
            .limit(1025)
        ).all()
        if len(rows) > 1024 or head.generation - generation > 512:
            raise ValueError("perp_manual_quote_receipt_budget_exceeded")
        grouped = {}
        for row in rows:
            body = json.loads(row.payload_json)
            if (
                canonical(body) != row.payload_json
                or canonical_hash(body) != row.payload_hash
                or body["scope_id"] != scope
                or body["generation"] != row.generation
                or body["event"] != row.event
                or aware_utc(__import__("datetime").datetime.fromisoformat(body["slot_at"]))
                != as_utc(row.slot_at)
                or row.receipt_id
                != "aqsr_" + canonical_hash([scope, body["slot_at"], row.generation, row.event])
                or row.event in grouped.get(row.generation, {})
            ):
                raise ValueError("perp_manual_sampling_original_invalid")
            grouped.setdefault(row.generation, {})[row.event] = (
                body,
                {"ref": row.receipt_id, "hash": row.payload_hash},
            )
        result = []
        for number in range(generation + 1, head.generation + 1):
            events = grouped.get(number, {})
            if "claimed" not in events:
                raise ValueError("perp_manual_sampling_sequence_gap")
            terminal = [events[k] for k in ("captured", "failed", "rejected") if k in events]
            if not terminal:
                break  # Never skip an incomplete earlier claimed observation.
            if len(terminal) != 1:
                raise ValueError("perp_manual_sampling_terminal_conflict")
            claimed = events["claimed"][0]
            body, ref = terminal[0]
            for key in ("scope_id", "slot_at", "owner_id", "generation", "lease_expires_at"):
                if body[key] != claimed[key]:
                    raise ValueError("perp_manual_sampling_claim_mismatch")
            result.append({"body": body, "ref": ref})
        return result
