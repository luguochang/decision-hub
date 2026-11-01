"""Capture the publisher's named timing document through normal source polling."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.ports.fomc_stage import POLICY_URL
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest, SourcePollResult
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE
from pydantic import HttpUrl

from .diagnostics import response_snapshot
from .transport import public_source_client

SOURCE_ID = "fomc-stage-policy"
VERSION = "fomc-stage-policy.v1"


def policy_envelope(binding: dict[str, Any], attempt_id: str) -> TextEnvelope:
    body = dict(kind="fomc_policy_native_binding", attempt_id=attempt_id, capture=binding)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    received = datetime.fromisoformat(str(binding["received_at"]))
    return TextEnvelope(
        source_id=SOURCE_ID,
        source_type=SourceType.official_feed,
        observed_at=received,
        received_at=received,
        published_at=None,
        raw_text=raw,
        language="en",
        event_hint="fomc-stage-policy:2026",
        event_family="fomc_policy_rule",
        source_url=HttpUrl(POLICY_URL),
        content_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )


class FomcPolicySource:
    def __init__(self, manifest: SourceManifest) -> None:
        self.manifest = manifest
        self.endpoint = POLICY_URL

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        batch = CURRENT_CAPTURE.get()
        if (
            batch is None
            or not batch.context_id.startswith("source:")
            or batch.adapter_version != VERSION
        ):
            raise ValueError("fomc_policy_native_capture_required")
        async with public_source_client(self.endpoint) as client:
            response = await client.get(self.endpoint, follow_redirects=False)
        snapshot = response_snapshot(response.status_code, response.content, VERSION)
        digest = hashlib.sha256(response.content).hexdigest()
        matches = [c for c in batch.captures if c["body_hash"] == digest and c["url"] == POLICY_URL]
        if len(matches) != 1:
            raise ValueError("fomc_policy_capture_ambiguous")
        capture = matches[0]
        binding = dict(
            capture_id=capture["capture_id"],
            raw_body_hash=digest,
            raw_body_ref=capture["body_ref"],
            url=POLICY_URL,
            received_at=capture["completed_at"].isoformat(),
        )
        envelope = policy_envelope(binding, batch.context_id.removeprefix("source:"))
        return SourcePollResult(
            source_id=SOURCE_ID,
            cursor_before=cursor,
            cursor_after=digest,
            envelopes=(envelope,),
            fetched_at=envelope.received_at,
            next_poll_at=datetime.now(UTC) + timedelta(hours=6),
            content_snapshot=snapshot.model_copy(update=dict(parse_status="valid", parsed_count=1)),
        )
