from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceContentSnapshot,
    SourcePollFailure,
)
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient


def _failure(code: str) -> SourcePollFailure:
    return SourcePollFailure(
        code, SourceContentSnapshot(parser_version="official-feed-http.v1")
    )


def _public_https(url: str) -> bool:
    target = urlsplit(url)
    hostname = (target.hostname or "").lower().rstrip(".")
    if (target.scheme != "https" or not hostname or target.username is not None
            or target.password is not None or hostname == "localhost"
            or hostname.endswith((".localhost", ".local"))):
        return False
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    return address is None or (address.is_global and not address.is_multicast)


def _configured_proxy(url: str) -> str | None:
    configured = os.environ.get("DECISION_HUB_PUBLIC_HTTP_PROXY")
    if configured is None or configured == "" or not _public_https(url):
        return None
    try:
        proxy = urlsplit(configured)
        if (proxy.scheme not in {"http", "https"} or not proxy.hostname
                or proxy.port is None or not 1 <= proxy.port <= 65535
                or proxy.username is not None or proxy.password is not None
                or proxy.path not in {"", "/"} or proxy.query or proxy.fragment
                or any(char.isspace() for char in configured)
                or "?" in configured or "#" in configured):
            raise ValueError("invalid_proxy_shape")
        checked = httpx.URL(configured)
        if not checked.host:
            raise ValueError("invalid_proxy_host")
    except (ValueError, httpx.InvalidURL):
        raise _failure("source_proxy_config_invalid") from None
    return str(checked)


async def _require_proxy_target(request: httpx.Request) -> None:
    if not _public_https(str(request.url)):
        raise _failure("source_proxy_scope_denied")


@asynccontextmanager
async def public_source_client(url: str) -> AsyncIterator[CapturingAsyncClient]:
    """Preserve default source HTTP; explicit proxying covers public HTTPS only.

    Keep the existing HTTPX UA, TLS, timeout and environment behavior. This is
    transport configuration, not authority to bypass a source access denial.
    """
    proxy = _configured_proxy(url)
    options = {"proxy": proxy} if proxy is not None else {}
    try:
        async with CapturingAsyncClient(timeout=20, **options) as client:
            if proxy is not None:
                client.max_redirects = 5
                client.event_hooks["request"].append(_require_proxy_target)
            yield client
    except httpx.TooManyRedirects:
        if proxy is None:
            raise
        raise _failure("source_proxy_redirect_limit") from None
