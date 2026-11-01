from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from packages.provider_adapters.historical import (
    PortaraCrossAssetEventWindowLoader,
    PortaraMacroEventWindowLoader,
    PortaraSourceAttestation,
)
from tools.research_evaluation.build_core_replay_dataset import _verify_portara_attestation


def _write_attestation(path: Path, source: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "portara-source-attestation.v1",
                "authorization_ref": "owner:test",
                "provider": "portara-cqg",
                "source_timezone": "America/Chicago",
                "files": {
                    "US2Y": {
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "source_id": "tullett-prebon-usy02y-via-portara",
                        "source_url": "https://portaracqg.com/fixed-income/int/usy02y",
                    },
                    "DXY": {
                        "sha256": "b" * 64,
                        "source_id": "iceus-dx-via-portara",
                        "source_url": "https://portaracqg.com/futures/int/dxa",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_attestation_binds_owner_identity_and_exact_file_bytes(tmp_path: Path) -> None:
    source = tmp_path / "USY02Y.txt"
    source.write_bytes(b"20241107 1255 4.20\n")
    attestation_path = tmp_path / "attestation.json"
    _write_attestation(attestation_path, source)

    attestation = PortaraSourceAttestation.from_path(attestation_path)

    assert attestation.authorization_ref == "owner:test"
    assert attestation.verify_file("US2Y", source).source_id == (
        "tullett-prebon-usy02y-via-portara"
    )


def test_attestation_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "USY02Y.txt"
    source.write_bytes(b"changed")
    attestation_path = tmp_path / "attestation.json"
    _write_attestation(attestation_path, source)
    # Keep the declaration readable while making the declared hash differ.
    payload = json.loads(attestation_path.read_text(encoding="utf-8"))
    payload["files"]["US2Y"]["sha256"] = "a" * 64
    attestation_path.write_text(json.dumps(payload), encoding="utf-8")

    attestation = PortaraSourceAttestation.from_path(attestation_path)
    with pytest.raises(ValueError, match="portara_attestation_hash_mismatch:US2Y"):
        attestation.verify_file("US2Y", source)


def test_attestation_rejects_non_owner_or_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "attestation.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "portara-source-attestation.v1",
                "authorization_ref": "vendor:unverified",
                "provider": "portara-cqg",
                "source_timezone": "America/Chicago",
                "files": {},
                "extra": "reject",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="portara_attestation_invalid"):
        PortaraSourceAttestation.from_path(path)


def test_builder_rejects_an_unbound_optional_es_declaration(tmp_path: Path) -> None:
    source = tmp_path / "USY02Y.txt"
    source.write_bytes(b"20241107 1255 4.20\n")
    dxy = tmp_path / "DXA.txt"
    dxy.write_bytes(b"20241107 1255 104.0\n")
    es = tmp_path / "EP.txt"
    es.write_bytes(b"20241107 1255 5800.0\n")
    payload = {
        "schema_version": "portara-source-attestation.v1",
        "authorization_ref": "owner:test",
        "provider": "portara-cqg",
        "source_timezone": "America/Chicago",
        "files": {
            "US2Y": {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_id": "tullett-prebon-usy02y-via-portara",
                "source_url": PortaraMacroEventWindowLoader.rate_source_url,
            },
            "DXY": {
                "sha256": hashlib.sha256(dxy.read_bytes()).hexdigest(),
                "source_id": "iceus-dx-via-portara",
                "source_url": PortaraMacroEventWindowLoader.usd_source_url,
            },
            "ES": {
                "sha256": hashlib.sha256(es.read_bytes()).hexdigest(),
                "source_id": "cme-emini-sp500-es-via-portara",
                "source_url": PortaraCrossAssetEventWindowLoader.source_url,
            },
        },
    }
    path = tmp_path / "attestation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    attestation = PortaraSourceAttestation.from_path(path)
    with pytest.raises(ValueError, match="portara_attestation_roles_mismatch"):
        _verify_portara_attestation(
            attestation,
            us2y_path=source,
            dxy_path=dxy,
            es_path=None,
            source_timezone="America/Chicago",
        )
