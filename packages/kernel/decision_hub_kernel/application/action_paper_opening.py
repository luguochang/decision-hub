"""Artifact-only shared T0 bid valuation; no transaction ownership or order creation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from packages.kernel.decision_hub_kernel.ports.action_quote_clock import (
    CLOCK_POLICY_HASH,
    CLOCK_POLICY_REF,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
    ActionExecutionProtocolV1,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    PaperOpeningRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
    ExecutionRepository,
    canonical,
    encoded,
    number,
)
from packages.kernel.decision_hub_kernel.persistence.action_quote_repository import (
    QuoteRepository,
    RawArchive,
    StoredQuote,
)
from packages.kernel.decision_hub_kernel.ports.action_quote import (
    MAX_QUOTE_AGE,
    WAIT_FOR_QUOTE,
    decode_json,
    require_scope,
    text_at,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPathScope,
    aware_utc,
    canonical_hash,
)

OPENING_RULE_REF = "action-paper-opening-rule.v1"
OPENING_RULE = {
    "schema_version": OPENING_RULE_REF,
    "valuation": "bid",
    "window_seconds": 60,
    "max_age_at_first_availability_seconds": 10,
    "selection_order": ["available_at", "received_at", "capture_id"],
    "provider_observed_at_gte": "T0",
    "reference_at": "first_committed_quote_available_at",
    "identity": ["protocol_id", "scope_id"],
    "inherited_position": "100_percent_equity_no_buy_fill_fee_or_slippage",
    "missing": "whole_scope_unknown_no_later_backfill",
    "protection": "effective_from_actual_activation_never_backdated",
}
OPENING_RULE_HASH = canonical_hash(OPENING_RULE)
OPENING_RULE_V2_REF = "action-paper-opening-rule.v2"
OPENING_RULE_V2 = OPENING_RULE | {
    "schema_version": OPENING_RULE_V2_REF,
    "provider_observed_at_gte": "raw_timestamp_preserved_use_authenticated_local_interval",
    "local_observation_interval_lower_gte": "T0",
    "capture_started_at_gte": "T0",
    "clock_policy_ref": CLOCK_POLICY_REF,
    "clock_policy_hash": CLOCK_POLICY_HASH,
}
OPENING_RULE_V2_HASH = canonical_hash(OPENING_RULE_V2)


def quote_clock_policy(protocol: ActionExecutionProtocolV1) -> str:
    pair = protocol.opening_rule_ref, protocol.opening_rule_hash
    if pair == (OPENING_RULE_REF, OPENING_RULE_HASH):
        return "legacy"
    if pair == (OPENING_RULE_V2_REF, OPENING_RULE_V2_HASH):
        return "bracket_v1"
    raise ValueError("execution_opening_rule_mismatch")


@dataclass(frozen=True)
class OpeningOriginals:
    id: str
    payload_hash: str
    payload_json: str
    reference_price: Decimal
    reference_at: datetime
    quote: StoredQuote

    @property
    def opening_rule_ref(self) -> str:
        return decode_json(self.payload_json)["opening_rule_ref"]

    @property
    def opening_rule_hash(self) -> str:
        return decode_json(self.payload_json)["opening_rule_hash"]


def _protocol(
    session: Session, protocol: ActionExecutionProtocolV1, scope: MarketPathScope
) -> ActionExecutionProtocolV1:
    require_scope(scope)
    registered = ExecutionRepository(session).protocol(protocol.id)
    if encoded(registered) != encoded(protocol):
        raise ValueError("execution_opening_protocol_mismatch")
    if registered.registered_at > registered.T0:
        raise ValueError("execution_opening_protocol_not_prospective")
    if scope.scope_id not in registered.scopes:
        raise ValueError("execution_opening_scope_not_registered")
    quote_clock_policy(registered)
    return registered


def _identity(protocol_id: str, scope_id: str) -> str:
    return "opening_" + canonical_hash([protocol_id, scope_id])


def _body(
    protocol: ActionExecutionProtocolV1,
    scope: MarketPathScope,
    quote: StoredQuote,
    *,
    prepared_at: datetime,
    exclusions: list[dict],
) -> dict:
    available = quote.available_at
    policy = quote_clock_policy(protocol)
    lower, upper = quote.parsed.local_interval(clock_policy=policy)
    if (
        quote.parsed.scope != scope
        or available is None
        or quote.availability_hash is None
        or not protocol.T0 <= lower <= upper <= available
        or (policy == "bracket_v1" and quote.parsed.capture.started_at < protocol.T0)
        or not protocol.T0 <= available <= protocol.T0 + WAIT_FOR_QUOTE
        or available > prepared_at
        or available - lower > MAX_QUOTE_AGE
    ):
        raise ValueError("execution_opening_quote_not_eligible")
    body = {
        "schema_version": "action-paper-opening.v1",
        "id": _identity(protocol.id, scope.scope_id),
        "protocol_ref": protocol.id,
        "protocol_hash": protocol.payload_hash,
        "scope": scope.model_dump(mode="json"),
        "opening_rule_ref": protocol.opening_rule_ref,
        "opening_rule_hash": protocol.opening_rule_hash,
        "T0": text_at(protocol.T0),
        "window_end": text_at(protocol.T0 + WAIT_FOR_QUOTE),
        "price_basis": "bid",
        "reference_price": number(quote.parsed.bid),
        "reference_at": text_at(available),
        "quote_ref": quote.quote_id,
        "quote_hash": quote.payload_hash,
        "availability_hash": quote.availability_hash,
        "provider_observed_at": text_at(quote.parsed.observed_at),
        "received_at": text_at(quote.parsed.capture.completed_at),
        "admitted_at": text_at(quote.admitted_at),
        "available_at": text_at(available),
        "capture_ref": quote.parsed.capture.capture_id,
        "capture_hash": canonical_hash(quote.parsed.capture.payload()),
        "raw_ref": quote.parsed.capture.body_ref,
        "raw_hash": quote.parsed.capture.body_hash,
        "prepared_at": text_at(prepared_at),
        "quote_exclusions": exclusions,
    }
    if policy == "bracket_v1":
        clock = quote.parsed.clock_receipt
        if clock is None:
            raise ValueError("execution_opening_clock_original_missing")
        body.update(
            schema_version="action-paper-opening.v2",
            clock_ref=clock["id"],
            clock_hash=clock["payload_hash"],
            observed_local_lower=text_at(lower),
            observed_local_upper=text_at(upper),
        )
    return body


def _seal(body: dict, quote: StoredQuote) -> OpeningOriginals:
    digest = canonical_hash(body)
    return OpeningOriginals(
        body["id"],
        digest,
        canonical(body | {"payload_hash": digest}),
        Decimal(body["reference_price"]),
        aware_utc(datetime.fromisoformat(body["reference_at"])),
        quote,
    )


def prepare_opening_in_session(
    session: Session,
    *,
    protocol: ActionExecutionProtocolV1,
    scope: MarketPathScope,
    archive: RawArchive,
    observed_at: datetime,
) -> OpeningOriginals:
    """Read-only prepare: quote selection and exact bytes join the caller's readset."""
    protocol = _protocol(session, protocol, scope)
    now = aware_utc(observed_at)
    if now < protocol.T0:
        raise ValueError("execution_opening_before_T0")
    existing = session.scalar(
        select(PaperOpeningRecord)
        .where(
            PaperOpeningRecord.protocol_id == protocol.id,
            PaperOpeningRecord.scope_id == scope.scope_id,
        )
        .execution_options(populate_existing=True)
    )
    repository = QuoteRepository(archive)
    payload = None
    if existing is not None:
        payload = decode_json(existing.payload_json)
        prepared_at = aware_utc(datetime.fromisoformat(payload["prepared_at"]))
        if prepared_at > now:
            raise ValueError("execution_opening_from_future")
    else:
        prepared_at = now
    selection = repository.first_eligible(
        session,
        scope=scope,
        eligible_at=protocol.T0,
        as_of=prepared_at,
        clock_policy=quote_clock_policy(protocol),
    )
    quote = selection.selected
    if quote is None:
        code = "pending" if now < protocol.T0 + WAIT_FOR_QUOTE else "missing"
        raise ValueError(f"execution_opening_T0_quote_{code}")
    exclusions = []
    for item in selection.exclusions:
        value = asdict(item)
        value["observed_at"] = text_at(item.observed_at)
        value["available_at"] = text_at(item.available_at)
        exclusions.append(value)
    prepared = _seal(
        _body(protocol, scope, quote, prepared_at=prepared_at, exclusions=exclusions), quote
    )
    if existing is not None and (
        existing.opening_id != prepared.id
        or existing.payload_hash != prepared.payload_hash
        or existing.payload_json != prepared.payload_json
        or payload != decode_json(prepared.payload_json)
    ):
        raise ValueError("execution_opening_original_mismatch")
    return prepared


def store_opening_in_session(session: Session, prepared: OpeningOriginals) -> OpeningOriginals:
    """Store within the caller's verified short writer; no commit or source trust flag."""
    repository = ExecutionRepository(session)
    repository._writer()
    payload = decode_json(prepared.payload_json)
    protocol = repository.protocol(payload["protocol_ref"])
    scope = MarketPathScope.model_validate(payload["scope"])
    _protocol(session, protocol, scope)
    expected = _seal(
        _body(
            protocol,
            scope,
            prepared.quote,
            prepared_at=aware_utc(datetime.fromisoformat(payload["prepared_at"])),
            exclusions=payload["quote_exclusions"],
        ),
        prepared.quote,
    )
    if expected != prepared:
        raise ValueError("execution_opening_prepared_mismatch")
    existing = session.scalar(
        select(PaperOpeningRecord)
        .where(
            PaperOpeningRecord.protocol_id == protocol.id,
            PaperOpeningRecord.scope_id == scope.scope_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if (existing.opening_id, existing.payload_hash, existing.payload_json) != (
            prepared.id,
            prepared.payload_hash,
            prepared.payload_json,
        ):
            raise ValueError("execution_opening_original_conflict")
        return prepared
    session.add(
        PaperOpeningRecord(
            opening_id=prepared.id,
            protocol_id=protocol.id,
            scope_id=scope.scope_id,
            payload_json=prepared.payload_json,
            payload_hash=prepared.payload_hash,
        )
    )
    session.flush()
    return prepared
