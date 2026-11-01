"""Explicit durable current-path read contract; no execution or source-read grant."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from .market_path import MarketCandle, MarketPathScope


class MarketOriginalBytesReadPort(Protocol):
    def read(self, ref: str) -> bytes:
        """Return freshly read, authenticated original bytes for the exact ref."""
        ...


class MarketPathReadPortV2(Protocol):
    @property
    def version(self) -> Literal["market-path-read.v2"]:
        """Implementation version identity, not permission or financial qualification."""
        ...

    def load(
        self,
        scope: MarketPathScope,
        *,
        start_at: datetime,
        end_at: datetime,
        as_of: datetime,
    ) -> tuple[MarketCandle, ...]: ...
