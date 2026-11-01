from __future__ import annotations

import os


def graceful_shutdown_timeout_seconds() -> int:
    raw = os.getenv("HUB_API_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", "30")
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("hub_api_graceful_shutdown_timeout_invalid") from exc
    if not 5 <= seconds <= 120:
        raise ValueError("hub_api_graceful_shutdown_timeout_invalid")
    return seconds
