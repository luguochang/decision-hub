"""One public v1 request for four index series, with normal native capture."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts.models import SourceManifest, SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.ports.bls_cpi import (
    BLS_SERIES, BLS_SOURCE_ID, BLS_URL, BLS_VERSION, parse_bls_cpi_metrics, period_offset,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollFailure, SourcePollResult
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE

from .diagnostics import response_snapshot
from .transport import public_source_client

CURSOR_PREFIX = "bls-cpi-v1:"


@dataclass(frozen=True)
class BlsApiResponse:
    status: int
    body: bytes
    provenance: dict


async def _fetch(startyear: int, endyear: int) -> BlsApiResponse:
    batch = CURRENT_CAPTURE.get()
    if batch is None or not batch.context_id.startswith("source:"):
        raise ValueError("bls_native_source_capture_required")
    payload = {"seriesid": [value[0] for value in BLS_SERIES.values()], "startyear": str(startyear), "endyear": str(endyear)}
    async with public_source_client(BLS_URL) as client:
        response = await client.post(BLS_URL, json=payload, follow_redirects=False)
    digest = hashlib.sha256(response.content).hexdigest()
    captures = [entry for entry in batch.captures if entry.get("body_hash") == digest and entry["url"] == BLS_URL]
    if len(captures) != 1:
        raise ValueError("bls_native_capture_missing_or_ambiguous")
    capture = captures[0]
    return BlsApiResponse(response.status_code, response.content, {
        "capture_id": capture["capture_id"], "attempt_id": batch.context_id.removeprefix("source:"),
        "received_at": capture["completed_at"], "request_startyear": startyear, "request_endyear": endyear,
    })


class BlsPublicApiSource:
    def __init__(self, manifest: SourceManifest, *, fetcher: Callable[[int, int], Awaitable[BlsApiResponse]] | None = None, clock: Callable[[], datetime] | None = None):
        self.manifest = manifest
        self.fetcher = fetcher or _fetch
        self.clock = clock or (lambda: datetime.now(UTC))

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        now = self.clock().astimezone(UTC)
        reference_period = period_offset(now.strftime("%Y-%m"), -1)
        result = await self.fetcher(now.year - 2, now.year)
        snapshot = response_snapshot(result.status, result.body, BLS_VERSION)
        try:
            metrics = parse_bls_cpi_metrics(result.body, reference_period=reference_period, provenance=result.provenance)
        except (ValueError, TypeError) as error:
            raise SourcePollFailure("source_bls_payload_invalid", snapshot.model_copy(update={"parse_status": "invalid"})) from error
        snapshot = snapshot.model_copy(update={"parse_status": "valid", "parsed_count": len(metrics)})
        if all(metric.actual_value is None for metric in metrics):
            raise SourcePollFailure("source_bls_reference_period_unavailable", snapshot)
        seen = {}
        if cursor:
            try:
                if not cursor.startswith(CURSOR_PREFIX):
                    raise ValueError
                data = json.loads(cursor.removeprefix(CURSOR_PREFIX))
                if data["source_id"] != BLS_SOURCE_ID or not isinstance(data["seen"], dict):
                    raise ValueError
                seen = data["seen"]
                if set(seen) - set(BLS_SERIES) or any(set(value) != {"semantic_hash", "content_hash"} or any(not isinstance(h, str) or len(h) != 64 for h in value.values()) for value in seen.values()):
                    raise ValueError
            except (ValueError, KeyError, TypeError) as error:
                raise SourcePollFailure("source_bls_cursor_invalid", snapshot) from error
        envelopes = []
        for metric in metrics:
            old = seen.get(metric.metric_id)
            if old and old["semantic_hash"] == metric.semantic_hash:
                continue
            raw = json.dumps(metric.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            envelopes.append(TextEnvelope(
                source_id=BLS_SOURCE_ID, source_type=SourceType.official_feed,
                observed_at=metric.received_at, received_at=metric.received_at, published_at=None,
                # Index retrieval is not a timed CPI release. In particular it
                # must not drive the nearest-family calendar reconciliation.
                raw_text=raw, language="en", event_hint="cpi_index_metric", event_family=None,
                source_url=BLS_URL, scheduled_at=None, macro_event=None,
                revision_of=("sha256:" + old["content_hash"]) if old else None, content_hash=digest,
            ))
            seen[metric.metric_id] = {"semantic_hash": metric.semantic_hash, "content_hash": digest}
        after = CURSOR_PREFIX + json.dumps({"source_id": BLS_SOURCE_ID, "seen": seen}, sort_keys=True, separators=(",", ":"))
        return SourcePollResult(source_id=BLS_SOURCE_ID, cursor_before=cursor, cursor_after=after, envelopes=tuple(envelopes), fetched_at=result.provenance["received_at"], next_poll_at=now + timedelta(hours=6), content_snapshot=snapshot)
