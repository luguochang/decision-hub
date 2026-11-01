from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import zstandard
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkNotification, DshSdkRun

_CANONICAL_REQUEST_MARKER = "Canonical request:\n"
_DEFAULT_MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_EVENTS = 100_000


@dataclass(frozen=True)
class DshSessionArchive:
    """A DSH JSONL archive bound to its authoritative Session header."""

    path: Path
    archive_sha256: str
    session_id: str
    events: tuple[Mapping[str, object], ...]
    notifications: tuple[DshSdkNotification, ...]
    created_at: datetime
    finished_at: datetime
    finish_reason: str | None
    final_response: str

    def as_sdk_run(self) -> DshSdkRun:
        return DshSdkRun(
            session_id=self.session_id,
            final_response=self.final_response,
            finish_reason=self.finish_reason,
            events=self.events,
            notifications=self.notifications,
            session_root=str(self.path.parent),
        )

    def canonical_request(self) -> ResearchSessionRequest:
        """Recover the one canonical request embedded by the DSH profile."""

        requests: dict[str, ResearchSessionRequest] = {}
        decoder = json.JSONDecoder()
        for event in self.events:
            if event.get("type") not in {"agent/inbox/spliced", "user/message"}:
                continue
            for text in _message_texts(event.get("data")):
                marker_at = text.rfind(_CANONICAL_REQUEST_MARKER)
                if marker_at < 0:
                    continue
                raw_request = text[marker_at + len(_CANONICAL_REQUEST_MARKER) :].lstrip()
                try:
                    payload, consumed = decoder.raw_decode(raw_request)
                except json.JSONDecodeError as exc:
                    raise AgentExecutionError(
                        "dsh_archive_request_invalid",
                        "DSH Session archive contains an invalid canonical request",
                    ) from exc
                if raw_request[consumed:].strip():
                    raise AgentExecutionError(
                        "dsh_archive_request_invalid",
                        "DSH Session archive canonical request has untrusted trailing content",
                    )
                try:
                    request = ResearchSessionRequest.model_validate(payload)
                except ValidationError as exc:
                    raise AgentExecutionError(
                        "dsh_archive_request_invalid",
                        "DSH Session archive canonical request violates the product contract",
                    ) from exc
                fingerprint = request.model_dump_json(exclude_none=False)
                requests[fingerprint] = request
        if len(requests) != 1:
            raise AgentExecutionError(
                "dsh_archive_request_missing" if not requests else "dsh_archive_request_conflict",
                "DSH Session archive must contain exactly one canonical request identity",
            )
        return next(iter(requests.values()))


def load_dsh_session_archive(
    path: Path,
    *,
    expected_session_id: str | None = None,
    max_uncompressed_bytes: int = _DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_events: int = _DEFAULT_MAX_EVENTS,
) -> DshSessionArchive:
    """Read one bounded JSONL/Zstandard archive and attest every event to its header.

    Raw Session JSONL events intentionally do not repeat ``sessionId``. The first
    record is the authoritative Session header, so this adapter validates it once
    and adds that identity to each normalized notification before any call/result
    attestation runs.
    """

    if max_uncompressed_bytes <= 0 or max_events <= 0:
        raise ValueError("archive bounds must be positive")
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise AgentExecutionError(
            "dsh_archive_unavailable", "DSH Session archive could not be read"
        ) from exc
    archive_sha256 = hashlib.sha256(encoded).hexdigest()
    raw = _decompress_bounded(path, encoded, max_uncompressed_bytes)
    events = _parse_events(raw, max_events=max_events)
    header = events[0]
    if header.get("type") != "session":
        raise AgentExecutionError(
            "dsh_archive_identity_invalid",
            "DSH Session archive must begin with its Session header",
        )
    session_id = _nonempty_string(header.get("id"))
    if session_id is None or (
        expected_session_id is not None and session_id != expected_session_id
    ):
        raise AgentExecutionError(
            "dsh_archive_identity_invalid",
            "DSH Session archive identity does not match the expected Session",
        )
    if any(event.get("type") == "session" for event in events[1:]):
        raise AgentExecutionError(
            "dsh_archive_identity_invalid",
            "DSH Session archive contains more than one Session header",
        )

    body = tuple(events[1:])
    notifications = tuple(
        DshSdkNotification(
            method="session.event",
            payload={"sessionId": session_id, "event": event},
        )
        for event in body
    )
    created_at = _milliseconds_at(header.get("createdAt"))
    if created_at is None:
        raise AgentExecutionError(
            "dsh_archive_identity_invalid", "DSH Session header has no valid creation time"
        )
    event_times = [item for event in body if (item := _event_time(event)) is not None]
    finished_at = max(event_times, default=created_at)
    return DshSessionArchive(
        path=path.resolve(),
        archive_sha256=archive_sha256,
        session_id=session_id,
        events=body,
        notifications=notifications,
        created_at=created_at,
        finished_at=finished_at,
        finish_reason=_finish_reason(body),
        final_response=_final_response(body),
    )


def _decompress_bounded(path: Path, encoded: bytes, limit: int) -> bytes:
    if path.name.endswith((".zstd", ".zst")):
        try:
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(encoded)) as reader:
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = reader.read(min(64 * 1024, limit + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        break
                raw = b"".join(chunks)
        except zstandard.ZstdError as exc:
            raise AgentExecutionError(
                "dsh_archive_invalid", "DSH Session archive is not valid Zstandard data"
            ) from exc
    else:
        raw = encoded
    if len(raw) > limit:
        raise AgentExecutionError(
            "dsh_archive_too_large", "DSH Session archive exceeds the readback byte limit"
        )
    return raw


def _parse_events(raw: bytes, *, max_events: int) -> tuple[Mapping[str, object], ...]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentExecutionError(
            "dsh_archive_invalid", "DSH Session archive is not UTF-8 JSONL"
        ) from exc
    events: list[Mapping[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(events) >= max_events:
            raise AgentExecutionError(
                "dsh_archive_too_large", "DSH Session archive exceeds the event limit"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentExecutionError(
                "dsh_archive_invalid",
                f"DSH Session archive line {line_number} is not valid JSON",
            ) from exc
        if not isinstance(value, Mapping) or _nonempty_string(value.get("type")) is None:
            raise AgentExecutionError(
                "dsh_archive_invalid",
                f"DSH Session archive line {line_number} is not a typed event",
            )
        events.append(cast(Mapping[str, object], value))
    if not events:
        raise AgentExecutionError("dsh_archive_invalid", "DSH Session archive is empty")
    return tuple(events)


def _message_texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    messages: list[Mapping[str, object]] = []
    inserted = value.get("inserted")
    if isinstance(inserted, Sequence) and not isinstance(inserted, (str, bytes)):
        messages.extend(item for item in inserted if isinstance(item, Mapping))
    message = value.get("message")
    if isinstance(message, Mapping):
        messages.append(message)
    if "content" in value:
        messages.append(value)
    texts: list[str] = []
    for item in messages:
        content = item.get("content")
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
    return tuple(texts)


def _final_response(events: Sequence[Mapping[str, object]]) -> str:
    for event in reversed(events):
        if event.get("type") != "assistant/message":
            continue
        texts = _message_texts(event.get("data"))
        if texts:
            return "\n".join(texts)
    return ""


def _finish_reason(events: Sequence[Mapping[str, object]]) -> str | None:
    for event in reversed(events):
        if event.get("type") != "turn/end":
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        reason = data.get("reason")
        if isinstance(reason, Mapping):
            kind = _nonempty_string(reason.get("kind"))
            if kind is not None:
                return kind
        direct = _nonempty_string(reason)
        if direct is not None:
            return direct
    return None


def _event_time(event: Mapping[str, object]) -> datetime | None:
    return _milliseconds_at(event.get("time"))


def _milliseconds_at(value: object) -> datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _nonempty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
