from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import CoverageManifest, PartitionManifest

_PARTITION_PREFIX = "historical-partition://sha256/"
_COVERAGE_PREFIX = "coverage-manifest://sha256/"
_PARTITION_REF = re.compile(r"^historical-partition://sha256/([a-f0-9]{64})$")
_COVERAGE_REF = re.compile(r"^coverage-manifest://sha256/([a-f0-9]{64})$")


class HistoricalDataArchive:
    """Immutable monthly partitions and atomically published coverage manifests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.partitions = root / "partitions"
        self.manifests = root / "manifests"
        self.partitions.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)

    def write_partition(
        self,
        *,
        dataset_id: str,
        partition_key: str,
        source_id: str,
        rows: list[dict[str, object]],
        unit: str,
        frequency: str,
        instrument: str,
        venue: str | None,
        contract_type: str | None,
        created_at: datetime,
    ) -> PartitionManifest:
        ordered = sorted(rows, key=lambda row: str(row["timestamp"]))
        if not ordered:
            raise ValueError("historical_partition_empty")
        timestamps = [str(row["timestamp"]) for row in ordered]
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("historical_partition_timestamp_duplicate")
        payload = {
            "schema_version": "historical-partition.v1",
            "dataset_id": dataset_id,
            "partition_key": partition_key,
            "source_id": source_id,
            "unit": unit,
            "frequency": frequency,
            "instrument": instrument,
            "venue": venue,
            "contract_type": contract_type,
            "rows": ordered,
        }
        serialized = _canonical(payload)
        payload_hash = hashlib.sha256(serialized).hexdigest()
        payload_ref = f"{_PARTITION_PREFIX}{payload_hash}"
        _write_once(self.partitions / f"{payload_hash}.json", serialized)
        return PartitionManifest.model_validate(
            {
                "schema_version": "historical-partition-manifest.v1",
                "dataset_id": dataset_id,
                "partition_key": partition_key,
                "source_id": source_id,
                "payload_ref": payload_ref,
                "payload_hash": payload_hash,
                "row_count": len(ordered),
                "actual_start_at": _timestamp(timestamps[0]),
                "actual_end_at": _timestamp(timestamps[-1]),
                "unit": unit,
                "frequency": frequency,
                "instrument": instrument,
                "venue": venue,
                "contract_type": contract_type,
                "created_at": created_at,
            }
        )

    def load_partition(
        self, payload_ref: str
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        payload = self._load(payload_ref, self.partitions, _PARTITION_REF)
        rows = payload.get("rows")
        if payload.get("schema_version") != "historical-partition.v1" or not isinstance(
            rows, list
        ):
            raise ValueError("historical_partition_invalid")
        return payload, [dict(row) for row in rows if isinstance(row, dict)]

    def write_coverage(self, manifest: CoverageManifest) -> str:
        serialized = _canonical(manifest.model_dump(mode="json"))
        payload_hash = hashlib.sha256(serialized).hexdigest()
        _write_once(self.manifests / f"{payload_hash}.json", serialized)
        return f"{_COVERAGE_PREFIX}{payload_hash}"

    def load_coverage(self, manifest_ref: str) -> CoverageManifest:
        payload = self._load(manifest_ref, self.manifests, _COVERAGE_REF)
        try:
            return CoverageManifest.model_validate(payload)
        except ValueError as exc:
            raise ValueError("coverage_manifest_invalid") from exc

    @staticmethod
    def _load(ref: str, root: Path, pattern: re.Pattern[str]) -> dict[str, object]:
        match = pattern.fullmatch(ref)
        if match is None:
            raise ValueError("historical_asset_ref_invalid")
        digest = match.group(1)
        try:
            serialized = (root / f"{digest}.json").read_bytes()
        except OSError as exc:
            raise ValueError("historical_asset_missing") from exc
        if hashlib.sha256(serialized).hexdigest() != digest:
            raise ValueError("historical_asset_hash_mismatch")
        try:
            payload = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise ValueError("historical_asset_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("historical_asset_invalid")
        return payload


def _write_once(path: Path, serialized: bytes) -> None:
    if path.exists():
        if path.read_bytes() != serialized:
            raise ValueError("historical_asset_hash_conflict")
        return
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("historical_timestamp_naive")
    return parsed.astimezone(UTC)
