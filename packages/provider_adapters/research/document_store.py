from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .documents import FetchedDocument

DOCUMENT_REF_PREFIX = "document://sha256/"
_DOCUMENT_REF = re.compile(r"^document://sha256/([a-f0-9]{64})$")
_MAX_DOCUMENT_CHARACTERS = 2_000_000


@dataclass(frozen=True)
class StoredDocument:
    content_ref: str
    source_url: str
    text: str
    source_content_hash: str
    parser_ref: str
    published_at: datetime | None
    observed_at: datetime
    received_at: datetime


class ContentAddressedDocumentStore:
    """Immutable local archive for source-policy-approved original text.

    References are opaque to callers. Reads verify both the canonical asset
    hash and the original body hash, so a citation can never silently point to
    mutated local content.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, document: FetchedDocument, *, parser_ref: str) -> StoredDocument:
        if not parser_ref.strip():
            raise ValueError("research_document_parser_ref_required")
        if not document.text or len(document.text) > _MAX_DOCUMENT_CHARACTERS:
            raise ValueError("research_document_content_size_invalid")
        source_content_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
        payload = {
            "schema_version": "research-document-asset.v1",
            "source_url": document.source_url,
            "text": document.text,
            "source_content_hash": source_content_hash,
            "parser_ref": parser_ref,
            "published_at": _iso(document.published_at),
            "observed_at": _iso(document.observed_at),
            "received_at": _iso(document.received_at),
        }
        serialized = _canonical(payload)
        asset_hash = hashlib.sha256(serialized).hexdigest()
        path = self.root / f"{asset_hash}.json"
        if not path.exists():
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{asset_hash}.", suffix=".tmp", dir=self.root
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return self.load(f"{DOCUMENT_REF_PREFIX}{asset_hash}")

    def load(
        self,
        content_ref: str,
        *,
        expected_source_content_hash: str | None = None,
    ) -> StoredDocument:
        match = _DOCUMENT_REF.fullmatch(content_ref)
        if match is None:
            raise ValueError("research_document_content_ref_invalid")
        asset_hash = match.group(1)
        path = self.root / f"{asset_hash}.json"
        try:
            serialized = path.read_bytes()
        except OSError as exc:
            raise ValueError("research_document_content_missing") from exc
        if hashlib.sha256(serialized).hexdigest() != asset_hash:
            raise ValueError("research_document_asset_hash_mismatch")
        try:
            payload = json.loads(serialized)
            if not isinstance(payload, dict) or payload.get("schema_version") != (
                "research-document-asset.v1"
            ):
                raise ValueError
            text = str(payload["text"])
            source_content_hash = str(payload["source_content_hash"])
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != source_content_hash:
                raise ValueError("research_document_content_hash_mismatch")
            if (
                expected_source_content_hash is not None
                and source_content_hash != expected_source_content_hash
            ):
                raise ValueError("research_document_content_hash_mismatch")
            return StoredDocument(
                content_ref=content_ref,
                source_url=str(payload["source_url"]),
                text=text,
                source_content_hash=source_content_hash,
                parser_ref=str(payload["parser_ref"]),
                published_at=_datetime(payload.get("published_at")),
                observed_at=_required_datetime(payload.get("observed_at")),
                received_at=_required_datetime(payload.get("received_at")),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("research_document_asset_invalid") from exc


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("research_document_timestamp_naive")
    return value.astimezone(UTC).isoformat()


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("research_document_asset_invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("research_document_asset_invalid")
    return parsed.astimezone(UTC)


def _required_datetime(value: object) -> datetime:
    parsed = _datetime(value)
    if parsed is None:
        raise ValueError("research_document_asset_invalid")
    return parsed
