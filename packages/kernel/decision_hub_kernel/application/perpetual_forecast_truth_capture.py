"""Original HTTP capture/archive transport for the explicit horizon truth codec."""

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select

from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    capture_scope,
)

from ..persistence.action_quote_repository import _capture  # pyright: ignore[reportPrivateUsage]
from ..persistence.db import RawCaptureRecord
from ..ports.market_path import canonical_hash
from ..ports.perpetual_close import PerpetualClosePage, public_url
from .market_original_parse import MarketOriginalParse
from .perpetual_forecast_truth import (
    VERSION,
    TruthCollectionError,
    collect_truth_pages,
    parse_truth_page,
)


async def capture_truth(runtime: Any, claim: dict[str, Any]) -> tuple[str, ...]:
    """Resume only the same claim's original pages; retain failed capture identities."""
    instrument = claim["instrument_id"].removeprefix("okx:")
    acquisition = canonical_hash([claim["run_id"], claim["target_ref"], claim["attempt"]])[:32]
    parser = MarketOriginalParse()
    async with CapturingAsyncClient(
        timeout=15, follow_redirects=False, transport=runtime.http_transport
    ) as client:

        async def page(cursor: int, sequence: int) -> PerpetualClosePage:
            context = f"{VERSION}:{instrument}:{acquisition}:base:{sequence}"
            with runtime.database.session() as session:
                prior = session.scalars(
                    select(RawCaptureRecord.capture_id)
                    .where(RawCaptureRecord.context_id == context)
                    .order_by(RawCaptureRecord.recorded_at, RawCaptureRecord.capture_id)
                ).all()
            if len(prior) > 1:
                raise TruthCollectionError(
                    "perpetual_scoring_capture_context_ambiguous", tuple(prior)
                )
            if not prior:
                batch = CaptureBatch(
                    context, VERSION, runtime.source.archive, runtime.source.lineage.record_capture
                )
                with capture_scope(batch):
                    try:
                        await client.get(public_url(instrument, cursor))
                    except httpx.HTTPError:
                        if not batch.captures:
                            raise
                prior = tuple(item["capture_id"] for item in batch.captures)
            if len(prior) != 1:
                raise TruthCollectionError(
                    "perpetual_scoring_one_page_capture_required", tuple(prior)
                )
            try:
                with runtime.database.session() as session:
                    row = session.get(RawCaptureRecord, prior[0], populate_existing=True)
                    if row is None or row.body_ref is None:
                        raise ValueError("perpetual_scoring_capture_missing")
                    capture = _capture(row)
                return parse_truth_page(
                    capture,
                    runtime.source.archive.read(capture.body_ref),
                    inst_id=instrument,
                    as_of=runtime.clock(),
                    selected_rows=parser.selected_rows,
                )
            except (ValueError, OSError) as error:
                raise TruthCollectionError(str(error), tuple(prior)) from error

        return await collect_truth_pages(
            instrument_id=instrument,
            start=datetime.fromisoformat(claim["target_start"].replace("Z", "+00:00")),
            end=datetime.fromisoformat(claim["target_end"].replace("Z", "+00:00")),
            horizon=claim["horizon"],
            capture_page=page,
            clock=runtime.clock,
        )
