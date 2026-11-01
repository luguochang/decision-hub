"""Bind a real same-writer component audit to its consumer's immutable output."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..persistence.db import as_utc
from ..persistence.market_case_records import MarketComponentAuditRecord
from ..ports.component_registry import ComponentKind
from ..ports.market_path import aware_utc, canonical_hash


def component_consumption_reference_in_session(
    session: Session,
    receipt: dict[str, Any],
    *,
    kind: ComponentKind,
    component_ref: str,
    scope_id: str,
    request_id: str,
    known_at: datetime,
    payload: dict[str, Any],
    value: object,
) -> dict[str, str]:
    # The injected consumer writes its receipt in this transaction. Re-read
    # that exact original; a returned dictionary alone is not an audit receipt.
    audit_id = receipt.get("audit_id")
    if not isinstance(audit_id, str):
        raise ValueError("component_consumption_audit_reference_missing")
    session.flush()
    row = session.get(MarketComponentAuditRecord, audit_id, populate_existing=True)
    expected = canonical_hash(receipt)
    selection = receipt.get("selection")
    if (
        row is None
        or not isinstance(selection, dict)
        or row.payload_hash != expected
        or json.loads(row.payload_json) != receipt
        or receipt.get("schema_version") != "market-component-consumption.v1"
        or receipt.get("request_id") != "component-consume:" + request_id
        or row.request_id != receipt.get("request_id")
        or as_utc(row.created_at) != aware_utc(known_at)
        or receipt.get("input_hash") != canonical_hash(payload)
        or receipt.get("output_hash") != canonical_hash(value)
        or receipt.get("output") != value
        or (selection.get("kind"), selection.get("component_ref"), selection.get("scope_id"))
        != (kind, component_ref, scope_id)
        or receipt.get("state") not in {"selected", "baseline_not_registry_approved"}
        or receipt.get("probability_qualified") is not False
        or receipt.get("trading_authority") is not False
    ):
        raise ValueError("component_consumption_original_binding_invalid")
    return {"audit_id": audit_id, "payload_hash": expected}
