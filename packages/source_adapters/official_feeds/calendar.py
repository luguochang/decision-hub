from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from icalendar import Calendar
from pydantic import HttpUrl, TypeAdapter

from packages.contracts_py.decision_hub_contracts.models import (
    MacroEventPayload,
    SourceType,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceManifest,
    SourcePollFailure,
    SourcePollResult,
)
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

from .calendar_cursor import select_calendar_changes
from .diagnostics import calendar_snapshot, response_snapshot
from .transport import public_source_client

CalendarFetcher = Callable[[str], Awaitable[tuple[int, bytes]]]


async def _fetch(url: str) -> tuple[int, bytes]:
    async with public_source_client(url) as client:
        response = await client.get(url, follow_redirects=True)
        return response.status_code, response.content


class OfficialCalendarSource:
    """Adapter for authorized iCalendar event feeds; events become text observations."""

    def __init__(
        self,
        manifest: SourceManifest,
        endpoint: str,
        *,
        fetcher: CalendarFetcher | None = None,
        source_timezone: str | None = None,
    ) -> None:
        self.manifest = manifest
        self.endpoint = endpoint
        self.fetcher = fetcher or _fetch
        self.source_timezone = source_timezone

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        status, body = await self.fetcher(self.endpoint)
        snapshot = response_snapshot(status, body, self.manifest.version)
        received_at = datetime.now(UTC)
        try:
            events = self._parse_events(body, received_at)
        except (ValueError, KeyError) as exc:
            raise SourcePollFailure(
                str(exc) if "source_parse_error" in str(exc) else "source_parse_error",
                snapshot.model_copy(update={"parse_status": "invalid"}),
            ) from exc
        envelopes, cursor_after = select_calendar_changes(
            events,
            source_id=self.manifest.source_id,
            cursor=cursor,
            max_batch=self.manifest.max_batch,
        )
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after=cursor_after,
            envelopes=envelopes,
            fetched_at=received_at,
            content_snapshot=calendar_snapshot(
                snapshot, events, parsed_count=len(events), now=received_at
            ),
        )

    def _parse_events(self, body: bytes, received_at: datetime) -> list[TextEnvelope]:
        calendar = Calendar.from_ical(body.decode("utf-8"))
        events: list[TextEnvelope] = []
        for component in calendar.walk("VEVENT"):
            uid = str(component.get("uid", "")).strip()
            summary = str(component.get("summary", "")).strip()
            dtstart = component.get("dtstart")
            if not uid or not summary or dtstart is None:
                raise ValueError("source_parse_error:calendar_required_field_missing")
            value = dtstart.dt
            precision: Literal["exact", "date"] = "exact"
            timezone = self.source_timezone
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    if not timezone:
                        raise ValueError("source_parse_error:calendar_timezone_missing")
                    value = value.replace(tzinfo=ZoneInfo(timezone))
                timezone = str(
                    dtstart.params.get("TZID") or getattr(value.tzinfo, "key", "") or value.tzinfo
                )
                start = value.astimezone(UTC)
                scheduled_date = value.date()
            else:
                start = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
                scheduled_date = value
                precision = "date"
            status = (
                "cancelled"
                if str(component.get("status", "")).upper() == "CANCELLED"
                else "scheduled"
            )
            events.append(
                _to_envelope(
                    self.manifest,
                    self.endpoint,
                    uid,
                    start,
                    summary,
                    received_at,
                    scheduled_date=scheduled_date,
                    time_precision=precision,
                    source_timezone=timezone,
                    status=status,
                )
            )
        events.sort(key=lambda item: (item.scheduled_at or received_at, item.event_hint or ""))
        return events


def _to_envelope(
    manifest: SourceManifest,
    endpoint: str,
    uid: str,
    start: datetime,
    summary: str,
    received_at: datetime,
    *,
    scheduled_date: date,
    time_precision: Literal["exact", "date"],
    source_timezone: str | None,
    status: str,
) -> TextEnvelope:
    schedule = (
        start.isoformat() if time_precision == "exact" else f"{scheduled_date} (time not announced)"
    )
    raw_text = (
        f"{summary} (scheduled: {schedule}; precision: {time_precision}; "
        f"source timezone: {source_timezone or 'unknown'}; status: {status}; uid: {uid})"
    )
    return TextEnvelope(
        source_id=manifest.source_id,
        source_type=SourceType.official_feed,
        # DTSTART is a scheduled future occurrence, not information received in the future.
        # Keep it in the canonical text and timestamp the observation at fetch time for PIT.
        observed_at=received_at,
        published_at=None,
        received_at=received_at,
        raw_text=raw_text,
        language="en",
        event_hint=uid,
        event_family=_event_family(summary),
        scheduled_at=start,
        source_url=TypeAdapter(HttpUrl).validate_python(endpoint),
        macro_event=MacroEventPayload(
            external_event_id=uid,
            family=_event_family(summary),
            scheduled_at=start,
            scheduled_date=scheduled_date,
            time_precision=time_precision,
            source_timezone=source_timezone,
            source_authority=manifest.authority_level,
            status=status,
        ),
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )


def _event_family(summary: str) -> str:
    normalized = summary.casefold()
    mappings = (
        (("consumer price", "cpi"), "cpi"),
        (("employment situation", "nonfarm", "payroll"), "nfp"),
        (("producer price", "ppi"), "ppi"),
        (("personal income", "personal consumption", "pce"), "pce"),
        (("gross domestic product", "gdp"), "gdp"),
        (("fomc", "federal open market"), "fomc"),
        (("governing council", "ecb"), "ecb_policy"),
        (("monetary policy meeting", "mpm", "boj"), "boj_mpm"),
    )
    for markers, family in mappings:
        if any(marker in normalized for marker in markers):
            return family
    return "scheduled_macro_event"
