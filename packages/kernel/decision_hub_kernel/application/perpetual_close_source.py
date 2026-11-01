"""Bounded original collection/read for perpetual target thresholds, without orders.

All HTTP, archive reads, parsing and Decimal calculation finish before a caller's
writer. The issued PreparedOriginals carries ordinary existing readset fences;
it grants no Run admission, reviewer permission or publication authority.
"""

from __future__ import annotations

import _decimal
import asyncio
import hashlib
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
    InstrumentContractV1,
)
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    RawBodyArchive,
    capture_scope,
)

from ..persistence.action_quote_repository import _capture
from ..persistence.db import Database, RawCaptureRecord, utcnow
from ..persistence.original_preparation import PreparedOriginals, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_close import (
    MAX_PAGES,
    VERSION,
    PerpetualCloseWindow,
    assemble_window,
    millis,
    minute_end,
    parse_page,
    public_url,
)
from .market_original_parse import MarketOriginalParse
from .okx_perpetual_source import OKXPerpetualSource


@dataclass(frozen=True)
class CollectedPerpetualCloseWindow:
    capture_ids: tuple[str, ...]
    requested_end_at: datetime
    finished_at: datetime

    @property
    def as_of(self) -> datetime:
        return self.finished_at


def software_identity() -> str:
    kernel = Path(__file__).resolve().parents[1]
    paths = (
        "application/perpetual_close_source.py",
        "ports/perpetual_close.py",
        "application/market_original_parse.py",
        "application/okx_perpetual_source.py",
        "ports/okx_perpetual_original.py",
        "ports/exact_decimal_text.py",
        "ports/action_quote.py",
        "ports/market_path.py",
    )
    return canonical_hash(
        {
            "files": {
                path: hashlib.sha256((kernel / path).read_bytes()).hexdigest() for path in paths
            },
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "decimal": _decimal.__version__,
            "libmpdec": _decimal.__libmpdec_version__,
            "specification_sha256": hashlib.sha256(
                (
                    kernel.parents[2]
                    / (
                        "docs/design/decision-product-control-2026-10-17/"
                        "02_DECISION_METHOD_AND_EVALUATION.md"
                    )
                ).read_bytes()
            ).hexdigest(),
        }
    )


def read_perpetual_close_window_in_session(
    session: Session,
    *,
    instrument_ref: DecisionSourceRefV1,
    capture_ids: tuple[str, ...],
    as_of: datetime,
    archive: RawBodyArchive,
    instrument_reader: Callable[[Session, DecisionSourceRefV1], InstrumentContractV1],
    parser: MarketOriginalParse | None = None,
) -> PerpetualCloseWindow:
    """Authenticate in the caller's read snapshot, without a Database/nested session.

    The configured instrument_reader must be the existing authenticated original
    reader. This returns evidence only. Call outside a writer; use prepare_ when
    a later writer needs the existing readset/source guard receipt.
    """
    if (
        not isinstance(capture_ids, tuple)
        or not 5 <= len(capture_ids) <= MAX_PAGES
        or len(set(capture_ids)) != len(capture_ids)
    ):
        raise ValueError("perp_close_page_count_invalid")
    if not callable(instrument_reader):
        raise ValueError("perp_close_instrument_reader_required")
    reference = DecisionSourceRefV1.model_validate(instrument_ref.model_dump(mode="json"))
    cutoff = aware_utc(as_of)
    contract = instrument_reader(session, reference)
    if aware_utc(contract.known_at) > cutoff:
        raise ValueError("perp_close_instrument_not_known_at_cutoff")
    pages = []
    parser = parser if parser is not None else MarketOriginalParse()
    for capture_id in capture_ids:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perp_close_capture_missing")
        pages.append(
            parse_page(
                _capture(row),
                archive.read(row.body_ref),
                inst_id=contract.inst_id,
                as_of=cutoff,
                selected_rows=parser.selected_rows,
            )
        )
    return assemble_window(
        instrument_ref=reference,
        inst_id=contract.inst_id,
        pages=tuple(pages),
        as_of=cutoff,
        software_hash=software_identity(),
    )


class PerpetualCloseSource:
    def __init__(self, database: Database, instrument_source: OKXPerpetualSource):
        if instrument_source.database is not database:
            raise ValueError("perp_close_instrument_database_mismatch")
        self.database, self.instrument_source = database, instrument_source
        self.archive, self.lineage = instrument_source.archive, instrument_source.lineage
        self.parser = MarketOriginalParse()

    def prepare_perpetual_close_window(
        self,
        *,
        instrument_ref: DecisionSourceRefV1,
        capture_ids: tuple[str, ...],
        as_of: datetime,
    ) -> PreparedOriginals[PerpetualCloseWindow]:
        reference = DecisionSourceRefV1.model_validate(instrument_ref.model_dump(mode="json"))
        cutoff = aware_utc(as_of)

        def read(session):
            return read_perpetual_close_window_in_session(
                session,
                instrument_ref=reference,
                capture_ids=capture_ids,
                as_of=cutoff,
                archive=self.archive,
                instrument_reader=self.instrument_source.read_contract_in_session,
                parser=self.parser,
            )

        return prepare_originals(self.database, read)

    async def collect_window(
        self, *, inst_id: str, max_pages: int = MAX_PAGES
    ) -> CollectedPerpetualCloseWindow:
        # This is a coverage policy: 5*300 covers 1441 observations; the sixth
        # page permits smaller provider pages OR one newest-bar boundary refresh.
        # Logical coverage pages are independent of research/model budgets.
        # Only a failed public GET transport can reconnect within the same page.
        if type(max_pages) is not int or not 5 <= max_pages <= MAX_PAGES:
            raise ValueError("perp_close_collection_bound_invalid")
        requested_end = minute_end(utcnow())
        after = millis(requested_end)
        public_url(inst_id, after)  # Exact swap/endpoint before network I/O.
        acquisition, ids = uuid4().hex, []
        first_required_open = requested_end - timedelta(minutes=1441)
        oldest = None
        async with CapturingAsyncClient(timeout=15, follow_redirects=False) as client:
            for sequence in range(max_pages):
                kind = "base"
                if oldest is not None and oldest <= first_required_open:
                    latest_end = minute_end(utcnow())
                    if latest_end == requested_end:
                        break
                    # The only refresh is a new request for NEWLY completed bars,
                    # not a retry or permission to select an older price window.
                    kind, after = "refresh", millis(latest_end)
                for attempt in range(3):
                    # The guard identifies logical pages; raw capture IDs identify
                    # attempts. Each failed request keeps its own original batch.
                    batch = CaptureBatch(
                        f"{VERSION}:{inst_id}:{acquisition}:{kind}:{sequence}",
                        VERSION,
                        self.archive,
                        self.lineage.record_capture,
                    )
                    try:
                        with capture_scope(batch):
                            response = await client.get(public_url(inst_id, after))
                    except (httpx.NetworkError, httpx.TimeoutException):
                        if attempt == 2:
                            raise
                        await asyncio.sleep(attempt + 1)
                    else:
                        break
                response.raise_for_status()
                if len(batch.captures) != 1:
                    raise ValueError("perp_close_one_capture_required")
                entry = batch.captures[0]
                capture = _capture(RawCaptureRecord(**entry))
                page = parse_page(
                    capture,
                    response.content,
                    inst_id=inst_id,
                    as_of=utcnow(),
                    selected_rows=self.parser.selected_rows,
                )
                ids.append(entry["capture_id"])
                oldest = page.closes[-1].open_at
                after = millis(oldest)
                if kind == "refresh":
                    break
        return CollectedPerpetualCloseWindow(tuple(ids), requested_end, utcnow())
