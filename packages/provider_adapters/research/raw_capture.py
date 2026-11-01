"""Explicit HTTP capture; context-local, with no global httpx monkeypatching."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx

# Query values not explicitly used by public data endpoints are redacted.
PUBLIC_PARAMETERS = frozenset(
    {
        "symbol",
        "symbols",
        "instId",
        "instType",
        "bar",
        "interval",
        "limit",
        "after",
        "before",
        "start",
        "end",
        "startTime",
        "endTime",
        "currency_pair",
        "contract",
        "contract_code",
        "period",
        "amount_type",
        "series_id",
        "file_type",
        "observation_start",
        "observation_end",
        "realtime_start",
        "realtime_end",
        "period1",
        "period2",
        "range",
        "events",
        "market",
        "type",
        "count",
        "size",
        "from",
        "to",
        "sort_order",
        "units",
        "frequency",
        "aggregation_method",
    }
)


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    query = urlencode(
        [
            (k, v if k in PUBLIC_PARAMETERS else "REDACTED")
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, host, parts.path, query, ""))[:2048]


class RawBodyArchive:
    def __init__(self, root: Path):
        self.root = root

    def put(self, body: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                # Preserve corrupt bytes for diagnosis; only a newly received
                # body with this exact SHA can restore the content address.
                os.replace(path, path.with_name(f"{digest}.corrupt.{uuid4().hex}"))
        if not path.exists():
            temporary = path.with_name(f".{digest}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(body)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return digest, f"raw://sha256/{digest}"

    def read(self, ref: str) -> bytes:
        if not re.fullmatch(r"raw://sha256/[a-f0-9]{64}", ref):
            raise ValueError("raw_capture_ref_invalid")
        digest = ref.rsplit("/", 1)[1]
        body = (self.root / digest[:2] / digest).read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("raw_capture_archive_corrupt")
        return body


@dataclass
class CaptureBatch:
    context_id: str
    adapter_version: str
    archive: RawBodyArchive
    persist: Callable[[dict], None]
    captures: list[dict] = field(default_factory=list)
    # Populated by the trusted reuse adapter, never model-supplied metadata.
    reused_facts: dict[str, str] = field(default_factory=dict)
    reused_evidence: dict[str, str] = field(default_factory=dict)
    reused_observed: dict[str, datetime] = field(default_factory=dict)
    reuse_key: str | None = None

    def record(self, *, url, started_at, completed_at, response=None, error=None):
        body = response.content if response is not None else b""
        digest = ref = None
        # Keep an explicit metadata failure for oversize bodies, never truncate
        # bytes and call them the original response.
        if response is not None and len(body) <= 16 * 1024 * 1024:
            digest, ref = self.archive.put(body)
        elif response is not None:
            error = "raw_capture_body_too_large"
        entry = {
            "capture_id": f"raw_{uuid4().hex}",
            "context_id": self.context_id,
            "url": safe_url(url),
            "status_code": response.status_code if response else None,
            "body_hash": digest,
            "body_ref": ref,
            "byte_count": len(body),
            "media_type": (response.headers.get("content-type", "")[:128] if response else ""),
            "adapter_version": self.adapter_version,
            "error_code": error,
            "started_at": started_at,
            "completed_at": completed_at,
            "recorded_at": datetime.now(UTC),
        }
        self.persist(entry)
        self.captures.append(entry)


CURRENT_CAPTURE: ContextVar[CaptureBatch | None] = ContextVar("research_raw_capture", default=None)


def reused_observation_times():
    batch = CURRENT_CAPTURE.get()
    return dict(batch.reused_observed) if batch else {}


@contextmanager
def capture_scope(batch: CaptureBatch) -> Iterator[CaptureBatch]:
    token = CURRENT_CAPTURE.set(batch)
    try:
        yield batch
    finally:
        CURRENT_CAPTURE.reset(token)


class CapturingAsyncClient(httpx.AsyncClient):
    async def send(
        self,
        request: httpx.Request,
        *,
        stream=False,
        auth=httpx.USE_CLIENT_DEFAULT,
        follow_redirects=httpx.USE_CLIENT_DEFAULT,
    ):
        batch = CURRENT_CAPTURE.get()
        started = datetime.now(UTC)
        try:
            response = await super().send(
                request, stream=stream, auth=auth, follow_redirects=follow_redirects
            )
        except httpx.HTTPError as exc:
            if batch is not None:
                batch.record(
                    url=str(request.url),
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    error=type(exc).__name__,
                )
            raise
        if batch is not None:
            # Call sites here use ordinary non-streamed public GET responses.
            # Never archive authentication headers, request bodies or local tools.
            bls_public_source = (batch.context_id.startswith("source:")
                                 and batch.adapter_version == "bls-cpi-index-derived.v1"
                                 and _public_bls_cpi_post(request))
            if (request.method != "GET" and not bls_public_source) or stream:
                raise ValueError("raw_capture_method_unsupported")
            batch.record(
                url=str(response.url),
                started_at=started,
                completed_at=datetime.now(UTC),
                response=response,
            )
        return response


def _public_bls_cpi_post(request: httpx.Request) -> bool:
    """Archive only the owned, keyless four-series public v1 response."""
    if (request.method != "POST" or str(request.url) != "https://api.bls.gov/publicAPI/v1/timeseries/data/"
        or any(name in request.headers for name in ("authorization", "cookie", "x-api-key"))
        or len(request.content) > 2048):
        return False
    try:
        body = json.loads(request.content)
        if (set(body) != {"seriesid", "startyear", "endyear"}
            or not isinstance(body["seriesid"], list) or len(body["seriesid"]) != 4
            or set(body["seriesid"]) != {"CUSR0000SA0", "CUSR0000SA0L1E", "CUUR0000SA0", "CUUR0000SA0L1E"}
            or not all(isinstance(body[key], str) and re.fullmatch(r"20\d{2}", body[key]) for key in ("startyear", "endyear"))):
            return False
        return 1 <= int(body["endyear"]) - int(body["startyear"]) + 1 <= 3
    except (TypeError, KeyError, ValueError):
        return False
