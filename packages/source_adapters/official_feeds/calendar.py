from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from icalendar import Calendar

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest, SourcePollResult

CalendarFetcher = Callable[[str], Awaitable[tuple[int, bytes]]]


async def _fetch(url: str) -> tuple[int, bytes]:
    async with httpx.AsyncClient(timeout=20) as client:
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
    ) -> None:
        self.manifest = manifest
        self.endpoint = endpoint
        self.fetcher = fetcher or _fetch

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        status, body = await self.fetcher(self.endpoint)
        if status == 429:
            raise RuntimeError("source_rate_limited")
        if status >= 400:
            raise RuntimeError(f"source_unavailable:{status}")
        calendar = Calendar.from_ical(body.decode("utf-8"))
        events: list[tuple[str, datetime, str]] = []
        for component in calendar.walk("VEVENT"):
            uid = str(component.get("uid", "")).strip()
            summary = str(component.get("summary", "")).strip()
            dtstart = component.get("dtstart")
            if not uid or not summary or dtstart is None:
                continue
            value = dtstart.dt
            if isinstance(value, datetime):
                start = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
            else:
                start = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
            events.append((uid, start, summary))
        events.sort(key=lambda item: (item[1], item[0]))
        selected = [item for item in events if _cursor_for(item) > cursor] if cursor else events
        received_at = datetime.now(UTC)
        envelopes = tuple(
            _to_envelope(self.manifest, uid, start, summary, received_at)
            for uid, start, summary in selected[: self.manifest.max_batch]
        )
        # A capped batch must retain the first unprocessed event for the next poll.
        cursor_after = _cursor_for(selected[len(envelopes) - 1]) if envelopes else cursor
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after=cursor_after,
            envelopes=envelopes,
            fetched_at=received_at,
        )


def _to_envelope(
    manifest: SourceManifest,
    uid: str,
    start: datetime,
    summary: str,
    received_at: datetime,
) -> TextEnvelope:
    raw_text = f"{summary} (scheduled: {start.isoformat()})"
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
        event_family="scheduled_macro_event",
        scheduled_at=start,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )


def _cursor_for(item: tuple[str, datetime, str]) -> str:
    return f"{item[1].isoformat()}|{item[0]}"
