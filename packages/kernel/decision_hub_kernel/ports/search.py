from __future__ import annotations

from typing import Protocol

from packages.contracts_py.decision_hub_contracts.models import SearchQuery, SearchResult


class SearchCapabilityError(RuntimeError):
    """Stable error boundary for an audited search capability call."""

    def __init__(self, error_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class SearchTransport(Protocol):
    """Provider-owned transport; authorization remains in the Kernel service."""

    async def search(self, query: SearchQuery) -> SearchResult: ...


class SearchCapabilityPort(Protocol):
    async def search(self, query: SearchQuery) -> SearchResult: ...
