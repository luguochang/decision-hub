from __future__ import annotations

from packages.provider_adapters.research.documents import (
    DocumentFetcher,
    HttpDocumentResearchAdapter,
)


class OfficialDocumentResearchAdapter(HttpDocumentResearchAdapter):
    def __init__(self, *, fetcher: DocumentFetcher | None = None) -> None:
        super().__init__(
            capability_id="official.macro",
            kind="official",
            authority="official",
            source_id="official-macro-document",
            fetcher=fetcher,
        )
