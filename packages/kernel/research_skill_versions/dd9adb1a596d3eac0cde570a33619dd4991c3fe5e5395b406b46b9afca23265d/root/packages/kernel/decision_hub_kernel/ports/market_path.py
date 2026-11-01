"""Versioned local contracts for auditable market paths; independent of research runtimes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market_path_timezone_required")
    return value.astimezone(UTC)


def decimal_text(value: Decimal) -> str:
    if not value.is_finite() or abs(value.as_tuple().exponent) > 18:
        raise ValueError("market_path_decimal_invalid")
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


def canonical_hash(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def timestamp_text(value: datetime) -> str:
    return aware_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketPathScope(FrozenContract):
    schema_version: Literal["market-path-scope.v1"] = "market-path-scope.v1"
    scope_id: str = Field(min_length=1)
    asset_class: str = "crypto"
    instrument_kind: Literal["spot", "perpetual_swap"] = "spot"
    venue: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    quote_currency: str = Field(min_length=1)
    price_type: Literal["trade", "mark", "index"] = "trade"
    price_unit: str = Field(min_length=1)
    volume_unit: str = Field(min_length=1)
    timezone: Literal["UTC"] = "UTC"
    calendar_ref: str = "crypto-24x7.v1"
    interval_seconds: Literal[60] = 60

    @model_validator(mode="after")
    def validate_scope(self) -> MarketPathScope:
        expected_id = (
            f"{self.asset_class}:{self.instrument_kind}:{self.venue}:"
            f"{self.instrument}:{self.price_type}:1m"
        )
        if self.scope_id != expected_id:
            raise ValueError("market_path_scope_id_invalid")
        if self.asset_class == "crypto" and self.instrument_kind == "spot":
            parts = self.instrument.split("-")
            if len(parts) != 2:
                raise ValueError("market_path_spot_instrument_invalid")
            base, quote = parts
            if (self.quote_currency, self.price_unit, self.volume_unit) != (
                quote,
                f"{quote}/{base}",
                base,
            ):
                raise ValueError("market_path_scope_units_invalid")
        return self


def spot_scope(instrument: str, *, venue: str = "okx") -> MarketPathScope:
    if instrument not in {"BTC-USDT", "ETH-USDT"}:
        raise ValueError("market_path_spot_scope_not_enabled")
    base, quote = instrument.split("-")
    return MarketPathScope(
        scope_id=f"crypto:spot:{venue}:{instrument}:trade:1m",
        venue=venue,
        instrument=instrument,
        quote_currency=quote,
        price_unit=f"{quote}/{base}",
        volume_unit=base,
    )


class MarketCandle(FrozenContract):
    scope_id: str
    venue: str
    instrument: str
    price_type: Literal["trade", "mark", "index"] = "trade"
    interval_seconds: Literal[60] = 60
    open_at: datetime
    close_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_base: Decimal | None = None
    volume_quote: Decimal | None = None
    closed: Literal[True] = True
    received_at: datetime
    accepted_at: datetime | None = None
    capture_ref: str | None = None
    capture_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    row_index: int | None = Field(default=None, ge=0)

    @field_validator("open_at", "close_at", "received_at", "accepted_at")
    @classmethod
    def clocks(cls, value: datetime | None) -> datetime | None:
        return aware_utc(value) if value is not None else None

    @field_validator("open", "high", "low", "close", "volume_base", "volume_quote")
    @classmethod
    def numbers(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(decimal_text(value))

    @model_validator(mode="after")
    def validate_bar(self) -> MarketCandle:
        if self.open_at.timestamp() % 60 or (self.close_at - self.open_at).total_seconds() != 60:
            raise ValueError("market_path_bar_not_minute_aligned")
        if self.close_at > self.received_at:
            raise ValueError("market_path_bar_not_closed_at_receipt")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("market_path_price_not_positive")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("market_path_ohlc_invalid")
        if any(item is not None and item < 0 for item in (self.volume_base, self.volume_quote)):
            raise ValueError("market_path_volume_negative")
        return self

    @property
    def known_at(self) -> datetime:
        return max(self.received_at, self.accepted_at or self.received_at)

    def semantic_payload(self) -> dict:
        return {
            "scope_id": self.scope_id,
            "venue": self.venue,
            "instrument": self.instrument,
            "price_type": self.price_type,
            "interval_seconds": self.interval_seconds,
            "open_at": timestamp_text(self.open_at),
            "close_at": timestamp_text(self.close_at),
            **{
                name: decimal_text(getattr(self, name)) for name in ("open", "high", "low", "close")
            },
            "volume_base": decimal_text(self.volume_base) if self.volume_base is not None else None,
            "volume_quote": decimal_text(self.volume_quote)
            if self.volume_quote is not None
            else None,
        }


def path_hashes(scope: MarketPathScope, candles: tuple[MarketCandle, ...]) -> tuple[str, str]:
    ordered = sorted(candles, key=lambda item: item.open_at)
    semantic = canonical_hash(
        {
            "scope": scope.model_dump(mode="json"),
            "bars": [item.semantic_payload() for item in ordered],
        }
    )
    provenance = canonical_hash(
        {
            "semantic_hash": semantic,
            "receipts": [
                {
                    "open_at": timestamp_text(item.open_at),
                    "received_at": timestamp_text(item.received_at),
                    "accepted_at": timestamp_text(item.accepted_at)
                    if item.accepted_at is not None
                    else None,
                    "capture_ref": item.capture_ref,
                    "capture_hash": item.capture_hash,
                    "row_index": item.row_index,
                }
                for item in ordered
            ],
        }
    )
    return semantic, provenance


def scope_matches(scope: MarketPathScope, candle: MarketCandle) -> bool:
    return all(
        getattr(scope, name) == getattr(candle, name)
        for name in (
            "scope_id",
            "venue",
            "instrument",
            "price_type",
            "interval_seconds",
        )
    )


class MarketPathPage(FrozenContract):
    schema_version: Literal["market-path-page.v1"] = "market-path-page.v1"
    scope: MarketPathScope
    candles: tuple[MarketCandle, ...]
    cursor: str | None
    next_cursor: str | None
    received_at: datetime
    capture_ref: str | None = None
    capture_hash: str


class MarketPricePath(FrozenContract):
    schema_version: Literal["market-price-path.v1"] = "market-price-path.v1"
    scope: MarketPathScope
    window_start_at: datetime
    window_end_at: datetime
    candles: tuple[MarketCandle, ...]
    page_count: int
    request_count: int
    duplicate_count: int = 0
    missing_open_at: tuple[datetime, ...] = ()
    status: Literal["complete", "partial"]
    semantic_hash: str
    manifest_hash: str


class MarketPathPort(Protocol):
    async def candles(
        self,
        *,
        scope: MarketPathScope,
        start_at: datetime,
        end_at: datetime,
        cursor: str | None = None,
    ) -> MarketPathPage: ...
