from __future__ import annotations

from collections.abc import Callable

from packages.contracts_py.decision_hub_contracts.models import SearchQuery, SearchResult


class FakeSearchTransport:
    """Deterministic offline transport for contract and orchestration tests."""

    def __init__(self, result: SearchResult | Callable[[SearchQuery], SearchResult]) -> None:
        self._result = result
        self.calls: list[SearchQuery] = []

    async def search(self, query: SearchQuery) -> SearchResult:
        self.calls.append(query)
        return self._result(query) if callable(self._result) else self._result
