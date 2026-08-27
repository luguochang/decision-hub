from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.source_adapters.transcript_meeting_copilot import (
    TranscriptFragment,
    TranscriptSourceAdapter,
)


def test_transcript_fragment_preserves_text_revision_and_pit_timestamps() -> None:
    observed_at = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
    received_at = datetime(2026, 8, 27, 1, 0, 2, tzinfo=UTC)
    fragment = TranscriptFragment(
        fragment_id="fragment-2",
        source_id="meeting-copilot:fomc",
        text="The committee sees upside inflation risks.",
        language="en",
        observed_at=observed_at,
        received_at=received_at,
        revision_of="observation-1",
        provisional=False,
    )

    envelope = TranscriptSourceAdapter.from_fragment(fragment)

    assert envelope.source_type is SourceType.transcript
    assert envelope.observed_at == observed_at
    assert envelope.received_at == received_at
    assert envelope.revision_of == "observation-1"
    assert envelope.event_hint == "transcript"
    assert envelope.content_hash == hashlib.sha256(fragment.text.encode("utf-8")).hexdigest()


def test_transcript_adapter_accepts_only_transcript_text_envelopes() -> None:
    now = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
    adapter = TranscriptSourceAdapter()
    transcript = TextEnvelope(
        source_id="meeting-copilot:fomc",
        source_type=SourceType.transcript,
        observed_at=now,
        received_at=now,
        raw_text="Provisional remark.",
        language="en",
        content_hash=hashlib.sha256(b"Provisional remark.").hexdigest(),
    )
    wrong_source_type = transcript.model_copy(update={"source_type": SourceType.manual})

    assert asyncio.run(adapter.ingest(transcript)) == [transcript]
    with pytest.raises(ValueError, match="source_type=transcript"):
        asyncio.run(adapter.ingest(wrong_source_type))
