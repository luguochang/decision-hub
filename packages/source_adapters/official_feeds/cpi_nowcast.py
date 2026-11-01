"""Normal read-only official nowcast source with source-scope raw capture."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts.models import SourceManifest, SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.ports.cpi_nowcast import (
    NOWCAST_METRICS, NOWCAST_SOURCE_ID, NOWCAST_URL, NOWCAST_VERSION, parse_cpi_nowcast_metrics,
)
from packages.kernel.decision_hub_kernel.ports.bls_cpi import period_offset
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollFailure, SourcePollResult
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE

from .diagnostics import response_snapshot
from .transport import public_source_client

CURSOR_PREFIX = "cpi-nowcast-v1:"


@dataclass(frozen=True)
class NowcastPageResponse:
    status: int
    body: bytes
    provenance: dict


async def _fetch() -> NowcastPageResponse:
    batch = CURRENT_CAPTURE.get()
    if batch is None or not batch.context_id.startswith("source:") or batch.adapter_version != NOWCAST_VERSION:
        raise ValueError("cpi_nowcast_native_source_capture_required")
    async with public_source_client(NOWCAST_URL) as client:
        response = await client.get(NOWCAST_URL, follow_redirects=False)
    digest = hashlib.sha256(response.content).hexdigest()
    captures = [entry for entry in batch.captures if entry.get("body_hash") == digest and entry["url"] == NOWCAST_URL]
    if len(captures) != 1:
        raise ValueError("cpi_nowcast_native_capture_missing_or_ambiguous")
    capture = captures[0]
    return NowcastPageResponse(response.status_code, response.content, {
        "capture_id": capture["capture_id"], "attempt_id": batch.context_id.removeprefix("source:"),
        "received_at": capture["completed_at"],
    })


class ClevelandCpiNowcastSource:
    def __init__(self, manifest: SourceManifest, *, fetcher: Callable[[], Awaitable[NowcastPageResponse]] | None = None, clock: Callable[[], datetime] | None = None):
        self.manifest = manifest
        self.fetcher = fetcher or _fetch
        self.clock = clock or (lambda: datetime.now(UTC))

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("cpi_nowcast_clock_offset_required")
        now = now.astimezone(UTC)
        reference_period = now.strftime("%Y-%m")
        result = await self.fetcher()
        snapshot = response_snapshot(result.status, result.body, NOWCAST_VERSION)
        try:
            metrics = []
            # The actual official HTML exposes the current and previous month;
            # preserve both active periods, especially before the prior month's
            # release in early-month sessions. This is not a history retention cap.
            for period in (period_offset(reference_period, -1), reference_period):
                try:
                    group = parse_cpi_nowcast_metrics(result.body, reference_period=period, provenance=result.provenance)
                except ValueError as error:
                    if str(error) == "cpi_nowcast_reference_period_unavailable_or_duplicate":
                        raise  # Missing or duplicate explicit rows remain a parse gap.
                    raise
                if all(metric.value is None for metric in group):
                    continue
                if any(metric.value is None for metric in group):
                    raise ValueError("cpi_nowcast_active_period_incomplete")
                metrics.extend(group)
            metrics = tuple(metrics)
        except (ValueError, TypeError) as error:
            raise SourcePollFailure("source_cpi_nowcast_payload_invalid", snapshot.model_copy(update={"parse_status": "invalid"})) from error
        snapshot = snapshot.model_copy(update={"parse_status": "valid", "parsed_count": len(metrics)})
        if not metrics:
            raise SourcePollFailure("source_cpi_nowcast_four_metrics_unavailable", snapshot)
        seen = {}
        if cursor:
            try:
                if not cursor.startswith(CURSOR_PREFIX):
                    raise ValueError
                data = json.loads(cursor.removeprefix(CURSOR_PREFIX))
                if data["source_id"] != NOWCAST_SOURCE_ID or not isinstance(data["seen"], dict):
                    raise ValueError
                seen = data["seen"]
                if any(key.split(":")[-1] not in NOWCAST_METRICS or len(key.split(":")) != 2 for key in seen) or any(not isinstance(value, dict) or set(value) != {"semantic_hash", "content_hash"} or any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h) for h in value.values()) for value in seen.values()):
                    raise ValueError
            except (ValueError, KeyError, TypeError) as error:
                raise SourcePollFailure("source_cpi_nowcast_cursor_invalid", snapshot) from error
        envelopes = []
        for metric in metrics:
            key = metric.reference_period + ":" + metric.metric_id
            old = seen.get(key)
            if old and old["semantic_hash"] == metric.semantic_hash:
                continue
            raw = json.dumps(metric.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            envelopes.append(TextEnvelope(
                source_id=NOWCAST_SOURCE_ID, source_type=SourceType.official_feed,
                observed_at=metric.received_at, received_at=metric.received_at, published_at=None,
                raw_text=raw, language="en", event_hint="cpi_nowcast_metric", event_family=None,
                source_url=NOWCAST_URL, scheduled_at=None, macro_event=None,
                revision_of=("sha256:" + old["content_hash"]) if old else None, content_hash=digest,
            ))
            seen[key] = {"semantic_hash": metric.semantic_hash, "content_hash": digest}
        seen = {key: value for key, value in seen.items() if key in {m.reference_period + ":" + m.metric_id for m in metrics}}
        after = CURSOR_PREFIX + json.dumps({"source_id": NOWCAST_SOURCE_ID, "seen": seen}, sort_keys=True, separators=(",", ":"))
        return SourcePollResult(source_id=NOWCAST_SOURCE_ID, cursor_before=cursor, cursor_after=after, envelopes=tuple(envelopes), fetched_at=result.provenance["received_at"], next_poll_at=now+timedelta(hours=6), content_snapshot=snapshot)
