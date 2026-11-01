"""Bounded JSON for a saved whole native transport, not one Provider callback.

Parsing grants no trust. Callers still verify original rows and exact raw byte
hashes before using the selected occurrence. ProviderInput limits are unchanged.
"""

from __future__ import annotations

import json
import math
from typing import Any, cast

# The existing transport observation writer's envelope limit is 64 MiB. A whole
# native transcript can contain many individually bounded Provider callbacks.
MAX_TRANSPORT_JSON_BYTES = 64 * 1024 * 1024


def read_transport_events_json(text: str) -> list[dict[str, Any]]:
    """Decode only event arrays, retaining duplicate/nonfinite/complexity guards."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("research_transport_duplicate_json_key")
            result[key] = value
        return result

    def invalid(_: str) -> Any:
        raise ValueError("research_transport_nonfinite_json")

    try:
        if len(text.encode("utf-8")) > MAX_TRANSPORT_JSON_BYTES:
            raise ValueError("research_transport_bytes_exceeded")
        parsed = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
        count = 0

        def bound(value: Any, depth: int) -> None:
            nonlocal count
            count += 1
            if depth > 64 or count > 100_000:
                raise ValueError("research_transport_json_complexity_exceeded")
            if isinstance(value, dict):
                for key, item in value.items():
                    key.encode("utf-8")
                    bound(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    bound(item, depth + 1)
            elif isinstance(value, str):
                value.encode("utf-8")
            elif type(value) is float and not math.isfinite(value):
                invalid("")

        bound(parsed, 0)
        if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
            raise ValueError("research_transport_events_invalid")
        return cast("list[dict[str, Any]]", parsed)
    except (RecursionError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("research_transport_json_invalid") from exc
