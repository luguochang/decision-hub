"""Complete closed spot paths, separate from the legacy two-close window adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPathPage,
    MarketPathScope,
    MarketPricePath,
    aware_utc,
    path_hashes,
)
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE, CapturingAsyncClient


class MarketPathError(ValueError):
    def __init__(
        self, code: str, *, retryable: bool = False, retry_after_seconds: float | None = None
    ):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class CapturedMarketPage:
    body: bytes
    received_at: datetime
    capture_ref: str | None = None


Fetcher = Callable[[str], Awaitable[CapturedMarketPage]]


async def _fetch(url: str) -> CapturedMarketPage:
    try:
        async with CapturingAsyncClient(timeout=15) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        retry_after = exc.response.headers.get("Retry-After")
        try:
            seconds = max(0.0, float(retry_after)) if retry_after else None
        except ValueError:
            seconds = None
        raise MarketPathError(
            f"market_path_http_{status}",
            retryable=status == 429 or status >= 500,
            retry_after_seconds=seconds,
        ) from exc
    except httpx.HTTPError as exc:
        raise MarketPathError("market_path_transport_unavailable", retryable=True) from exc
    if len(response.content) > 1_000_000:
        raise MarketPathError("market_path_body_too_large")
    received, ref = datetime.now(UTC), None
    batch = CURRENT_CAPTURE.get()
    digest = hashlib.sha256(response.content).hexdigest()
    if batch is not None:
        capture = next(
            (item for item in reversed(batch.captures) if item.get("body_hash") == digest), None
        )
        if capture:
            received, ref = capture["completed_at"], capture["body_ref"]
    return CapturedMarketPage(body=response.content, received_at=received, capture_ref=ref)


class OKXMarketPathAdapter:
    adapter_version = "okx-closed-spot-path.v1"

    def __init__(self, *, fetcher: Fetcher | None = None, max_pages: int = 6):
        if not 1 <= max_pages <= 32:
            raise ValueError("market_path_page_budget_invalid")
        self.fetcher = fetcher or _fetch
        self.max_pages = max_pages

    @staticmethod
    def _window(scope, start_at, end_at):
        start, end = aware_utc(start_at), aware_utc(end_at)
        if (
            scope.venue != "okx"
            or scope.instrument_kind != "spot"
            or scope.price_type != "trade"
            or scope.quote_currency != "USDT"
        ):
            raise MarketPathError("market_path_scope_not_supported")
        if (
            start.timestamp() % 60
            or end.timestamp() % 60
            or not 0 < (end - start).total_seconds() <= 86400
        ):
            raise MarketPathError("market_path_window_invalid")
        return start, end

    async def candles(
        self,
        *,
        scope: MarketPathScope,
        start_at: datetime,
        end_at: datetime,
        cursor: str | None = None,
    ) -> MarketPathPage:
        start, end = self._window(scope, start_at, end_at)
        after = int(cursor) if cursor is not None else int(end.timestamp() * 1000)
        if after % 60_000 or not int(start.timestamp() * 1000) <= after <= int(
            end.timestamp() * 1000
        ):
            raise MarketPathError("market_path_cursor_invalid")
        url = "https://www.okx.com/api/v5/market/history-candles?" + urlencode(
            {
                "instId": scope.instrument,
                "bar": "1m",
                "after": after,
                "limit": 300,
            }
        )
        captured = await self.fetcher(url)
        received = aware_utc(captured.received_at)
        try:
            payload = json.loads(captured.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MarketPathError("market_path_json_invalid") from exc
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            code = str(payload.get("code", "invalid")) if isinstance(payload, dict) else "invalid"
            raise MarketPathError(
                "market_path_provider_code_" + code, retryable=code in {"50011", "50040"}
            )
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows or len(rows) > 300:
            raise MarketPathError("market_path_page_empty_or_oversized")
        body_hash = hashlib.sha256(captured.body).hexdigest()
        parsed = []
        timestamps = []
        for index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 9:
                raise MarketPathError("market_path_row_shape_invalid")
            try:
                stamp = int(row[0])
                if stamp >= after or stamp % 60_000:
                    raise MarketPathError("market_path_cursor_not_progressing")
                timestamps.append(stamp)
                opened = datetime.fromtimestamp(stamp / 1000, UTC)
                if not start <= opened < end:
                    continue
                if str(row[8]) != "1":
                    raise MarketPathError("market_path_bar_unclosed")
                parsed.append(
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
                        capture_ref=captured.capture_ref,
                        capture_hash=body_hash,
                        row_index=index,
                    )
                )
            except (ValueError, ArithmeticError, OverflowError, OSError) as exc:
                if isinstance(exc, MarketPathError):
                    raise
                raise MarketPathError("market_path_row_invalid") from exc
        oldest = min(timestamps)
        return MarketPathPage(
            scope=scope,
            candles=tuple(sorted(parsed, key=lambda item: item.open_at)),
            cursor=str(after),
            next_cursor=str(oldest) if oldest > int(start.timestamp() * 1000) else None,
            received_at=received,
            capture_ref=captured.capture_ref,
            capture_hash=body_hash,
        )

    async def path(
        self, *, scope: MarketPathScope, start_at: datetime, end_at: datetime
    ) -> MarketPricePath:
        start, end = self._window(scope, start_at, end_at)
        cursor, selected, duplicate_count = None, {}, 0
        count = 0
        for _page in range(self.max_pages):
            count += 1
            page = await self.candles(scope=scope, start_at=start, end_at=end, cursor=cursor)
            for candle in page.candles:
                old = selected.get(candle.open_at)
                if old:
                    if old.semantic_payload() != candle.semantic_payload():
                        raise MarketPathError("market_path_conflicting_duplicate")
                    duplicate_count += 1
                else:
                    selected[candle.open_at] = candle
            if page.next_cursor is None:
                break
            if cursor is not None and int(page.next_cursor) >= int(cursor):
                raise MarketPathError("market_path_cursor_not_progressing")
            cursor = page.next_cursor
        else:
            raise MarketPathError("market_path_page_budget_exhausted")
        expected = tuple(
            start + timedelta(minutes=i) for i in range(int((end - start).total_seconds() // 60))
        )
        missing = tuple(value for value in expected if value not in selected)
        candles = tuple(selected[key] for key in sorted(selected))
        semantic, manifest = path_hashes(scope, candles)
        return MarketPricePath(
            scope=scope,
            window_start_at=start,
            window_end_at=end,
            candles=candles,
            page_count=count,
            request_count=count,
            duplicate_count=duplicate_count,
            missing_open_at=missing,
            status="partial" if missing else "complete",
            semantic_hash=semantic,
            manifest_hash=manifest,
        )
