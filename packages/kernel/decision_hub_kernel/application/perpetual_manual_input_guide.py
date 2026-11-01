"""Read-only manual paper input guide over authenticated prepared originals."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from ..persistence.action_quote_sampling_records import QuoteSamplingReceipt
from ..persistence.action_repository import ActionRepository
from ..persistence.db import RawCaptureRecord, as_utc, utcnow
from ..persistence.perpetual_paper_repository import PerpetualPaperRepository
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_execution_protocol import PerpetualManualPaperProtocol
from ..ports.perpetual_manual_execution import (
    EventManualBinding,
    ManualInputGuide,
    manual_horizon,
    manual_scope,
    parse_manual_identity,
    parse_manual_preparation,
)
from .decision_settings import DecisionSettingsService
from .event_scenario_manual import read_event_manual_in_session


class PerpetualManualInputGuideQuery:
    """Authenticate one prepare receipt and replay the newest captured scope original."""

    def __init__(self, manual_service, *, clock=utcnow):
        self.service, self.database, self.clock = manual_service, manual_service.database, clock

    def read(self, owner_id: str, prepare_request_id: str) -> dict:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            repo = ActionRepository(session)
            receipt = repo.get_command(owner_id, prepare_request_id)
            if receipt is None:
                raise PermissionError("perp_manual_input_guide_owner_or_prepare_missing")
            if (
                receipt.owner_id != owner_id
                or receipt.request_id != prepare_request_id
                or receipt.operation != "perp_prepare"
                or receipt.receipt_hash != canonical_hash(receipt.body())
            ):
                raise ValueError("perp_manual_input_guide_prepare_invalid")
            response = receipt.response
            if response.get("status") != "prepared":
                raise ValueError("perp_manual_input_guide_prepare_invalid")
            command = parse_manual_preparation(response.get("command"))
            context = parse_manual_identity(response.get("context"))
            event = isinstance(command, EventManualBinding)
            if (
                command.request_id != receipt.request_id
                or receipt.payload_hash
                != canonical_hash(
                    {"owner_id": owner_id, "command": command.model_dump(mode="json")}
                )
                or context.partition != command.partition
                or context.settings_revision != command.settings_revision
                or context.settings_state_version != command.settings_state_version
                or context.revision != command.revision
                or manual_horizon(context) != command.horizon
            ):
                raise ValueError("perp_manual_input_guide_prepare_invalid")
            if event and any(
                getattr(command, key) != getattr(context, key)
                for key in EventManualBinding.model_fields
            ):
                raise ValueError("perp_manual_input_guide_prepare_invalid")

            current = DecisionSettingsService.current_in_session(
                session, owner_id, command.partition
            )
            if (
                current is None
                or current.state_version != context.settings_state_version
                or (current.revision.revision_id, current.revision.payload_hash)
                != (context.settings_revision.ref, context.settings_revision.hash)
            ):
                raise ValueError("perp_manual_input_guide_settings_changed")
            ledger = PerpetualPaperRepository(session).current(
                owner_id, command.partition.account_id
            )
            if (
                ledger["revision"] != context.account_revision.model_dump(mode="json")
                or ledger["state_version"] != context.account_state_version
            ):
                raise ValueError("perp_manual_input_guide_account_changed")

            revision = repo.get_revision(context.revision.ref)
            head = repo.get_head(receipt.plan_id)
            if (
                revision is None
                or revision.payload_hash != context.revision.hash
                or revision.plan_id != receipt.plan_id
                or revision.context.owner_id != owner_id
                or revision.context.account_kind != command.partition.account_kind
                or revision.context.account_id != command.partition.account_id
                or revision.context.instrument_id != command.partition.instrument_id
                or revision.horizon != command.horizon
                or head is None
                or head.current_revision_id != context.revision.ref
                or head.state_version != context.expected_plan_state_version
            ):
                raise ValueError("perp_manual_input_guide_revision_changed")
            if event:
                facts = read_event_manual_in_session(
                    session,
                    owner_id=owner_id,
                    command=command,
                    at=now,
                )
                target_side = facts["target_side"]
                latest_entry_at = aware_utc(datetime.fromisoformat(facts["latest_entry_at"]))
            else:
                candidate = revision.selected_candidate
                if candidate.action != "open" or candidate.target_side not in {"long", "short"}:
                    raise ValueError("perp_manual_input_guide_entry_required")
                target_side, latest_entry_at = (
                    candidate.target_side,
                    aware_utc(revision.valid_until),
                )

            protocol = self.service.protocols.read_in_session(session, context.protocol_ref)
            declared_protocol = PerpetualManualPaperProtocol.model_validate(
                response.get("protocol")
            )
            if (
                not isinstance(protocol, PerpetualManualPaperProtocol)
                or protocol != declared_protocol
                or protocol.reference != context.protocol_ref
                or protocol.owner_id != owner_id
                or protocol.account_id != command.partition.account_id
            ):
                raise ValueError("perp_manual_input_guide_protocol_invalid")
            contract = self.service.paper.source.read_contract_in_session(
                session, current.revision.values.instrument
            )
            if (
                contract.state != "live"
                or "okx:" + contract.inst_id != command.partition.instrument_id
            ):
                raise ValueError("perp_manual_input_guide_contract_invalid")

            scope = manual_scope(command.partition.instrument_id.removeprefix("okx:"))
            sampled = session.scalar(
                select(QuoteSamplingReceipt)
                .where(
                    QuoteSamplingReceipt.scope_id == scope,
                    QuoteSamplingReceipt.event == "captured",
                )
                .order_by(
                    QuoteSamplingReceipt.generation.desc(),
                    QuoteSamplingReceipt.receipt_id.desc(),
                )
                .limit(1)
            )
            quote_source = None
            if sampled is not None:
                body = json.loads(sampled.payload_json)
                expected_id = "aqsr_" + canonical_hash(
                    [scope, body.get("slot_at"), sampled.generation, "captured"]
                )
                captures = body.get("capture_ids")
                if (
                    canonical_hash(body) != sampled.payload_hash
                    or sampled.receipt_id != expected_id
                    or body.get("scope_id") != scope
                    or body.get("generation") != sampled.generation
                    or body.get("event") != "captured"
                    or not isinstance(captures, list)
                    or len(captures) != 2
                    or body.get("quote_id") != captures[0]
                ):
                    raise ValueError("perp_manual_input_guide_sampling_invalid")
                originals = [
                    session.get(RawCaptureRecord, capture_id, populate_existing=True)
                    for capture_id in captures
                ]
                if any(row is None for row in originals):
                    raise ValueError("perp_manual_input_guide_capture_missing")
                cutoff = max(as_utc(row.recorded_at) for row in originals if row is not None)
                if cutoff > now:
                    raise ValueError("perp_manual_input_guide_capture_not_yet_known")
                quote_source = (
                    sampled.receipt_id,
                    sampled.payload_hash,
                    tuple(captures),
                    cutoff,
                    current.revision.values.instrument,
                )
            base = dict(
                schema_version="perpetual-manual-input-guide.v1",
                owner_id=owner_id,
                prepare_request_id=prepare_request_id,
                context=context.model_dump(mode="json"),
                target_side=target_side,
                tick_sz=contract.tick_sz,
                latest_entry_at=min(latest_entry_at, protocol.T1),
                latest_exit_at=protocol.T1,
                server_time=now,
            )
        if quote_source is None:
            return ManualInputGuide.model_validate(
                {**base, "quote": {"status": "pending"}}
            ).model_dump(mode="json")

        receipt_id, receipt_hash, captures, cutoff, instrument_ref = quote_source
        prepared = self.service.paper.prepare_quote(
            instrument_ref=instrument_ref,
            capture_id=captures[0],
            clock_measurement_id=captures[1],
            cutoff=cutoff,
        )
        book = prepared.value.book
        if book.instrument_id != command.partition.instrument_id:
            raise ValueError("perp_manual_input_guide_quote_scope_invalid")
        expires = book.observed_local_lower + timedelta(seconds=5)
        quote = dict(
            status="fresh" if now <= expires else "stale",
            sampling_receipt={"ref": receipt_id, "hash": receipt_hash},
            capture_ref={"ref": book.capture_id, "hash": book.capture_hash},
            bid=book.bid,
            ask=book.ask,
            provider_observed_at=book.observed_at,
            observed_local_lower=book.observed_local_lower,
            observed_local_upper=book.observed_local_upper,
            received_at=book.received_at,
            expires_at=expires,
        )
        return ManualInputGuide.model_validate({**base, "quote": quote}).model_dump(mode="json")
