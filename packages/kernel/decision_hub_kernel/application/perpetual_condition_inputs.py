"""Read real captured SWAP closes for the existing continuing watch.

Archive parsing happens during read preparation, before the watch writer. This
does not admit a research Run, alter prices or authorize an executable fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    capture_scope,
)

from ..persistence.action_quote_repository import _capture
from ..persistence.db import RawCaptureRecord
from ..ports.market_path import aware_utc
from ..ports.perpetual_close import (
    VERSION,
    PerpetualClose,
    millis,
    minute_end,
    parse_page,
    public_url,
)
from .market_original_parse import MarketOriginalParse


@dataclass(frozen=True)
class PerpetualConditionInputs:
    scope_id: str
    as_of: datetime
    closes: tuple[PerpetualClose, ...]


def read_perpetual_condition_inputs_in_session(
    session,
    *,
    instrument_ref,
    scope_id,
    as_of,
    source,
) -> PerpetualConditionInputs:
    cutoff = aware_utc(as_of)
    contract = source.read_contract_in_session(session, instrument_ref)
    if contract.inst_id not in {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}:
        raise ValueError("perpetual_condition_instrument_unsupported")
    if scope_id != f"crypto:perp:okx:{contract.inst_id}:trade:1m":
        raise ValueError("perpetual_condition_scope_mismatch")
    if aware_utc(contract.known_at) > cutoff:
        raise ValueError("perpetual_condition_instrument_not_known")
    rows = session.scalars(
        select(RawCaptureRecord)
        .where(
            RawCaptureRecord.adapter_version == VERSION,
            RawCaptureRecord.context_id.startswith(f"{VERSION}:{contract.inst_id}:"),
            RawCaptureRecord.recorded_at <= cutoff,
            RawCaptureRecord.status_code == 200,
            RawCaptureRecord.error_code.is_(None),
        )
        .order_by(RawCaptureRecord.recorded_at.desc(), RawCaptureRecord.capture_id)
        .limit(8)
    ).all()
    if not rows:
        raise ValueError("perpetual_condition_closed_capture_missing")
    parser, indexed = MarketOriginalParse(), {}
    for row in rows:
        if row.body_ref is None:
            raise ValueError("perpetual_condition_closed_capture_missing")
        page = parse_page(
            _capture(row),
            source.archive.read(row.body_ref),
            inst_id=contract.inst_id,
            as_of=cutoff,
            selected_rows=parser.selected_rows,
        )
        for close in page.closes:
            previous = indexed.get(close.close_at)
            if previous is not None and previous.price != close.price:
                raise ValueError("perpetual_condition_conflicting_close")
            if previous is None or close.recorded_at > previous.recorded_at:
                indexed[close.close_at] = close
    return PerpetualConditionInputs(
        scope_id, cutoff, tuple(indexed[k] for k in sorted(indexed))[-1440:]
    )


async def collect_perpetual_condition_page(source, *, inst_id, as_of):
    """One existing history-candle capture, bounded to one public request/5s."""
    url = public_url(inst_id, millis(minute_end(as_of)))
    batch = CaptureBatch(
        f"{VERSION}:{inst_id}:{uuid4().hex}:base:0",
        VERSION,
        source.archive,
        source.lineage.record_capture,
    )
    async with CapturingAsyncClient(timeout=5, follow_redirects=False) as client:
        with capture_scope(batch):
            response = await client.get(url)
    response.raise_for_status()
    if len(batch.captures) != 1:
        raise ValueError("perpetual_condition_one_capture_required")
    return batch.captures[0]["capture_id"]
