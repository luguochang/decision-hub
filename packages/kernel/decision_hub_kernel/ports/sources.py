from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.models import (
    EventWindowCapture,
    EventWindowSample,
    MarketQuote,
    NotificationMessage,
    SourceManifest,
    TextEnvelope,
)


class SourcePollResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    cursor_before: str | None = None
    cursor_after: str | None = None
    envelopes: tuple[TextEnvelope, ...] = ()
    fetched_at: datetime
    next_poll_at: datetime | None = None


class SourceConnector(Protocol):
    manifest: SourceManifest

    async def poll(self, cursor: str | None = None) -> SourcePollResult: ...


class SourceRegistryPort(Protocol):
    def manifests(self) -> list[SourceManifest]: ...

    def get(self, source_id: str) -> SourceConnector: ...


class MarketDataPort(Protocol):
    async def quote(
        self, instrument: str, *, observed_at: datetime | None = None
    ) -> MarketQuote: ...


class PriceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: MarketQuote
    exit: MarketQuote
    fees: float = Field(default=0, ge=0)
    slippage: float = Field(default=0, ge=0)
    quality_status: str = "observed"


class MarketWindowPort(Protocol):
    async def window(
        self, instrument: str, emitted_at: datetime, expires_at: datetime
    ) -> PriceWindow | None: ...


class EventWindowSamplerPort(Protocol):
    """Capture one due slot without exposing provider payloads to the Kernel."""

    async def capture(self, sample: EventWindowSample) -> EventWindowCapture | None: ...


class NotificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delivered: bool
    retryable: bool = False
    error_code: str | None = None
    provider_message_id: str | None = None


class NotificationPort(Protocol):
    channel: str

    async def deliver(self, message: NotificationMessage) -> NotificationResult: ...
