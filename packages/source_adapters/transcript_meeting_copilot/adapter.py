from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope


class TranscriptSourceAdapter:
    """ASR/Meeting Copilot boundary; audio capture and transcription stay upstream."""

    source_id = "transcript-adapter"

    async def ingest(self, envelope: TextEnvelope) -> list[TextEnvelope]:
        if envelope.source_type.value != "transcript":
            raise ValueError("transcript adapter requires source_type=transcript")
        return [envelope]
