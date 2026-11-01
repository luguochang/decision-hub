"""Self-contained retained BTC inputs and narrow select-only reader fixtures."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate
from packages.kernel.decision_hub_kernel.application.daily_execution_binding import (
    DailyExecutionReceipt,
)
from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import SERVER_ROLES
from packages.kernel.decision_hub_kernel.persistence.db import ResearchEvidenceRecord
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    V2_DECISION_OUTPUT_SCHEMA_REF,
)


class BuilderInputs(TypedDict):
    evidence: EvidenceCandidate
    receipt_json: str
    raw_body: bytes
    coverage_body: bytes
    source_job: dict[str, Any]
    source_capture: dict[str, Any]
    partition_bodies: dict[str, bytes]
    as_of: datetime


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf8") if isinstance(value, str) else value).hexdigest()


def retained_inputs() -> BuilderInputs:
    path = (
        Path(__file__).parents[1] / "fixtures/historical_review_material_budget/btc-retained.json"
    )
    wire = json.loads(path.read_text(encoding="utf8"))
    return BuilderInputs(
        evidence=EvidenceCandidate.model_validate(wire["evidence"]),
        receipt_json=wire["receipt_json"],
        raw_body=wire["raw_body_utf8"].encode("utf8"),
        coverage_body=wire["coverage_body_utf8"].encode("utf8"),
        source_job=wire["source_job"],
        source_capture=wire["source_capture"],
        partition_bodies={
            key: body.encode("utf8") for key, body in wire["partition_bodies_utf8"].items()
        },
        as_of=datetime.fromisoformat(wire["as_of"].replace("Z", "+00:00")),
    )


def seal_receipt(inputs: BuilderInputs, receipt: dict[str, Any]) -> None:
    typed = DailyExecutionReceipt.model_validate(receipt)
    inputs["receipt_json"] = canonical(typed.model_dump(mode="json"))


def seal_coverage(inputs: BuilderInputs, coverage: dict[str, Any]) -> None:
    body = canonical(coverage).encode("utf8")
    inputs["coverage_body"] = body
    inputs["evidence"] = inputs["evidence"].model_copy(
        update={"structured_payload_ref": "coverage-manifest://sha256/" + sha(body)}
    )


def pad_raw_original(inputs: BuilderInputs, count: int) -> None:
    """Add valid JSON whitespace while updating every raw/capture receipt binding."""
    receipt = json.loads(inputs["receipt_json"])
    old_hash = receipt["body_hash"]
    body = inputs["raw_body"] + b" " * count
    inputs["raw_body"] = body
    receipt["body_hash"] = sha(body)
    receipt["body_ref"] = receipt["body_ref"].replace(old_hash, receipt["body_hash"])
    capture = inputs["source_capture"]
    capture.update(
        body_hash=receipt["body_hash"], body_ref=receipt["body_ref"], byte_count=len(body)
    )
    receipt["capture_row_hash"] = sha(canonical(capture))
    seal_receipt(inputs, receipt)


class ReaderRequest:
    """Only the request fields used by the representation reader and its hash."""

    run_id = "retained-representation-test"
    input_evidence: tuple[EvidenceCandidate, ...] = ()
    output_schema_ref = V2_DECISION_OUTPUT_SCHEMA_REF

    def model_dump(self, **_kwargs: object) -> dict[str, str]:
        return {"run_id": self.run_id, "output_schema_ref": self.output_schema_ref}


class EvidenceRows:
    def __init__(self, rows: list[ResearchEvidenceRecord]) -> None:
        self.rows = rows

    def all(self) -> list[ResearchEvidenceRecord]:
        return self.rows


class SelectOnlyEvidenceReader:
    """No Session/engine/app; only the already accepted row iteration seam."""

    def __init__(self, evidence: EvidenceCandidate, at: datetime) -> None:
        payload = evidence.model_dump()
        self.rows = [
            ResearchEvidenceRecord(
                **{
                    column.key: payload[column.key]
                    for column in ResearchEvidenceRecord.__table__.columns
                    if column.key in payload
                },
                run_id=ReaderRequest.run_id,
                accepted_at=at,
            )
        ]

    def scalars(self, _statement: object) -> EvidenceRows:
        return EvidenceRows(self.rows)


def reader_materials() -> dict[str, Any]:
    result: dict[str, Any] = {role: {"fixture_role": role} for role in SERVER_ROLES}
    result["context"] = {
        name + "_ref": None
        for name in ("instrument", "profile", "settings", "account", "position", "utility")
    }
    result.update(
        {
            name + "_ref": None
            for name in (
                "method",
                "cost_assumptions",
                "cost_policy",
                "candidate_target",
                "anchor_rule",
                "allowed_domain_policy",
                "invalidation_policy",
            )
        }
    )
    return result
