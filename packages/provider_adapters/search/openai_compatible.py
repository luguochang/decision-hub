from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

from packages.contracts_py.decision_hub_contracts.models import SearchQuery, SearchResult
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityError

SearchCall = Callable[
    [SearchQuery],
    Awaitable[SearchResult | Mapping[str, object]] | SearchResult | Mapping[str, object],
]


class OpenAICompatibleSearchTransport:
    """Typed seam for an explicitly configured OpenAI-compatible search client.

    Provider-specific SDK parsing stays in the injected call so an API variant can
    be replaced without changing Kernel authorization or canonical result contracts.
    """

    def __init__(self, execute: SearchCall) -> None:
        self._execute = execute

    async def search(self, query: SearchQuery) -> SearchResult:
        try:
            value = self._execute(query)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, SearchResult):
                return value
            raw = cast(object, value)
            if not isinstance(raw, Mapping):
                raise TypeError("search provider returned a non-object response")
            return SearchResult.model_validate(dict(raw))
        except SearchCapabilityError:
            raise
        except (TypeError, ValueError) as exc:
            raise SearchCapabilityError(
                "search_output_invalid", "search provider returned invalid canonical output"
            ) from exc
