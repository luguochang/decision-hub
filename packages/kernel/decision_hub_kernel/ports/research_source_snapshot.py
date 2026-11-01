"""Immutable source identities for one publication read. Never a permission."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

SOURCE_SNAPSHOT_READ_ABI = "publication-source-snapshot-read.v2"


class SourceSnapshotFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relative_ref: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(strict=True, ge=0, le=2 * 1024 * 1024)


class PublicationSourceSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["publication-source-snapshot.v1"] = "publication-source-snapshot.v1"
    reader_abi: Literal["publication-source-snapshot-read.v2"] = SOURCE_SNAPSHOT_READ_ABI
    files: tuple[SourceSnapshotFileV1, ...]
    source_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exact_source_set(self):
        import re
        from pathlib import PurePosixPath

        refs = tuple(file.relative_ref for file in self.files)
        if (
            not refs
            or len(refs) > 2048
            or refs != tuple(sorted(set(refs)))
            or sum(file.bytes for file in self.files) > 32 * 1024 * 1024
        ):
            raise ValueError("publication_source_snapshot_bounds_or_identity_invalid")
        for ref in refs:
            path = PurePosixPath(ref)
            if (
                path.as_posix() != ref
                or path.is_absolute()
                or any(part in {".", ".."} for part in path.parts)
                or re.search(r'[\\\x00-\x1f<>:"|?*]', ref)
            ):
                raise ValueError("publication_source_snapshot_path_invalid")
        body = [[file.relative_ref, file.sha256, file.bytes] for file in self.files]
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        if hashlib.sha256(raw).hexdigest() != self.source_set_sha256:
            raise ValueError("publication_source_snapshot_set_hash_changed")
        return self


class PublicationSourceLease(Protocol):
    """Server issued, one thread/task/native read; the DTO alone grants nothing."""

    snapshot: PublicationSourceSnapshotV1

    def require_read(self, session: object, run_id: str) -> None: ...

    def require_reader_source(self, path: object) -> None: ...
