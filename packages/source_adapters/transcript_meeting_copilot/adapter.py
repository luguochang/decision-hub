from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope


class TranscriptFragment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fragment_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=200_000)
    language: str = Field(min_length=2, max_length=16)
    observed_at: datetime
    received_at: datetime
    revision_of: str | None = None
    provisional: bool = True


class TranscriptSourceAdapter:
    """ASR/Meeting Copilot boundary; audio capture and transcription stay upstream."""

    source_id = "transcript-adapter"

    async def ingest(self, envelope: TextEnvelope) -> list[TextEnvelope]:
        if envelope.source_type.value != "transcript":
            raise ValueError("transcript adapter requires source_type=transcript")
        return [envelope]

    @staticmethod
    def from_fragment(fragment: TranscriptFragment) -> TextEnvelope:
        return TextEnvelope(
            source_id=fragment.source_id,
            source_type=SourceType.transcript,
            observed_at=fragment.observed_at,
            received_at=fragment.received_at,
            raw_text=fragment.text,
            language=fragment.language,
            event_hint="transcript_provisional" if fragment.provisional else "transcript",
            revision_of=fragment.revision_of,
            content_hash=hashlib.sha256(fragment.text.encode("utf-8")).hexdigest(),
        )
