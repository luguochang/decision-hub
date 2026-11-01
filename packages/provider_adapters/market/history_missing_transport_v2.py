"""One explicit audited GET; callers reserve attempts and persist every result."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPathPage,
    MarketPathScope,
    aware_utc,
    canonical_hash,
    spot_scope,
)
from packages.provider_adapters.market.okx_path import (
    CapturedMarketPage,
    MarketPathError,
    OKXMarketPathAdapter,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

CAPTURE_PRODUCER_V2 = "history-missing-capture.v2"
ORIGINAL_DECODER_V1 = "okx-closed-spot-path.v1"
MAX_BODY_BYTES = 1_000_000
ATTEMPT_TIMEOUT_SECONDS = 15
_ROOT = Path(__file__).resolve().parents[3]
SOURCE_FILES = tuple(
    (name, hashlib.sha256((_ROOT / name).read_bytes()).hexdigest())
    for name in (
        "packages/provider_adapters/market/history_missing_transport_v2.py",
        "packages/provider_adapters/market/okx_path.py",
        "packages/provider_adapters/research/raw_capture.py",
        "packages/kernel/decision_hub_kernel/ports/market_path.py",
    )
)


@dataclass(frozen=True)
class CapturedHistoryPageV2:
    capture: dict
    body: bytes | None
    page: MarketPathPage | None
    semantic_rows: tuple[tuple[int, str], ...]
    error_code: str | None
    cancelled: bool
    source_files: tuple[tuple[str, str], ...] = SOURCE_FILES
    financial_qualified: bool = False


class HistoryMissingTransportV2:
    capture_producer_version = CAPTURE_PRODUCER_V2
    original_decoder_version = ORIGINAL_DECODER_V1
    source_files = SOURCE_FILES

    def __init__(
        self,
        archive: RawBodyArchive,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        fixture_mode: bool = False,
    ) -> None:
        if type(archive) is not RawBodyArchive:
            raise ValueError("history_missing_archive_dependency_invalid")
        if transport is not None and not fixture_mode:
            raise ValueError("history_missing_fixture_transport_requires_fixture_mode")
        if fixture_mode and transport is None:
            raise ValueError("history_missing_fixture_requires_explicit_transport")
        self.archive, self.transport, self.fixture_mode = archive, transport, fixture_mode

    @staticmethod
    def _request(context_id, scope, cursor, start_at, end_at) -> tuple[datetime, datetime, str]:
        if not isinstance(context_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,200}", context_id
        ):
            raise ValueError("history_missing_context_invalid")
        if (
            type(scope) is not MarketPathScope
            or scope.instrument not in {"BTC-USDT", "ETH-USDT"}
            or scope != spot_scope(scope.instrument)
        ):
            raise ValueError("history_missing_scope_invalid")
        start, end = aware_utc(start_at), aware_utc(end_at)
        if (
            start.timestamp() % 60
            or end.timestamp() % 60
            or not 0 < (end - start).total_seconds() <= 90 * 86400
            or end > datetime.now(UTC)
        ):
            raise ValueError("history_missing_window_invalid")
        if not isinstance(cursor, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", cursor):
            raise ValueError("history_missing_cursor_invalid")
        after = int(cursor)
        if after % 60_000 or not int(start.timestamp() * 1000) < after <= int(
            end.timestamp() * 1000
        ):
            raise ValueError("history_missing_cursor_invalid")
        url = "https://www.okx.com/api/v5/market/history-candles?" + urlencode(
            {"instId": scope.instrument, "bar": "1m", "after": cursor, "limit": 300}
        )
        return start, end, url

    async def _decode(self, body, received, ref, scope, cursor, start, end) -> MarketPathPage:
        payload = json.loads(body)
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise ValueError("history_missing_provider_non_success")
        rows = payload.get("data")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
            raise ValueError("history_missing_rows_empty_or_over_limit")
        previous = int(cursor)
        digest = hashlib.sha256(body).hexdigest()
        # Validate even rows older than this window; never admit an unclosed,
        # malformed or duplicate original as a checkpoint boundary.
        for index, row in enumerate(rows):
            if (
                not isinstance(row, list)
                or len(row) != 9
                or not isinstance(row[0], str)
                or not re.fullmatch(r"[1-9][0-9]{0,15}", row[0])
                or str(row[8]) != "1"
            ):
                raise ValueError("history_missing_row_shape_or_confirmation_invalid")
            stamp = int(row[0])
            if stamp >= previous or stamp % 60_000:
                raise ValueError("history_missing_row_cursor_or_minute_invalid")
            previous = stamp
            opened = datetime.fromtimestamp(stamp / 1000, UTC)
            MarketCandle(
                scope_id=scope.scope_id,
                venue=scope.venue,
                instrument=scope.instrument,
                price_type=scope.price_type,
                open_at=opened,
                close_at=opened + timedelta(minutes=1),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume_base=Decimal(str(row[5])),
                volume_quote=Decimal(str(row[7])),
                received_at=received,
                capture_ref=ref,
                capture_hash=digest,
                row_index=index,
            )

        async def original(_url: str) -> CapturedMarketPage:
            return CapturedMarketPage(body, received, ref)

        local_end = datetime.fromtimestamp(int(cursor) / 1000, UTC)
        page = await OKXMarketPathAdapter(fetcher=original).candles(
            scope=scope,
            start_at=max(start, local_end - timedelta(days=1)),
            end_at=local_end,
            cursor=cursor,
        )
        if not page.candles:
            raise ValueError("history_missing_selected_rows_empty")
        return page.model_copy(
            update={
                "next_cursor": str(previous) if previous > int(start.timestamp() * 1000) else None
            }
        )

    async def fetch_one(
        self,
        *,
        context_id: str,
        scope: MarketPathScope,
        cursor: str,
        start_at: datetime,
        end_at: datetime,
    ) -> CapturedHistoryPageV2:
        start, end, url = self._request(context_id, scope, cursor, start_at, end_at)
        started = datetime.now(UTC)
        status, media, body, error, chunks, size = None, "", None, None, [], 0
        cancelled = False
        try:
            async with asyncio.timeout(ATTEMPT_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    timeout=15, trust_env=True, transport=self.transport, follow_redirects=False
                ) as client:
                    async with client.stream(
                        "GET", url, headers={"Accept-Encoding": "identity"}
                    ) as response:
                        status = response.status_code
                        media = response.headers.get("content-type", "")[:128]
                        async for chunk in response.aiter_raw():
                            size += len(chunk)
                            if size > MAX_BODY_BYTES:
                                raise ValueError("history_missing_body_too_large")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        if response.headers.get("content-encoding", "").strip().lower() not in {
                            "",
                            "identity",
                        }:
                            error = "history_missing_content_encoding_unsupported"
                        elif not 200 <= status < 300:
                            error = f"history_missing_http_{status}"
        except asyncio.CancelledError:
            error, cancelled = "history_missing_transport_cancelled", True
        except (TimeoutError, httpx.TimeoutException):
            error = "history_missing_transport_timeout"
        except httpx.HTTPError:
            error = "history_missing_transport_unavailable"
        except ValueError as exc:
            error = str(exc) if str(exc).startswith("history_missing_") else "history_missing_error"
        except Exception:
            error = "history_missing_transport_unavailable"
        completed = datetime.now(UTC)
        digest = ref = None
        if body is not None:
            try:
                digest, ref = self.archive.put(body)
            except (OSError, ValueError):
                error = error or "history_missing_archive_unavailable"
        capture = {
            "capture_id": "raw_" + uuid4().hex,
            "context_id": context_id,
            "url": url,
            "status_code": status,
            "body_hash": digest,
            "body_ref": ref,
            "byte_count": size,
            "media_type": media,
            "adapter_version": CAPTURE_PRODUCER_V2,
            "capture_producer_version": CAPTURE_PRODUCER_V2,
            "original_decoder_version": ORIGINAL_DECODER_V1,
            "fixture_mode": self.fixture_mode,
            "error_code": error,
            "started_at": started,
            "completed_at": completed,
            "recorded_at": datetime.now(UTC),
        }
        page = None
        if error is None and body is not None and ref is not None:
            try:
                page = await self._decode(body, completed, ref, scope, cursor, start, end)
            except asyncio.CancelledError:
                error, cancelled = "history_missing_transport_cancelled", True
            except (
                ValueError,
                ArithmeticError,
                OverflowError,
                OSError,
                RecursionError,
                MarketPathError,
            ):
                error = "history_missing_original_decode_invalid"
        capture["error_code"] = error
        semantic_rows = (
            tuple(
                (int(bar.open_at.timestamp() * 1000), canonical_hash(bar.semantic_payload()))
                for bar in page.candles
            )
            if page is not None
            else ()
        )
        return CapturedHistoryPageV2(capture, body, page, semantic_rows, error, cancelled)
