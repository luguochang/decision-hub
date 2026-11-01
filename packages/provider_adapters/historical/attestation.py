from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ROLES = frozenset({"US2Y", "DXY"})
_OPTIONAL_ROLES = frozenset({"ES"})


@dataclass(frozen=True)
class PortaraFileAttestation:
    """Owner-declared identity for one licensed Portara/CQG source file."""

    sha256: str
    source_id: str
    source_url: str


@dataclass(frozen=True)
class PortaraSourceAttestation:
    """A small, path-free attestation binding source identity to file bytes.

    The attestation is deliberately supplied by the owner rather than inferred
    from a filename.  It does not grant a license; it records the owner-side
    authorization reference that must accompany a strict hard-data build.
    """

    authorization_ref: str
    provider: str
    source_timezone: str
    files: Mapping[str, PortaraFileAttestation]

    @classmethod
    def from_path(cls, path: Path) -> PortaraSourceAttestation:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("portara_attestation_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("portara_attestation_invalid")
        allowed = {
            "schema_version",
            "authorization_ref",
            "provider",
            "source_timezone",
            "files",
        }
        if (
            set(payload) != allowed
            or payload.get("schema_version") != "portara-source-attestation.v1"
        ):
            raise ValueError("portara_attestation_invalid")
        authorization_ref = _required_string(payload.get("authorization_ref"))
        provider = _required_string(payload.get("provider"))
        source_timezone = _required_string(payload.get("source_timezone"))
        if not authorization_ref.startswith("owner:") or provider != "portara-cqg":
            raise ValueError("portara_attestation_identity_invalid")
        raw_files = payload.get("files")
        if not isinstance(raw_files, dict) or not _REQUIRED_ROLES <= set(raw_files) <= (
            _REQUIRED_ROLES | _OPTIONAL_ROLES
        ):
            raise ValueError("portara_attestation_files_invalid")
        files: dict[str, PortaraFileAttestation] = {}
        for role, raw in raw_files.items():
            if role not in _REQUIRED_ROLES | _OPTIONAL_ROLES or not isinstance(raw, dict):
                raise ValueError("portara_attestation_files_invalid")
            if set(raw) != {"sha256", "source_id", "source_url"}:
                raise ValueError("portara_attestation_files_invalid")
            sha256 = _required_string(raw.get("sha256"))
            source_id = _required_string(raw.get("source_id"))
            source_url = _required_string(raw.get("source_url"))
            if not _SHA256.fullmatch(sha256):
                raise ValueError("portara_attestation_hash_invalid")
            files[role] = PortaraFileAttestation(sha256, source_id, source_url)
        return cls(
            authorization_ref=authorization_ref,
            provider=provider,
            source_timezone=source_timezone,
            files=files,
        )

    def verify_file(self, role: str, path: Path) -> PortaraFileAttestation:
        """Verify bytes and return the declared identity for one role."""

        declared = self.files.get(role)
        if declared is None:
            raise ValueError(f"portara_attestation_role_missing:{role}")
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(f"portara_attestation_file_unavailable:{role}") from exc
        if actual != declared.sha256:
            raise ValueError(f"portara_attestation_hash_mismatch:{role}")
        return declared


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("portara_attestation_value_invalid")
    return value.strip()
