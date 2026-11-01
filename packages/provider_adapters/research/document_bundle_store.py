from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


_REF = re.compile(r"^docbundle://sha256/([a-f0-9]{64})/([A-Za-z0-9_.-]+)$")


class DocumentBundleStore:
    """Content-addressed source and derived-document assets.

    Database rows store identity and state; this store owns immutable bytes.
    A write is atomic and a read always verifies the requested content hash.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_original(self, data: bytes, *, suffix: str) -> tuple[str, str]:
        if not data:
            raise ValueError("document_file_empty")
        normalized_suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        if normalized_suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md", ".html"}:
            normalized_suffix = ".bin"
        digest = hashlib.sha256(data).hexdigest()
        return self._write_asset(digest, f"original{normalized_suffix}", data)

    def write_text(self, text: str, *, asset_hash: str | None = None, name: str = "normalized.md") -> str:
        encoded = text.encode("utf-8")
        digest = asset_hash or hashlib.sha256(encoded).hexdigest()
        ref, _ = self._write_asset(digest, name, encoded)
        return ref

    def write_json(self, payload: object, *, asset_hash: str | None = None, name: str) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = asset_hash or hashlib.sha256(encoded).hexdigest()
        ref, _ = self._write_asset(digest, name, encoded)
        return ref

    def read(self, content_ref: str) -> bytes:
        match = _REF.fullmatch(content_ref)
        if match is None:
            raise ValueError("document_bundle_ref_invalid")
        digest, name = match.groups()
        path = self.root / digest / name
        try:
            path.relative_to(self.root)
            payload = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError("document_bundle_asset_missing") from exc
        if hashlib.sha256(payload).hexdigest() != digest and name.startswith("original"):
            raise ValueError("document_bundle_hash_mismatch")
        return payload

    def path_for(self, content_ref: str) -> Path:
        match = _REF.fullmatch(content_ref)
        if match is None:
            raise ValueError("document_bundle_ref_invalid")
        digest, name = match.groups()
        path = (self.root / digest / name).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("document_bundle_path_invalid") from exc
        if not path.is_file():
            raise ValueError("document_bundle_asset_missing")
        return path

    def _write_asset(self, digest: str, name: str, data: bytes) -> tuple[str, str]:
        directory = self.root / digest
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        if not path.exists():
            fd, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return f"docbundle://sha256/{digest}/{name}", digest
