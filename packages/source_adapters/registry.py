from __future__ import annotations

from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector, SourceManifest


class SourceRegistry:
    """In-process capability registry; durable state belongs to the Kernel ledger."""

    def __init__(self) -> None:
        self._sources: dict[str, SourceConnector] = {}

    def register(self, source: SourceConnector) -> None:
        source_id = source.manifest.source_id
        if source_id in self._sources:
            raise ValueError(f"source already registered: {source_id}")
        self._sources[source_id] = source

    def manifests(self) -> list[SourceManifest]:
        return [source.manifest for source in self._sources.values()]

    def get(self, source_id: str) -> SourceConnector:
        source = self._sources.get(source_id)
        if source is None:
            raise KeyError(source_id)
        return source
