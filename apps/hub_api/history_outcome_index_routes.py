"""Bounded signed-owner index of explicitly granted evaluations; metadata only."""

from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from packages.contracts_py.decision_hub_contracts.generated.history_outcome_index_v2 import (
    HistoryOutcomeIndexV2,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc

from .history_outcome_routes import OutcomeBootstrapV2
from .history_owner_authority import HISTORY_COOKIE
from .local_owner_session import LocalOwnerAuthenticationError


def _clock(value):
    if type(value) is not str:
        raise ValueError("history_outcome_index_native_clock_required")
    return as_utc(datetime.fromisoformat(value)).isoformat()


def read_outcome_index(bootstrap):
    """At most 16 indexed PK metadata rows. No original read, job or producer."""
    bootstrap.__post_init__()
    grants = bootstrap.protocol_ids
    if len(grants) > 16:
        raise ValueError("history_outcome_index_grants_over_limit")
    values = []
    with bootstrap.service.commands._read_session() as session:
        native = session.connection().connection.driver_connection
        changes = native.total_changes
        if grants:
            rows = session.execute(
                text(
                    "SELECT protocol_id,owner_claim,fixture_mode,registered_at,"
                    "first_hour,last_hour,"
                    "opportunity_count,confirmed_at,confirmation_id,confirmation_original_ref,"
                    "confirmation_original_hash,confirmation_payload_hash "
                    "FROM market_history_v2_outcome_protocols WHERE owner_claim=:owner "
                    "AND protocol_id IN :ids LIMIT 16"
                ).bindparams(bindparam("ids", expanding=True)),
                dict(owner=bootstrap.service.owner_claim, ids=grants),
            ).all()
            if len(rows) != len(grants):
                raise ValueError("history_outcome_index_granted_metadata_missing")
            for row in rows:
                identifier, owner, fixture, registered, first, last, count, confirmed, *receipt = (
                    row
                )
                if (
                    type(identifier) is not str
                    or identifier not in grants
                    or owner != bootstrap.service.owner_claim
                    or type(fixture) is not int
                    or fixture != 0
                    or type(count) is not int
                    or count != 48
                    or ((confirmed is None) != all(value is None for value in receipt))
                    or (
                        confirmed is not None
                        and any(type(value) is not str or not value for value in receipt)
                    )
                    or (
                        confirmed is not None
                        and (
                            re.fullmatch(r"hacceptance2_[a-f0-9]{48}", receipt[0]) is None
                            or any(
                                re.fullmatch(r"[a-f0-9]{64}", value) is None
                                for value in receipt[2:4]
                            )
                        )
                    )
                ):
                    raise ValueError("history_outcome_index_native_binding_invalid")
                values.append(
                    dict(
                        protocol_id=identifier,
                        prepared_registered_at=_clock(registered),
                        independently_confirmed_registered_at=None
                        if confirmed is None
                        else _clock(confirmed),
                        first_hour=_clock(first),
                        last_hour=_clock(last),
                        expected_total=count,
                        state="prepared" if confirmed is None else "confirmed",
                        fixture_mode=False,
                    )
                )
        actual = bootstrap.service.now().isoformat()
        if (
            native.total_changes != changes
            or not native.in_transaction
            or native.execute("PRAGMA query_only").fetchone() != (1,)
        ):
            raise ValueError("history_outcome_index_physical_ro_required")
        values.sort(
            key=lambda value: (
                -datetime.fromisoformat(value["first_hour"]).timestamp(),
                value["protocol_id"],
            )
        )
        reply = HistoryOutcomeIndexV2.model_validate_json(
            json.dumps(
                dict(
                    schema_version="history-outcome-index.v2",
                    owner_authenticated=True,
                    actual_as_of=actual,
                    evaluations=values,
                    current_originals_verified=False,
                    financial_qualified=False,
                    promotion_allowed=False,
                )
            )
        )
        if len(reply.model_dump_json().encode()) > 32768:
            raise ValueError("history_outcome_index_reply_oversize")
    return reply


def history_outcome_index_router(bootstrap: OutcomeBootstrapV2 | None = None):
    if bootstrap is not None and type(bootstrap) is not OutcomeBootstrapV2:
        raise ValueError("history_outcome_index_actual_bootstrap_required")
    router = APIRouter()

    @router.get("/v1/history/evaluations", response_model=HistoryOutcomeIndexV2)
    def index(request: Request):
        if bootstrap is None:
            raise HTTPException(503, "history_outcome_not_configured")
        try:
            bootstrap.__post_init__()
            principal = bootstrap.history.authority.resolve(
                request.cookies.get(HISTORY_COOKIE, ""),
                origin=request.headers.get("Origin"),
                mutation=False,
            )
        except LocalOwnerAuthenticationError:
            raise HTTPException(403, "history_owner_session_rejected") from None
        except (ValueError, TypeError):
            raise HTTPException(409, "history_outcome_trusted_binding_changed") from None
        if principal.owner_identity != bootstrap.service.owner_claim:
            raise HTTPException(403, "history_outcome_protocol_not_granted")
        try:
            return read_outcome_index(bootstrap)
        except (ValueError, TypeError, KeyError, SQLAlchemyError):
            raise HTTPException(409, "history_outcome_index_metadata_unavailable") from None

    return router
