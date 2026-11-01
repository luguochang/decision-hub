"""Bounded readonly originals for an already admitted daily-history Evidence.

These bytes support source inspection, never financial or model authority.
The session reader authenticates the existing execution witness before calling
the pure projection; no acquisition, writes, clock renewal or directory creation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate, ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.daily_execution_binding import (
    DailyExecutionReceipt,
    read_daily_execution_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DataJobRecord,
    RawCaptureRecord,
    ResearchFactRecord,
)
from packages.provider_adapters.historical.gate_daily import normalize_gate_daily_payload
from packages.provider_adapters.historical.research import loaded_daily_implementation
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from sqlalchemy import select
from sqlalchemy.orm import Session

KEY = "historical_originals_v1"
SCHEMA = "historical-daily-review-originals.v1"
MAX_MATERIAL_BYTES = 192 * 1024
_COVERAGE = re.compile(r"coverage-manifest://sha256/([a-f0-9]{64})")
_PARTITION = re.compile(r"historical-partition://sha256/([a-f0-9]{64})")


class HistoricalDailyReviewMaterial(TypedDict):
    schema_version: Literal["historical-daily-review-originals.v1"]
    dataset_id: str
    parent_evidence_ref: dict[str, str]
    evidence_received_at: str
    source_job: dict[str, Any]
    source_capture: dict[str, Any]
    raw_body_utf8: str
    raw_body_sha256: str
    coverage_manifest_ref: str
    coverage_manifest_utf8: str
    partition_originals: list[dict[str, str]]
    execution_receipt_json: str
    source_window: dict[str, Any]
    selection_window: dict[str, Any]
    selected_rows: list[dict[str, object]]


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _time(value: str | datetime) -> datetime:
    result = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    if result.tzinfo is None:
        raise ValueError("historical_review_timezone_required")
    return result.astimezone(UTC)


def build_daily_review_material(
    *,
    evidence: EvidenceCandidate,
    receipt_json: str,
    raw_body: bytes,
    coverage_body: bytes,
    source_job: dict[str, Any],
    source_capture: dict[str, Any],
    partition_bodies: dict[str, bytes],
    as_of: datetime,
) -> HistoricalDailyReviewMaterial:
    """Project exact authenticated inputs, rechecking byte/selection boundaries."""
    receipt = DailyExecutionReceipt.model_validate_json(receipt_json)
    query = ResearchCapabilityQuery.model_validate_json(receipt.adapter_query_json)
    scopes = {
        "crypto.eth_usdt.ohlcv_1d": ("ETH-USDT", "usdt_and_eth"),
        "crypto.btc_usdt.ohlcv_1d": ("BTC-USDT", "usdt_and_btc"),
    }
    if len(query.symbols) != 1 or query.symbols[0] not in scopes:
        raise ValueError("historical_review_dataset_unsupported")
    expected_instrument, expected_unit = scopes[query.symbols[0]]
    ref = evidence.structured_payload_ref or ""
    match = _COVERAGE.fullmatch(ref)
    if match is None or _sha(coverage_body) != match[1]:
        raise ValueError("historical_review_coverage_hash_mismatch")
    coverage = json.loads(coverage_body)
    datasets = [row for row in coverage["datasets"] if row["dataset_id"] == query.symbols[0]]
    if len(datasets) != 1:
        raise ValueError("historical_review_dataset_mismatch")
    dataset = datasets[0]
    if (
        _canonical(receipt.model_dump(mode="json")) != receipt_json
        or evidence.evidence_id not in json.loads(receipt.strategy_receipt_json)["evidence_refs"]
        or evidence.tool_call_id != receipt.request_id
        or evidence.research_session_id != receipt.research_session_id
        or receipt.request_id != query.request_id
        or receipt.research_session_id != query.research_session_id
        or _sha(receipt.adapter_query_json) != receipt.adapter_query_hash
        or receipt.loaded_identity != loaded_daily_implementation().identity()
        or _sha(raw_body) != receipt.body_hash
        or source_capture["body_hash"] != receipt.body_hash
        or source_capture["body_ref"] != receipt.body_ref
        or source_capture["capture_id"] != receipt.capture_id
        or source_capture["byte_count"] != len(raw_body)
        or _sha(_canonical(source_capture)) != receipt.capture_row_hash
        or source_job["job_id"] != receipt.job_id
        or source_job["request_hash"] != receipt.job_request_hash
        or _sha(
            _canonical(
                {
                    "dataset_ids": json.loads(source_job["dataset_ids_json"]),
                    "requested_by": source_job["requested_by"],
                    "window_start_at": source_job["window_start_at"],
                    "window_end_at": source_job["window_end_at"],
                }
            )
        )
        != receipt.job_request_hash
        or coverage["job_id"] != receipt.job_id
        or dataset["raw_capture_ids"] != [receipt.capture_id]
        or dataset["status"] != "complete"
        or dataset["gaps"]
        or dataset["source_id"] != evidence.source_id
        or dataset["frequency"] != "1d"
        or dataset["instrument"] != expected_instrument
        or dataset["unit"] != expected_unit
        or sorted(dataset["partition_refs"]) != sorted(partition_bodies)
        or not set(partition_bodies) <= set(json.loads(source_job["partition_refs_json"]))
    ):
        raise ValueError("historical_review_original_binding_mismatch")
    start, end = _time(source_job["window_start_at"]), _time(source_job["window_end_at"])
    if (
        _time(coverage["requested_start_at"]) != start
        or _time(coverage["requested_end_at"]) != end
        or not start <= receipt.query_start_at < receipt.query_end_at <= end
        or not _time(source_capture["started_at"])
        <= _time(source_capture["completed_at"])
        <= _time(source_capture["recorded_at"])
        <= evidence.received_at
        <= _time(as_of)
        or _time(source_capture["completed_at"]) != receipt.closed_at
        or not _time(source_capture["recorded_at"])
        <= receipt.computed_started_at
        <= receipt.computed_completed_at
        <= evidence.received_at
        or _time(coverage["generated_at"]) > evidence.received_at
        or any(
            _time(source_job[key]) > evidence.received_at
            for key in ("created_at", "updated_at", "finished_at")
        )
    ):
        raise ValueError("historical_review_original_time_mismatch")
    original_rows = normalize_gate_daily_payload(
        json.loads(raw_body),
        start=start,
        end=end,
        closed_at=receipt.closed_at,
    )
    partition_rows: list[dict[str, object]] = []
    partition_originals: list[dict[str, str]] = []
    for partition_ref, body in sorted(partition_bodies.items()):
        part_match = _PARTITION.fullmatch(partition_ref)
        if part_match is None or _sha(body) != part_match[1]:
            raise ValueError("historical_review_partition_hash_mismatch")
        part = json.loads(body)
        if any(
            part[key] != dataset[key]
            for key in (
                "dataset_id",
                "source_id",
                "unit",
                "frequency",
                "instrument",
                "venue",
                "contract_type",
            )
        ):
            raise ValueError("historical_review_partition_scope_mismatch")
        partition_rows.extend(part["rows"])
        partition_originals.append({"ref": partition_ref, "utf8": body.decode("utf-8")})
    partition_rows.sort(key=lambda row: str(row["timestamp"]))
    if (
        _canonical(partition_rows) != _canonical(original_rows)
        or dataset["row_count"] != len(original_rows)
        or not original_rows
        or _time(dataset["actual_start_at"]) != _time(str(original_rows[0]["timestamp"]))
        or _time(dataset["actual_end_at"]) != _time(str(original_rows[-1]["timestamp"]))
    ):
        raise ValueError("historical_review_partition_rows_mismatch")
    selected = [
        row
        for row in original_rows
        if receipt.query_start_at <= _time(str(row["timestamp"])) < receipt.query_end_at
    ]
    summary = loaded_daily_implementation().summary(query.symbols[0], selected)
    if (
        _sha(_canonical(selected)) != receipt.normalized_rows_hash
        or _canonical(summary) != receipt.summary_json
    ):
        raise ValueError("historical_review_selection_mismatch")
    excerpt = json.loads(evidence.excerpt)
    if excerpt["summary"] != {key: summary[key] for key in sorted(set(query.fields) or summary)}:
        raise ValueError("historical_review_summary_mismatch")
    material = HistoricalDailyReviewMaterial(
        schema_version=SCHEMA,
        dataset_id=query.symbols[0],
        parent_evidence_ref={"ref": evidence.evidence_id, "hash": evidence.content_hash},
        evidence_received_at=evidence.received_at.isoformat(),
        source_job=source_job,
        source_capture=source_capture,
        raw_body_utf8=raw_body.decode("utf-8"),
        raw_body_sha256=receipt.body_hash,
        coverage_manifest_ref=ref,
        coverage_manifest_utf8=coverage_body.decode("utf-8"),
        partition_originals=partition_originals,
        execution_receipt_json=receipt_json,
        source_window={
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "row_count": len(original_rows),
        },
        selection_window={
            "start_at": receipt.query_start_at.isoformat(),
            "end_at": receipt.query_end_at.isoformat(),
            "row_count": len(selected),
        },
        selected_rows=selected,
    )
    if len(_canonical(material).encode("utf-8")) > MAX_MATERIAL_BYTES:
        raise ValueError("historical_review_material_too_large")
    return material


def validate_daily_review_material(
    value: dict[str, Any], evidence: EvidenceCandidate, *, as_of: datetime
) -> None:
    rebuilt = build_daily_review_material(
        evidence=evidence,
        receipt_json=value["execution_receipt_json"],
        raw_body=value["raw_body_utf8"].encode("utf-8"),
        coverage_body=value["coverage_manifest_utf8"].encode("utf-8"),
        source_job=value["source_job"],
        source_capture=value["source_capture"],
        partition_bodies={
            row["ref"]: row["utf8"].encode("utf-8") for row in value["partition_originals"]
        },
        as_of=as_of,
    )
    if _canonical(value) != _canonical(rebuilt):
        raise ValueError("historical_review_material_changed")


def _record(row: Any) -> dict[str, Any]:
    return {
        column.key: (
            (
                value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            ).isoformat()
            if isinstance(value, datetime)
            else value
        )
        for column in row.__table__.columns
        for value in (getattr(row, column.key),)
    }


def _content(root: Path, ref: str, pattern: re.Pattern[str]) -> bytes:
    match = pattern.fullmatch(ref)
    if match is None:
        raise ValueError("historical_review_original_ref_invalid")
    body = (root / (match[1] + ".json")).read_bytes()
    if _sha(body) != match[1]:
        raise ValueError("historical_review_original_hash_mismatch")
    return body


def read_daily_review_material_in_session(
    session: Session,
    *,
    run_id: str,
    evidence: EvidenceCandidate,
    as_of: datetime,
) -> HistoricalDailyReviewMaterial | None:
    """Called inside an already authenticated perpetual request/material scope."""
    if (
        evidence.source_id != "gateio-public"
        or _COVERAGE.fullmatch(evidence.structured_payload_ref or "") is None
    ):
        return None
    facts = session.scalars(
        select(ResearchFactRecord).where(
            ResearchFactRecord.run_id == run_id,
            ResearchFactRecord.evidence_id == evidence.evidence_id,
            ResearchFactRecord.quality == "accepted",
            ResearchFactRecord.accepted_at <= as_of,
        )
    ).all()
    receipts = {
        json.loads(row.attributes_json).get("daily_execution_receipt_json") for row in facts
    }
    receipts.discard(None)
    if not receipts:
        return None
    if len(receipts) != 1:
        raise ValueError("historical_review_execution_receipt_conflict")
    receipt_json = next(iter(receipts))
    if not isinstance(receipt_json, str):
        raise TypeError("historical_review_execution_receipt_invalid")
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (  # pyright: ignore[reportPrivateUsage]
        ADMISSION_ROLE,
        _read,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
    )

    admission: dict[str, Any] = _read(session, run_id, ADMISSION_ROLE)[0]
    archive_root = Path(admission["archive_root"])
    archive = RawBodyArchive(archive_root)
    try:
        frozen = read_daily_execution_in_session(
            session,
            run_id=run_id,
            evidence_id=evidence.evidence_id,
            receipt_json=receipt_json,
            archive=archive,
            implementation=loaded_daily_implementation(),
            publication_at=as_of,
            assessed_at=as_of,
        )
        if frozen is None:
            return None
        receipt = frozen.receipt
        capture = session.get(RawCaptureRecord, receipt.capture_id, populate_existing=True)
        job = session.get(DataJobRecord, receipt.job_id, populate_existing=True)
        if capture is None or job is None:
            return None
        historical_root = archive_root.parent / "historical"
        coverage_body = _content(
            historical_root / "manifests", evidence.structured_payload_ref or "", _COVERAGE
        )
        coverage = json.loads(coverage_body)
        dataset = next(
            row
            for row in coverage["datasets"]
            if row["dataset_id"] == json.loads(receipt.adapter_query_json)["symbols"][0]
        )
        # This optional material proves a complete retained source window only.
        # A legacy partial coverage summary remains readable and unpromoted;
        # it does not become a new fatal requirement for fresh preparation.
        if dataset["status"] != "complete" or dataset["gaps"]:
            return None
        return build_daily_review_material(
            evidence=evidence,
            receipt_json=receipt_json,
            raw_body=archive.read(receipt.body_ref),
            coverage_body=coverage_body,
            source_job=_record(job),
            source_capture=_record(capture),
            partition_bodies={
                ref: _content(historical_root / "partitions", ref, _PARTITION)
                for ref in dataset["partition_refs"]
            },
            as_of=as_of,
        )
    except FileNotFoundError:
        # Fresh preparation may disclose the existing gap. An old selected view
        # is still rejected by exact catalog reconstruction, never downgraded.
        return None
    except ValueError as exc:
        if str(exc) == "historical_review_material_too_large":
            return None
        raise
