from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope


class ManualTextSource:
    source_id = "manual-text"

    async def ingest(self, envelope: TextEnvelope) -> list[TextEnvelope]:
        return [envelope]
