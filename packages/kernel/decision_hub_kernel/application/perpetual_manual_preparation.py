"""One read snapshot for manual originals; writer authorization remains separate."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..persistence.callback_source_lease import CallbackSourceLease
from ..persistence.original_preparation import PreparedOriginals
from ..ports.market_path import aware_utc
from .perpetual_paper import PreparedPaperQuote, PreparedQuoteBundle


def _select(
    intent: Any,
    observations: list[dict[str, Any]],
    account: dict[str, Any],
    instrument_id: str,
    now: datetime,
    reader: Callable[..., PreparedPaperQuote],
    checkpoint: Callable[[], None] | None,
) -> tuple[dict[str, Any] | None, PreparedPaperQuote | None, list[dict[str, Any]]]:
    proof: list[dict[str, Any]] = []
    for entry in observations:
        if checkpoint:
            checkpoint()
        body = entry["body"]
        if body["event"] != "captured":
            proof.append(dict(sampling_receipt=entry["ref"], decision=body["event"]))
            continue
        if entry["cutoff"] > now or aware_utc(datetime.fromisoformat(body["recorded_at"])) > now:
            raise ValueError("perp_manual_quote_not_yet_known")
        from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
            DecisionSourceRefV1,
        )

        quote = reader(
            instrument_ref=DecisionSourceRefV1.model_validate(
                account["ledger"]["instrument_refs"][instrument_id]
            ),
            capture_id=body["capture_ids"][0],
            clock_measurement_id=body["capture_ids"][1],
            cutoff=entry["cutoff"],
        )
        book = quote.book
        if book.observed_local_lower <= intent.eligible_at:
            decision = "not_strictly_after_eligible"
        elif book.received_at - book.observed_local_lower > timedelta(seconds=5):
            decision = "stale_on_receipt"
        elif (
            intent.phase != "entry"
            and account["episode"]
            and book.capacity_key == account["episode"][0]["last_quote_capacity_key"]
        ):
            decision = "same_quote_capacity_as_prior_fill"
        else:
            proof.append(dict(sampling_receipt=entry["ref"], decision="first_eligible"))
            return entry, quote, proof
        proof.append(dict(sampling_receipt=entry["ref"], decision=decision))
    return None, None, proof


def prepare_manual_execution(
    service: Any,
    intent: Any,
    command: Any,
    now: datetime,
    *,
    checkpoint: Callable[[], None] | None = None,
    source_lease: CallbackSourceLease | None = None,
) -> PreparedOriginals[PreparedQuoteBundle[dict[str, Any]]]:
    entry_at = aware_utc(service.clock()) if intent.phase == "entry" else None

    # Legacy service payloads retain their original exact models and dict shapes.
    def originals(
        session: Session, reader: Callable[..., PreparedPaperQuote]
    ) -> tuple[dict[str, Any], PreparedPaperQuote | None]:
        entry_auth = (
            service._prepare(
                intent.owner_id, command, accepted_intent=intent, session=session, at=entry_at
            )
            if intent.phase == "entry"
            else None
        )
        account = service._account_in_session(session, intent)
        observations = service._observations(intent, session=session)
        selected, quote, proof = _select(
            intent, observations, account, command.partition.instrument_id, now, reader, checkpoint
        )
        return dict(
            entry_auth=entry_auth,
            account_auth=account,
            observations=observations,
            selected_entry=selected,
            proof=proof,
        ), quote

    if source_lease is None:
        return service.paper.prepare_quote_bundle(originals)
    return service.paper.prepare_quote_bundle(originals, source_lease=source_lease)
