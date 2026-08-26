from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.source_adapters.transcript_meeting_copilot.adapter import TranscriptSourceAdapter
from tools.contract_codegen.__main__ import check


def test_canonical_schemas_are_valid() -> None:
    assert check() == 0


def test_transcript_adapter_is_text_only() -> None:
    text = "The committee will remain data dependent."
    envelope = TextEnvelope(
        source_id="meeting-copilot",
        source_type=SourceType.transcript,
        observed_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        raw_text=text,
        language="en",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    result = asyncio.run(TranscriptSourceAdapter().ingest(envelope))
    assert result[0].raw_text == text
    assert result[0].source_type is SourceType.transcript
