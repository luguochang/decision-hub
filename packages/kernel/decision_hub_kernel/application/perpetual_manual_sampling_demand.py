"""Read active prepared manual-paper scopes for the normal quote tick."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from ..persistence.action_execution_records import ExecutionProtocolRecord
from ..ports.market_path import aware_utc
from ..ports.perpetual_execution_protocol import PerpetualManualPaperProtocol
from ..ports.perpetual_manual_execution import (
    MANUAL_INSTRUMENTS,
    PerpetualSamplingScope,
    manual_scope,
)
from .perpetual_manual_input_guide import PerpetualManualInputGuideQuery

_TRIAL_PREFIX = "manual:session:"


def prepared_manual_sampling_scopes(service, *, now: datetime):
    """Return deduplicated scopes whose exact prepared originals remain current."""

    at = aware_utc(now)
    candidates = []
    with service.database.session() as session:
        rows = session.execute(
            select(
                ExecutionProtocolRecord.protocol_id,
                ExecutionProtocolRecord.payload_hash,
            )
            .where(
                ExecutionProtocolRecord.end_at > at,
                ExecutionProtocolRecord.trial_id.like(_TRIAL_PREFIX + "%"),
            )
            .order_by(
                ExecutionProtocolRecord.registered_at.desc(),
                ExecutionProtocolRecord.protocol_id,
            )
        ).all()
        for protocol_id, payload_hash in rows:
            try:
                reference = DecisionSourceRefV1(ref=protocol_id, hash=payload_hash)
                protocol = service.protocols.read_in_session(session, reference)
            except ValueError:
                continue
            if (
                not isinstance(protocol, PerpetualManualPaperProtocol)
                or not protocol.trial_id.startswith(_TRIAL_PREFIX)
                or protocol.reference != reference
                or protocol.T1 <= at
            ):
                continue
            candidates.append(
                (
                    protocol.owner_id,
                    protocol.trial_id.removeprefix(_TRIAL_PREFIX),
                    reference,
                )
            )

    reader = PerpetualManualInputGuideQuery(service, clock=lambda: at)
    scopes = {}
    for owner_id, request_id, protocol_ref in candidates:
        try:
            guide = reader.read(owner_id, request_id)
            context = guide["context"]
            instrument_id = context["partition"]["instrument_id"]
            if (
                context["protocol_ref"] != protocol_ref.model_dump(mode="json")
                or aware_utc(datetime.fromisoformat(guide["latest_entry_at"])) <= at
                or not instrument_id.startswith("okx:")
            ):
                continue
            instrument = instrument_id.removeprefix("okx:")
            scope = PerpetualSamplingScope(manual_scope(instrument), instrument)
            scope.validate()
        except (KeyError, TypeError, ValueError, PermissionError):
            continue
        scopes.setdefault(scope.scope_id, scope)
        if len(scopes) == len(MANUAL_INSTRUMENTS):
            break
    return tuple(scopes[key] for key in sorted(scopes))
