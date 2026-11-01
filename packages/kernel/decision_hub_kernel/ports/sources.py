from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.generated.notification_envelope import (
    NotificationEnvelopeV2,
    NotificationEnvelopeV3,
)
from packages.contracts_py.decision_hub_contracts.models import (
    EventWindowCapture,
    EventWindowSample,
    MarketQuote,
    NotificationMessage,
    SourceManifest,
    TextEnvelope,
)

NotificationEnvelope = NotificationEnvelopeV2 | NotificationEnvelopeV3


class SourceContentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    http_status: int | None = None
    parser_version: str
    content_hash: str | None = None
    parse_status: Literal["valid", "invalid", "not_attempted"] = "not_attempted"
    parsed_count: int | None = Field(default=None, ge=0)
    upcoming_count: int | None = Field(default=None, ge=0)
    first_upcoming_date: str | None = None
    last_upcoming_date: str | None = None
    coverage_status: Literal["available", "no_upcoming", "unknown"] = "unknown"


class SourcePollFailure(RuntimeError):
    def __init__(self, error_code: str, snapshot: SourceContentSnapshot) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.snapshot = snapshot


class SourcePollResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    cursor_before: str | None = None
    cursor_after: str | None = None
    envelopes: tuple[TextEnvelope, ...] = ()
    fetched_at: datetime
    next_poll_at: datetime | None = None
    content_snapshot: SourceContentSnapshot | None = None


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


def _notification_payload(message: NotificationEnvelope) -> str:
    if message.created_at.tzinfo is None or message.created_at.utcoffset() is None:
        raise ValueError("notification_created_at_timezone_required")
    body = message.model_dump(mode="json", exclude={"payload_hash"})
    body["created_at"] = message.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_notification_envelope(
    *,
    subject_kind: Literal["report", "risk_alert", "action_revision", "event_opportunity"],
    subject_id: str,
    channel: str,
    dedupe_key: str,
    subject: str,
    body: str,
    created_at: datetime,
) -> NotificationEnvelope:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("notification_created_at_timezone_required")
    model = (
        NotificationEnvelopeV3 if subject_kind == "event_opportunity" else NotificationEnvelopeV2
    )
    candidate = model(
        schema_version="notification-envelope.v3"
        if subject_kind == "event_opportunity"
        else "notification-envelope.v2",
        subject_kind=subject_kind,
        subject_id=subject_id,
        channel=channel,
        dedupe_key=dedupe_key,
        subject=subject,
        body=body,
        created_at=created_at,
        payload_hash="0" * 64,
    )
    digest = hashlib.sha256(_notification_payload(candidate).encode("utf-8")).hexdigest()
    return candidate.model_copy(
        update={"created_at": created_at.astimezone(UTC), "payload_hash": digest}
    )


def decode_notification_envelope(payload: str) -> NotificationEnvelope:
    try:
        schema = json.loads(payload).get("schema_version")
        if schema == "notification-envelope.v2":
            return NotificationEnvelopeV2.model_validate_json(payload)
        if schema == "notification-envelope.v3":
            return NotificationEnvelopeV3.model_validate_json(payload)
    except (ValueError, AttributeError):
        pass
    raise ValueError("notification_payload_invalid")


def verify_notification_envelope(message: NotificationEnvelope) -> NotificationEnvelope:
    try:
        checked = decode_notification_envelope(message.model_dump_json())
    except ValueError:
        # Operational failures must not persist bodies from validation errors.
        raise ValueError("notification_payload_invalid") from None
    digest = hashlib.sha256(_notification_payload(checked).encode("utf-8")).hexdigest()
    if digest != checked.payload_hash:
        raise ValueError("notification_payload_hash_invalid")
    return checked.model_copy(update={"created_at": checked.created_at.astimezone(UTC)})


class NotificationPort(Protocol):
    channel: str

    async def deliver(
        self, message: NotificationMessage | NotificationEnvelope
    ) -> NotificationResult: ...
