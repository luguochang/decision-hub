from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope
from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceContentSnapshot,
    SourcePollFailure,
)


def response_snapshot(status: int, body: str | bytes, parser_version: str) -> SourceContentSnapshot:
    snapshot = SourceContentSnapshot(
        http_status=status,
        parser_version=parser_version,
        content_hash=hashlib.sha256(body.encode() if isinstance(body, str) else body).hexdigest(),
    )
    if status >= 400:
        error_code = {
            401: "source_authentication_required",
            403: "source_access_denied",
            429: "source_rate_limited",
        }.get(status, "source_unavailable")
        raise SourcePollFailure(error_code, snapshot)
    return snapshot


def calendar_snapshot(
    snapshot: SourceContentSnapshot,
    events: Sequence[TextEnvelope],
    *,
    parsed_count: int,
    now: datetime,
) -> SourceContentSnapshot:
    days = []
    for item in events:
        macro = item.macro_event
        if macro is None or macro.status == "cancelled":
            continue
        if macro.time_precision == "date" and macro.scheduled_date is not None:
            today = now.astimezone(ZoneInfo(macro.source_timezone or "UTC")).date()
            if macro.scheduled_date >= today:
                days.append(macro.scheduled_date.isoformat())
        elif macro.time_precision == "exact" and macro.scheduled_at >= now.astimezone(UTC):
            days.append((macro.scheduled_date or macro.scheduled_at.date()).isoformat())
    return snapshot.model_copy(
        update={
            "parse_status": "valid",
            "parsed_count": parsed_count,
            "upcoming_count": len(days),
            "first_upcoming_date": min(days) if days else None,
            "last_upcoming_date": max(days) if days else None,
            "coverage_status": "available" if days else "no_upcoming",
        }
    )
