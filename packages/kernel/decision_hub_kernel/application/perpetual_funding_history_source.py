"""One bounded public history capture; archive replay and preparation before writer."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    RawBodyArchive,
    capture_scope,
)

from ..persistence.action_quote_repository import _capture
from ..persistence.db import Database, RawCaptureRecord, utcnow
from ..persistence.original_preparation import prepare_originals
from ..ports.market_path import aware_utc
from ..ports.perpetual_funding_history import (
    COVERAGE_VERSION,
    TIME_URL,
    VERSION,
    FundingCoverageOriginal,
    FundingCoveragePage,
    FundingHistoryOriginal,
    decode_funding_coverage_page,
    decode_funding_history,
    decode_funding_server_time,
    funding_coverage_url,
    funding_history_url,
)
from .fact_lineage import FactLineageStore


class PerpetualFundingHistorySource:
    def __init__(self, database: Database, archive: RawBodyArchive):
        self.database, self.archive, self.lineage = database, archive, FactLineageStore(database)

    def read_in_session(
        self, session: Session, *, capture_id: str, inst_id: str, as_of: datetime
    ) -> FundingHistoryOriginal:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perp_funding_history_capture_missing")
        return decode_funding_history(
            _capture(row), self.archive.read(row.body_ref), inst_id=inst_id, as_of=as_of
        )

    def prepare_history(self, *, capture_id: str, inst_id: str, as_of: datetime):
        return prepare_originals(
            self.database,
            lambda session: self.read_in_session(
                session, capture_id=capture_id, inst_id=inst_id, as_of=as_of
            ),
        )

    async def collect_once(self, *, inst_id: str) -> str:
        """Exactly one request; failures remain archived. No paging/retry/coverage claim."""
        url = funding_history_url(inst_id)
        batch = CaptureBatch(
            f"{VERSION}:{inst_id}:{uuid4().hex}", VERSION, self.archive, self.lineage.record_capture
        )
        async with CapturingAsyncClient(timeout=15, follow_redirects=False) as client:
            with capture_scope(batch):
                response = await client.get(url)
            response.raise_for_status()
        if len(batch.captures) != 1:
            raise ValueError("perp_funding_history_one_capture_required")
        entry = batch.captures[0]
        # Authentication and PIT check happen immediately, not just on future reads.
        decode_funding_history(
            _capture(RawCaptureRecord(**entry)), response.content, inst_id=inst_id, as_of=utcnow()
        )
        return entry["capture_id"]

    @staticmethod
    def _millis(value: datetime) -> int:
        return int(aware_utc(value).timestamp() * 1000)

    async def collect_coverage(
        self, *, inst_id: str, provider_lower: datetime, provider_upper: datetime
    ) -> tuple[str, tuple[tuple[str, int, int], ...], str]:
        """Capture time/history pages/time; an explicit empty page terminates the proof."""
        lower, upper = aware_utc(provider_lower), aware_utc(provider_upper)
        if lower >= upper or upper - lower > timedelta(days=93):
            raise ValueError("perp_funding_coverage_interval_invalid")
        # OKX bounds are exclusive. Widen one millisecond so the holding interval
        # remains a subset of the authenticated source interval.
        before_ms = self._millis(lower) - 1
        initial_after_ms = self._millis(upper) + 1
        batch = CaptureBatch(
            f"{COVERAGE_VERSION}:{inst_id}:{uuid4().hex}",
            COVERAGE_VERSION,
            self.archive,
            self.lineage.record_capture,
        )
        pages: list[tuple[str, int, int]] = []
        async with CapturingAsyncClient(timeout=15, follow_redirects=False) as client:
            with capture_scope(batch):
                response = await client.get(TIME_URL, headers={"Cache-Control": "no-cache"})
                response.raise_for_status()
                server_before_id = batch.captures[-1]["capture_id"]
                after_ms = initial_after_ms
                for _ in range(8):
                    response = await client.get(
                        funding_coverage_url(inst_id, before_ms=before_ms, after_ms=after_ms),
                        headers={"Cache-Control": "no-cache"},
                    )
                    response.raise_for_status()
                    entry = batch.captures[-1]
                    page = decode_funding_coverage_page(
                        _capture(RawCaptureRecord(**entry)),
                        response.content,
                        inst_id=inst_id,
                        before_ms=before_ms,
                        after_ms=after_ms,
                        as_of=utcnow(),
                    )
                    pages.append((entry["capture_id"], before_ms, after_ms))
                    if not page.settlements:
                        break
                    after_ms = self._millis(page.settlements[-1].settlement_at)
                else:
                    raise ValueError("perp_funding_coverage_page_budget_exhausted")
                response = await client.get(TIME_URL, headers={"Cache-Control": "no-cache"})
                response.raise_for_status()
                server_after_id = batch.captures[-1]["capture_id"]
        if len(batch.captures) != len(pages) + 2:
            raise ValueError("perp_funding_coverage_capture_count_invalid")
        return server_before_id, tuple(pages), server_after_id

    def read_coverage_in_session(
        self,
        session: Session,
        *,
        inst_id: str,
        provider_lower: datetime,
        provider_upper: datetime,
        server_before_id: str,
        pages: tuple[tuple[str, int, int], ...],
        server_after_id: str,
        as_of: datetime,
    ) -> FundingCoverageOriginal:
        lower, upper, cutoff = (
            aware_utc(provider_lower),
            aware_utc(provider_upper),
            aware_utc(as_of),
        )

        def capture(capture_id: str):
            row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
            if row is None or row.body_ref is None:
                raise ValueError("perp_funding_coverage_capture_missing")
            return _capture(row), self.archive.read(row.body_ref)

        before_capture, before_raw = capture(server_before_id)
        after_capture, after_raw = capture(server_after_id)
        contexts = {before_capture.context_id, after_capture.context_id}
        server_before_at = decode_funding_server_time(
            before_capture, before_raw, inst_id=inst_id, as_of=cutoff
        )
        server_after_at = decode_funding_server_time(
            after_capture, after_raw, inst_id=inst_id, as_of=cutoff
        )
        decoded: list[FundingCoveragePage] = []
        all_rows = []
        previous_at = None
        for capture_id, before_ms, after_ms in pages:
            page_capture, raw = capture(capture_id)
            contexts.add(page_capture.context_id)
            page = decode_funding_coverage_page(
                page_capture,
                raw,
                inst_id=inst_id,
                before_ms=before_ms,
                after_ms=after_ms,
                as_of=cutoff,
            )
            if decoded:
                expected_after = self._millis(decoded[-1].settlements[-1].settlement_at)
                if after_ms != expected_after:
                    raise ValueError("perp_funding_coverage_cursor_invalid")
            for row in page.settlements:
                if previous_at is not None and row.settlement_at >= previous_at:
                    raise ValueError("perp_funding_coverage_cross_page_order_invalid")
                previous_at = row.settlement_at
                all_rows.append(row)
            decoded.append(page)
        if (
            len(contexts) != 1
            or not decoded
            or decoded[-1].settlements
            or not before_capture.completed_at <= decoded[0].capture.started_at
            or not decoded[-1].capture.completed_at <= after_capture.started_at
            or server_after_at < server_before_at
            or upper > server_before_at
            or lower >= upper
        ):
            raise ValueError("perp_funding_coverage_envelope_invalid")
        gaps = (
            (
                "settlement_mark_unavailable",
                "account_position_at_settlement_unproven",
            )
            if all_rows
            else ()
        )
        return FundingCoverageOriginal(
            instrument_id="okx:" + inst_id,
            provider_lower=lower,
            provider_upper=upper,
            server_before=before_capture,
            server_before_at=server_before_at,
            pages=tuple(decoded),
            server_after=after_capture,
            server_after_at=server_after_at,
            settlements=tuple(sorted(all_rows, key=lambda item: item.settlement_at)),
            coverage_complete=True,
            gaps=gaps,
        )

    def read_coverage_body_in_session(
        self, session, *, body: dict, as_of: datetime
    ) -> FundingCoverageOriginal:
        """Replay a saved coverage proof against archived bytes, never fetch data."""
        try:
            original = self.read_coverage_in_session(
                session,
                inst_id=body["instrument_id"].removeprefix("okx:"),
                provider_lower=datetime.fromisoformat(body["provider_lower"]),
                provider_upper=datetime.fromisoformat(body["provider_upper"]),
                server_before_id=body["server_before"]["capture_id"],
                pages=tuple(
                    (page["capture"]["capture_id"], page["before_ms"], page["after_ms"])
                    for page in body["pages"]
                ),
                server_after_id=body["server_after"]["capture_id"],
                as_of=as_of,
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("perp_funding_coverage_saved_body_invalid") from error
        if original.body != body:
            raise ValueError("perp_funding_coverage_saved_body_changed")
        # The archived page bounds must cover the claimed holding interval;
        # replaying a narrower empty query is not a full-interval zero proof.
        if (
            not 1 <= len(original.pages) <= 8
            or original.pages[0].before_ms != self._millis(original.provider_lower) - 1
            or original.pages[0].after_ms != self._millis(original.provider_upper) + 1
            or any(page.before_ms != original.pages[0].before_ms for page in original.pages)
        ):
            raise ValueError("perp_funding_coverage_saved_bounds_invalid")
        return original

    def prepare_coverage(
        self,
        *,
        inst_id: str,
        provider_lower: datetime,
        provider_upper: datetime,
        server_before_id: str,
        pages: tuple[tuple[str, int, int], ...],
        server_after_id: str,
        as_of: datetime,
    ):
        return prepare_originals(
            self.database,
            lambda session: self.read_coverage_in_session(
                session,
                inst_id=inst_id,
                provider_lower=provider_lower,
                provider_upper=provider_upper,
                server_before_id=server_before_id,
                pages=pages,
                server_after_id=server_after_id,
                as_of=as_of,
            ),
        )
