"""Freeze one report opinion and its value sources in the same SQLite writer.

These receipts certify a saved evaluation attachment, not Provider truth,
transaction permission, a directional label or learning/promotion eligibility.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from pydantic import ValidationError
from sqlalchemy import Boolean, DateTime, Float, Integer, String, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    FeedbackView,
    HorizonOutcomeView,
    ResearchSessionResult,
    RevisionFeedbackEvaluation,
    RevisionFeedbackEvaluationBasis,
)
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.research_value_basis import (
    saved_coverage_basis,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import assess_evidence_sufficiency
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Base,
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    EvaluationRecord,
    EventWatchRecord,
    FeedbackRecord,
    ForecastRecord,
    ObservationRecord,
    OutcomeRecord,
    ReportRevisionRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    ResearchRunScopeRecord,
    ResearchValueEvaluationRecord,
    RevisionFeedbackEvaluationRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StudyRecord,
    as_utc,
)

SCHEMA = "research-revision-feedback-evaluation.v1"
BASIS_SCHEMA = "research-revision-feedback-evaluation-basis.v1"
RECIPE = "research-value.revision-feedback.v1"
SourceReason = Literal[
    "source_missing", "source_invalid", "source_identity_mismatch", "source_hash_mismatch",
    "source_conflict", "source_not_research", "source_not_terminal",
]
MODELS = (
    FeedbackRecord, ReportRevisionRecord, RunRecord, ArtifactRecord, ResearchResultRecord,
    RunRoleOutputRecord, SnapshotRecord, ForecastRecord, OutcomeRecord, EvaluationRecord,
    ResearchEvidenceRecord, ResearchFactRecord, EventWatchRecord, ObservationRecord,
    DshSessionLinkRecord, DshSessionPromptRecord, StudyRecord, ResearchRunScopeRecord,
)
TABLES = {model.__tablename__: model.__table__ for model in MODELS}
RawScalar = str | int | float | bytes | None
RawRow = dict[str, RawScalar]


class _Column(TypedDict):
    name: str
    declared_type: str
    not_null: int
    default_sql: str | None
    primary_key_position: int
    hidden: int


class _FrozenTable(TypedDict):
    source_table: str
    columns: list[str]
    column_metadata: list[_Column]
    primary_key_columns: list[str]
    rows: list[list[dict[str, str]]]


class RevisionFeedbackEvaluationError(ValueError):
    """A specific receipt conflict; programming and storage exceptions stay distinct."""


class _SourceUnavailable(Exception):
    def __init__(self, reason: SourceReason) -> None:
        self.reason = reason
        super().__init__(reason)


def _required_assessed_at(value: datetime | None) -> datetime:
    normalized = as_utc(value)
    if normalized is None:
        raise _SourceUnavailable("source_invalid")
    return normalized


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _cell(value: object) -> dict[str, str]:
    """Fixed SQLite storage encoding; do not stringify arbitrary Python objects."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        # Hex preserves the exact SQLite REAL without JSON NaN/coercion. A
        # nonfinite value is retained here and rejected by source validation.
        return {"type": "real", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, bytes):
        return {"type": "blob", "value": base64.b64encode(value).decode("ascii")}
    raise TypeError("unsupported_sqlite_storage_scalar")


def _decode_cell(value: dict[str, str]) -> object:
    kind = value["type"]
    if kind == "null" and set(value) == {"type"}:
        return None
    if set(value) != {"type", "value"}:
        raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_invalid")
    raw = value["value"]
    if kind == "text":
        return raw
    if kind == "integer":
        integer = int(raw)
        if str(integer) == raw:
            return integer
    elif kind == "real":
        real = float.fromhex(raw)
        if real.hex() == raw:
            return real
    elif kind == "blob":
        blob = base64.b64decode(raw, validate=True)
        if base64.b64encode(blob).decode("ascii") == raw:
            return blob
    raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_invalid")


def _raw_rows(session: Session, table_name: str, where: str,
              parameters: tuple[object, ...]) -> list[RawRow]:
    # Names and predicates are only selected by this implementation's registry;
    # callers cannot supply SQL/table/column names to the public service.
    if table_name not in TABLES:
        raise ValueError("revision_feedback_source_table_unknown")
    cursor = session.connection().exec_driver_sql(
        f'SELECT * FROM "{table_name}" WHERE {where}', parameters
    )
    columns = list(cursor.keys())
    rows = cursor.mappings().all()
    return sorted((dict(row) for row in rows), key=lambda row: _canonical(
        [_cell(row[name]) for name in columns]
    ))


def _layout(session: Session, table_name: str) -> list[_Column]:
    if table_name not in TABLES:
        raise ValueError("revision_feedback_source_table_unknown")
    # Per-session physical metadata includes unmapped columns and empty sets.
    # Do not cache a schema from another database or hide SELECT * columns.
    metadata = session.connection().exec_driver_sql(
        f'PRAGMA table_xinfo("{table_name}")'
    ).mappings().all()
    if not metadata:
        raise RuntimeError("revision_feedback_source_table_missing")
    # xinfo also includes generated columns. Verify their exact SELECT * order
    # instead of accidentally dropping them with ORM metadata or table_info.
    physical_columns = list(session.connection().exec_driver_sql(
        f'SELECT * FROM "{table_name}" WHERE 0'
    ).keys())
    visible = [item for item in metadata if item["name"] in physical_columns]
    if [item["name"] for item in visible] != physical_columns:
        raise RuntimeError("revision_feedback_source_columns_inconsistent")
    return [{"name": item["name"], "declared_type": item["type"],
             "not_null": item["notnull"], "default_sql": item["dflt_value"],
             "primary_key_position": item["pk"], "hidden": item["hidden"]} for item in visible]


def _freeze_table(session: Session, table_name: str,
                  rows: list[RawRow]) -> _FrozenTable:
    metadata = _layout(session, table_name)
    columns = [item["name"] for item in metadata]
    return {
        "source_table": table_name,
        "columns": columns,
        "column_metadata": metadata,
        "primary_key_columns": [item["name"] for item in sorted(
            (item for item in metadata if item["primary_key_position"]),
            key=lambda item: item["primary_key_position"],
        )],
        "rows": [[_cell(row[name]) for name in columns] for row in rows],
    }


def _witnesses(frozen: list[_FrozenTable]) -> list[dict[str, object]]:
    witnesses: list[dict[str, object]] = []
    for entry in frozen:
        table_name = entry["source_table"]
        columns = entry["columns"]
        for cells in entry["rows"]:
            decoded = {name: _decode_cell(cell) for name, cell in zip(columns, cells, strict=True)}
            primary_key = []
            for name in entry["primary_key_columns"]:
                value = decoded[name]
                # An invalid raw identifier is preserved in basis, but cannot
                # be advertised as a verified string identity (never str(blob)).
                if type(value) is int:
                    value = str(value)
                if not isinstance(value, str) or not 1 <= len(value) <= 256:
                    break
                primary_key.append(value)
            if len(primary_key) != len(entry["primary_key_columns"]) or not primary_key:
                continue
            schema = decoded.get("schema_version")
            if not isinstance(schema, str) or not 1 <= len(schema) <= 128:
                schema = None
            witnesses.append({
                "source_table": table_name,
                "primary_key": primary_key,
                "row_hash_kind": "sqlite-complete-row.v1",
                "row_hash": _hash({"source_table": table_name, "columns": columns, "cells": cells}),
                "source_schema": schema,
            })
    return witnesses


def _capture(session: Session, feedback: FeedbackRecord) -> dict[str, list[RawRow]]:
    rows: dict[str, list[RawRow]] = {name: [] for name in TABLES}
    rows["workbench_feedback"] = _raw_rows(
        session, "workbench_feedback", '"feedback_id" = ?', (feedback.feedback_id,)
    )
    rows["report_revisions"] = _raw_rows(
        session, "report_revisions", '"revision_id" = ?', (feedback.target_id,)
    )
    if not rows["report_revisions"]:
        return rows
    revision = rows["report_revisions"][0]
    run_id, artifact_id = revision["source_run_id"], revision["artifact_id"]
    for name, key, identity in (
        ("runs", "run_id", run_id), ("artifacts", "artifact_id", artifact_id),
        ("research_results", "run_id", run_id), ("research_evidence", "run_id", run_id),
        ("research_facts", "run_id", run_id), ("forecasts", "artifact_id", artifact_id),
        ("dsh_session_links", "run_id", run_id),
    ):
        rows[name] = _raw_rows(session, name, f'"{key}" = ?', (identity,))
    rows["run_role_outputs"] = _raw_rows(
        session, "run_role_outputs",
        '"run_id" = ? AND "role" IN (?, ?, ?)',
        (run_id, "research_task_request", "research_request_pack", "research_compiled_policy"),
    )
    rows["dsh_session_prompts"] = _raw_rows(
        session, "dsh_session_prompts", '"run_id" = ? AND "generation" = 1', (run_id,)
    )
    for name in ("outcomes", "evaluations"):
        rows[name] = _raw_rows(session, name,
            '"forecast_id" IN (SELECT "forecast_id" FROM "forecasts" WHERE "artifact_id" = ?)',
            (artifact_id,))
    if rows["runs"]:
        run = rows["runs"][0]
        rows["studies"] = _raw_rows(
            session, "studies", '"study_id" = ?', (run["study_id"],)
        )
        rows["research_run_scopes"] = _raw_rows(
            session, "research_run_scopes", '"run_id" = ?', (run_id,)
        )
        for name in ("event_watches", "observations"):
            rows[name] = _raw_rows(session, name, '"event_id" = ?', (run["event_id"],))
        snapshots = list({run[key] for key in ("snapshot_id", "decision_snapshot_id")
                          if run[key] is not None})
        if snapshots:
            markers = ",".join("?" for _ in snapshots)
            rows["snapshots"] = _raw_rows(session, "snapshots",
                                         f'"snapshot_id" IN ({markers})', tuple(snapshots))
    return rows


def _validate_storage(rows: dict[str, list[RawRow]]) -> None:
    for table_name, table_rows in rows.items():
        for row in table_rows:
            for column in TABLES[table_name].columns:
                value = row[column.name]
                if value is None:
                    if not column.nullable:
                        raise _SourceUnavailable("source_invalid")
                    continue
                if isinstance(column.type, Boolean):
                    valid = type(value) is int and value in (0, 1)
                elif isinstance(column.type, Integer):
                    valid = type(value) is int
                elif isinstance(column.type, Float):
                    valid = isinstance(value, (int, float)) and math.isfinite(value)
                elif isinstance(column.type, DateTime):
                    valid = isinstance(value, str)
                    if isinstance(value, str):
                        try:
                            datetime.fromisoformat(value)
                        except ValueError:
                            valid = False
                else:
                    valid = isinstance(value, str)
                    if isinstance(value, str) and isinstance(column.type, String):
                        if column.type.length is not None:
                            valid = len(value) <= column.type.length
                        if column.primary_key and not value:
                            valid = False
                if not valid:
                    raise _SourceUnavailable("source_invalid")


def _source_text(raw: object) -> str:
    if not isinstance(raw, str):
        raise _SourceUnavailable("source_invalid")
    return raw


def _source_json(raw: object) -> object:
    def invalid_constant(token: str) -> object:
        raise _SourceUnavailable("source_invalid")

    def object_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in items:
            if key in result:
                raise _SourceUnavailable("source_invalid")
            result[key] = value
        return result

    try:
        return json.loads(_source_text(raw), parse_constant=invalid_constant,
                          object_pairs_hook=object_pairs)
    except json.JSONDecodeError as exc:
        raise _SourceUnavailable("source_invalid") from exc


def _one[RecordT: Base](
    session: Session, model: type[RecordT], identity: object,
) -> RecordT | None:
    return session.get(model, identity, populate_existing=True)


@dataclass(frozen=True)
class _Inputs:
    run: RunRecord
    artifact: ArtifactRecord
    result: ResearchSessionResult | None
    coverage: CoverageAssessment | None
    coverage_basis: str
    coverage_basis_ref: str | None
    coverage_cutoff_at: datetime | None
    evidence: list[ResearchEvidenceRecord]
    facts: list[ResearchFactRecord]
    forecasts: list[ForecastRecord]
    watch: EventWatchRecord | None


def _validated_inputs(session: Session, feedback: FeedbackRecord,
                      rows: dict[str, list[RawRow]]) -> _Inputs:
    _validate_storage(rows)
    if not all(rows[name] for name in ("report_revisions", "runs", "artifacts")):
        raise _SourceUnavailable("source_missing")
    revision = rows["report_revisions"][0]
    run_data, artifact_data = rows["runs"][0], rows["artifacts"][0]
    if (
        revision["source_run_id"] != run_data["run_id"]
        or revision["artifact_id"] != artifact_data["artifact_id"]
        or run_data["artifact_id"] != artifact_data["artifact_id"]
        or artifact_data["run_id"] != run_data["run_id"]
        or artifact_data["event_id"] != run_data["event_id"]
        or revision["strategy_version"] != run_data["strategy_version"]
        or revision["publication_status"] != artifact_data["gate_status"]
    ):
        raise _SourceUnavailable("source_identity_mismatch")
    if run_data["strategy_version"] != "research.v1":
        raise _SourceUnavailable("source_not_research")
    if run_data["status"] not in {"completed", "degraded", "failed", "cancelled"}:
        raise _SourceUnavailable("source_not_terminal")
    if (artifact_data["gate_status"] not in {"publish", "degraded", "reject", "research_only"}
        or not _nonnegative_or_null(run_data["latency_ms"])
        or not _nonnegative_or_null(run_data["cost_usd"])):
        raise _SourceUnavailable("source_invalid")
    artifact_payload = _source_json(artifact_data["payload_json"])
    if not isinstance(artifact_payload, dict) or (
        "citations" in artifact_payload and
        (not isinstance(artifact_payload["citations"], list)
         or not all(isinstance(item, str) for item in artifact_payload["citations"]))
    ):
        raise _SourceUnavailable("source_invalid")
    if not isinstance(_source_json(revision["change_json"]), dict):
        raise _SourceUnavailable("source_invalid")
    run = _one(session, RunRecord, run_data["run_id"])
    artifact = _one(session, ArtifactRecord, artifact_data["artifact_id"])
    if run is None or artifact is None:
        raise _SourceUnavailable("source_missing")
    assessed_at = _required_assessed_at(run.finished_at or run.updated_at)
    result = None
    if rows["research_results"]:
        result_data = rows["research_results"][0]
        body = _source_json(result_data["payload_json"])
        if _hash(body) != result_data["payload_hash"]:
            raise _SourceUnavailable("source_hash_mismatch")
        try:
            result = ResearchSessionResult.model_validate_json(_canonical(body))
        except ValidationError as exc:
            raise _SourceUnavailable("source_invalid") from exc
        if result.schema_version != result_data["schema_version"]:
            raise _SourceUnavailable("source_identity_mismatch")
    for row in rows["run_role_outputs"]:
        body = _source_json(row["payload_json"])
        actual_hash = hashlib.sha256(_source_text(row["payload_json"]).encode("utf-8")).hexdigest()
        if actual_hash != row["payload_hash"]:
            raise _SourceUnavailable("source_hash_mismatch")
        if not isinstance(body, dict) or body.get("run_id") != run.run_id:
            raise _SourceUnavailable("source_identity_mismatch")
    snapshot_ids = {row["snapshot_id"] for row in rows["snapshots"]}
    if any(run_data[key] is not None and run_data[key] not in snapshot_ids
           for key in ("snapshot_id", "decision_snapshot_id")):
        raise _SourceUnavailable("source_missing")
    for row in rows["snapshots"]:
        if row["event_id"] != run.event_id or row["run_id"] not in {None, run.run_id}:
            raise _SourceUnavailable("source_identity_mismatch")
        if not isinstance(_source_json(row["evidence_json"]), list):
            raise _SourceUnavailable("source_invalid")
    evidence_ids = {row["evidence_id"] for row in rows["research_evidence"]}
    for row in rows["research_facts"]:
        if row["evidence_id"] not in evidence_ids:
            raise _SourceUnavailable("source_identity_mismatch")
        _source_json(row["value_json"])
        if not isinstance(_source_json(row["attributes_json"]), dict):
            raise _SourceUnavailable("source_invalid")
    for name in ("outcomes", "evaluations"):
        identities = [row["forecast_id"] for row in rows[name]]
        if len(identities) != len(set(identities)):
            raise _SourceUnavailable("source_conflict")
    if len(rows["event_watches"]) > 1:
        raise _SourceUnavailable("source_conflict")
    if rows["event_watches"] and rows["event_watches"][0]["baseline_status"] not in {
        "not_applicable", "pending", "ready", "unavailable"
    }:
        raise _SourceUnavailable("source_invalid")
    try:
        ResearchEvidenceService.list_run_evidence_in_session(session, run.run_id)
        ResearchFactStore.list_run_facts_in_session(session, run.run_id)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise _SourceUnavailable("source_invalid") from exc
    evidence = list(session.scalars(select(ResearchEvidenceRecord).where(
        ResearchEvidenceRecord.run_id == run.run_id).execution_options(populate_existing=True)))
    facts = list(session.scalars(select(ResearchFactRecord).where(
        ResearchFactRecord.run_id == run.run_id).execution_options(populate_existing=True)))
    forecasts = list(session.scalars(select(ForecastRecord).where(
        ForecastRecord.artifact_id == artifact.artifact_id).order_by(
            ForecastRecord.expires_at, ForecastRecord.forecast_id
        ).execution_options(populate_existing=True)))
    for forecast in forecasts:
        if forecast.horizon not in {"30m", "24h", "72h"} or forecast.direction not in {
            "long", "short", "neutral", "no_trade"
        }:
            raise _SourceUnavailable("source_invalid")
        if not 0 <= forecast.probability <= 1:
            raise _SourceUnavailable("source_invalid")
        outcome_rows = [item for item in rows["outcomes"]
                        if item["forecast_id"] == forecast.forecast_id]
        evaluation_rows = [item for item in rows["evaluations"]
                           if item["forecast_id"] == forecast.forecast_id]
        outcome = outcome_rows[0] if outcome_rows else None
        evaluation = evaluation_rows[0] if evaluation_rows else None
        try:
            HorizonOutcomeView.model_validate({
                "forecast_id": forecast.forecast_id, "horizon": forecast.horizon,
                "direction": forecast.direction,
                "quality_status": outcome["quality_status"] if outcome else "unavailable",
                "return_pct": outcome["return_pct"] if outcome else None,
                "net_return_pct": evaluation["net_return_pct"] if evaluation else None,
                "brier_score": evaluation["brier_score"] if evaluation else None,
                "direction_correct": bool(evaluation["direction_correct"]) if evaluation else None,
                "mfe_pct": None, "mae_pct": None, "unavailable_reason": "outcome_pending",
                "observed_at": (as_utc(datetime.fromisoformat(_source_text(outcome["observed_at"])))
                                if outcome else None),
            })
        except ValidationError as exc:
            raise _SourceUnavailable("source_invalid") from exc
    watch = (_one(session, EventWatchRecord, rows["event_watches"][0]["watch_id"])
             if rows["event_watches"] else None)
    if result is not None:
        return _Inputs(run, artifact, result, result.final_coverage, "saved_result",
                       _source_text(rows["research_results"][0]["payload_hash"]), None,
                       evidence, facts, forecasts, watch)
    basis = saved_coverage_basis(session, run, assessed_at)
    if basis.status == "invalid":
        raise _SourceUnavailable("source_invalid")
    if basis.request is None:
        raise _SourceUnavailable("source_missing")
    cutoff = basis.cutoff_at
    if cutoff is None:
        raise _SourceUnavailable("source_invalid")
    coverage = assess_evidence_sufficiency(
        basis.request.evidence_requirements,
        ResearchEvidenceService.list_run_evidence_in_session(session, run.run_id),
        cutoff_at=cutoff, assessed_at=assessed_at,
        facts=ResearchFactStore.list_run_facts_in_session(session, run.run_id),
    )
    return _Inputs(run, artifact, None, coverage, basis.status, basis.reference, cutoff,
                   evidence, facts, forecasts, watch)


def _feedback_view(row: FeedbackRecord) -> FeedbackView:
    return FeedbackView.model_validate({
        "feedback_id": row.feedback_id, "request_id": row.request_id,
        "target_type": row.target_type, "target_id": row.target_id,
        "created_by": row.created_by, "verdict": row.verdict, "notes": row.notes,
        "created_at": as_utc(row.created_at),
    })


def _nonnegative_or_null(value: object) -> bool:
    return value is None or (isinstance(value, (int, float)) and value >= 0)


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and 1 <= len(value) <= 128 else None


def _physical_writer(session: Session) -> None:
    connection = session.connection()
    native = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection):
        raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_sqlite_required")
    if not native.in_transaction:
        raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_writer_required")


def _receipt_hash(payload: dict[str, object]) -> str:
    semantic = json.loads(_canonical(payload))
    semantic["binding"].pop("binding_hash")
    return _hash(semantic)


def append_revision_feedback_evaluation_in_session(
    session: Session, feedback_row: FeedbackRecord, recorded_at: datetime,
) -> RevisionFeedbackEvaluation:
    """Only a fresh report opinion; legacy replay never invokes this writer."""
    _physical_writer(session)
    if feedback_row.target_type != "report_revision":
        raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_target_invalid")
    if recorded_at.utcoffset() is None:
        raise ValueError("revision_feedback_evaluation_recorded_at_must_be_aware")
    if session.scalar(select(RevisionFeedbackEvaluationRecord.binding_seq).where(
        RevisionFeedbackEvaluationRecord.feedback_id == feedback_row.feedback_id
    )) is not None:
        raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_binding_exists")
    raw_rows = _capture(session, feedback_row)
    inputs = None
    reason = None
    try:
        inputs = _validated_inputs(session, feedback_row, raw_rows)
    except _SourceUnavailable as exc:
        reason = exc.reason
    frozen = [_freeze_table(session, name, raw_rows[name]) for name in sorted(TABLES)]
    witnesses = _witnesses(frozen)
    basis = {"recipe_id": RECIPE, "storage_encoding": "sqlite-typed-cells.v1",
             "owner_id": feedback_row.created_by, "revision_id": feedback_row.target_id,
             "request_id": feedback_row.request_id, "tables": frozen,
             "validation_issues": [reason] if reason else []}
    basis_json = _canonical(basis)
    basis_hash = hashlib.sha256(basis_json.encode("utf-8")).hexdigest()
    version = f"{RECIPE}:{basis_hash}"
    value = None
    if inputs is not None:
        value = ResearchValueEvaluationService.append_revision_in_session(
            session, run=inputs.run, artifact=inputs.artifact, result=inputs.result,
            feedback=feedback_row, coverage=inputs.coverage, coverage_basis=inputs.coverage_basis,
            coverage_basis_ref=inputs.coverage_basis_ref,
            coverage_cutoff_at=inputs.coverage_cutoff_at,
            evidence_rows=inputs.evidence, fact_rows=inputs.facts, forecasts=inputs.forecasts,
            watch=inputs.watch, evaluation_version=version, evaluated_at=recorded_at,
        )
    original = _feedback_view(feedback_row).model_dump(mode="json")
    revision_rows = raw_rows["report_revisions"]
    source_run_id = _identifier(revision_rows[0]["source_run_id"]) if revision_rows else None
    artifact_id = _identifier(revision_rows[0]["artifact_id"]) if revision_rows else None
    value_json = value.model_dump(mode="json") if value is not None else None
    value_hash = _hash(value_json) if value_json is not None else None
    binding = RevisionFeedbackEvaluationRecord(
        binding_id="feedback_eval_" + _hash([feedback_row.feedback_id, basis_hash])[:32],
        feedback_id=feedback_row.feedback_id, request_id=feedback_row.request_id,
        owner_id=feedback_row.created_by, revision_id=feedback_row.target_id,
        run_id=source_run_id, artifact_id=artifact_id, schema_version=SCHEMA,
        status="available" if value else "unavailable", reason_code=reason,
        evaluation_id=value.evaluation_id if value else None, evaluation_version=version,
        value_payload_hash=value_hash, basis_json=basis_json, basis_hash=basis_hash,
        receipt_json="{}", receipt_hash="", created_at=recorded_at,
    )
    session.add(binding)
    # Only this uncommitted new row receives its SQLite ordinal, then the
    # complete receipt. Any failure rolls back feedback/value/binding together.
    session.flush()
    payload = {
        "schema_version": SCHEMA, "owner_id": binding.owner_id, "revision_id": binding.revision_id,
        "feedback": original, "status": binding.status, "reason": reason,
        "confirmation_basis": "stored_feedback_evaluation_binding",
        "binding": {
            "binding_id": binding.binding_id, "recorded_order": binding.binding_seq,
            "source_run_id": binding.run_id, "artifact_id": binding.artifact_id,
            "evaluation_version": version,
            "basis_ref": f"revision-feedback-basis:{binding.binding_id}",
            "basis_hash": basis_hash, "feedback_payload_hash": _hash(original),
            "value_payload_hash": value_hash, "binding_hash": "0" * 64,
            "recorded_at": recorded_at.isoformat(), "validation_issues": basis["validation_issues"],
            "source_witnesses": witnesses,
        },
        "value": value_json,
    }
    # Hash the canonical DTO representation (including its UTC representation),
    # never the pre-validation +00:00 form that Pydantic can serialize as Z.
    normalized = RevisionFeedbackEvaluation.model_validate_json(_canonical(payload)).model_dump(
        mode="json"
    )
    normalized["binding"]["binding_hash"] = _receipt_hash(normalized)
    typed = RevisionFeedbackEvaluation.model_validate_json(_canonical(normalized))
    binding.receipt_json = _canonical(typed.model_dump(mode="json"))
    binding.receipt_hash = normalized["binding"]["binding_hash"]
    session.flush()
    return typed


def _owned_feedback(session: Session, revision_id: str, request_id: str,
                    owner_id: str) -> FeedbackRecord:
    row = session.scalar(select(FeedbackRecord).where(
        FeedbackRecord.request_id == request_id).execution_options(populate_existing=True))
    if row is None:
        raise KeyError("revision_feedback_not_found")
    if (row.target_type, row.target_id, row.created_by) != (
        "report_revision", revision_id, owner_id,
    ):
        raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_request_conflict")
    return row


def _legacy(feedback: FeedbackRecord) -> RevisionFeedbackEvaluation:
    return RevisionFeedbackEvaluation.model_validate_json(_canonical({
        "schema_version": SCHEMA, "owner_id": feedback.created_by,
        "revision_id": feedback.target_id,
        "feedback": _feedback_view(feedback).model_dump(mode="json"),
        "status": "unavailable", "reason": "legacy_feedback_not_bound",
        "confirmation_basis": "legacy_feedback_receipt_only", "binding": None, "value": None,
    }))


def _verify(session: Session, feedback: FeedbackRecord,
            row: RevisionFeedbackEvaluationRecord) -> RevisionFeedbackEvaluation:
    invalid = "revision_feedback_evaluation_invalid"
    try:
        typed = RevisionFeedbackEvaluation.model_validate_json(row.receipt_json)
        payload = typed.model_dump(mode="json")
        basis = json.loads(row.basis_json)
        if (not isinstance(basis, dict) or set(basis) != {
            "recipe_id", "storage_encoding", "owner_id", "revision_id", "request_id",
            "tables", "validation_issues",
        } or _canonical(basis) != row.basis_json or _hash(basis) != row.basis_hash
            or _canonical(payload) != row.receipt_json):
            raise RevisionFeedbackEvaluationError(invalid)
        binding = payload["binding"]
        if binding is None or (
            row.schema_version != SCHEMA or row.feedback_id != feedback.feedback_id
            or row.request_id != feedback.request_id or row.owner_id != feedback.created_by
            or row.revision_id != feedback.target_id or payload["owner_id"] != row.owner_id
            or payload["revision_id"] != row.revision_id or payload["status"] != row.status
            or payload["reason"] != row.reason_code
            or payload["confirmation_basis"] != "stored_feedback_evaluation_binding"
            or binding["binding_id"] != row.binding_id
            or binding["recorded_order"] != row.binding_seq
            or row.binding_id != (
                "feedback_eval_" + _hash([feedback.feedback_id, row.basis_hash])[:32]
            )
            or binding["source_run_id"] != row.run_id or binding["artifact_id"] != row.artifact_id
            or binding["evaluation_version"] != row.evaluation_version
            or row.evaluation_version != f"{RECIPE}:{row.basis_hash}"
            or binding["basis_hash"] != row.basis_hash
            or binding["basis_ref"] != f"revision-feedback-basis:{row.binding_id}"
            or typed.binding is None
            or typed.binding.recorded_at != as_utc(row.created_at)
            or payload["feedback"] != _feedback_view(feedback).model_dump(mode="json")
            or binding["feedback_payload_hash"] != _hash(payload["feedback"])
            or binding["binding_hash"] != row.receipt_hash
            or _receipt_hash(payload) != row.receipt_hash
            or binding["value_payload_hash"] != row.value_payload_hash
            or basis["owner_id"] != row.owner_id or basis["revision_id"] != row.revision_id
            or basis["request_id"] != row.request_id or basis["recipe_id"] != RECIPE
            or basis["storage_encoding"] != "sqlite-typed-cells.v1"
            or basis["validation_issues"] != binding["validation_issues"]
            or binding["source_witnesses"] != _witnesses(basis["tables"])
        ):
            raise RevisionFeedbackEvaluationError(invalid)
        frozen = {entry["source_table"]: entry for entry in basis["tables"]}
        if set(frozen) != set(TABLES) or len(basis["tables"]) != len(TABLES):
            raise RevisionFeedbackEvaluationError(invalid)
        for name, entry in frozen.items():
            layout = _freeze_table(session, name, [])
            if (set(entry) != set(layout) or entry["columns"] != layout["columns"]
                or entry["column_metadata"] != layout["column_metadata"]
                or entry["primary_key_columns"] != layout["primary_key_columns"]):
                raise RevisionFeedbackEvaluationError(invalid)
        # Replays authenticate their original immutable attachments, not today's
        # Outcome/Evidence. Legitimate later measurements leave frozen basis intact.
        for name, predicate, parameters in (
            ("workbench_feedback", '"feedback_id" = ?', (feedback.feedback_id,)),
            ("report_revisions", '"revision_id" = ?', (row.revision_id,)),
            ("artifacts", '"artifact_id" = ?', (row.artifact_id,)),
            ("research_results", '"run_id" = ?', (row.run_id,)),
        ):
            actual = _freeze_table(session, name, _raw_rows(session, name, predicate, parameters))
            if actual != frozen[name]:
                raise RevisionFeedbackEvaluationError(invalid)
        # Later cost/Outcome/Study updates are not a re-evaluation. Run routing
        # identity still has to identify the original source attachment.
        current_runs = _raw_rows(session, "runs", '"run_id" = ?', (row.run_id,))
        original_runs = frozen["runs"]["rows"]
        if len(current_runs) != len(original_runs):
            raise RevisionFeedbackEvaluationError(invalid)
        if current_runs:
            original_run = {key: _decode_cell(cell) for key, cell in zip(
                frozen["runs"]["columns"], original_runs[0], strict=True
            )}
            for key in ("run_id", "event_id", "study_id", "artifact_id", "strategy_version"):
                if current_runs[0][key] != original_run[key]:
                    raise RevisionFeedbackEvaluationError(invalid)
        if row.status == "available":
            stored = _one(session, ResearchValueEvaluationRecord, row.evaluation_id)
            if stored is None or (
                stored.run_id != row.run_id or stored.artifact_id != row.artifact_id
                or stored.evaluation_version != row.evaluation_version
                or stored.payload_hash != row.value_payload_hash
                or _hash(json.loads(stored.payload_json)) != stored.payload_hash
                or json.loads(stored.payload_json) != payload["value"]
            ):
                raise RevisionFeedbackEvaluationError(invalid)
        elif row.evaluation_id is not None or payload["value"] is not None:
            raise RevisionFeedbackEvaluationError(invalid)
        return typed
    except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RevisionFeedbackEvaluationError):
            raise
        raise RevisionFeedbackEvaluationError(invalid) from exc


def read_revision_feedback_evaluation_in_session(
    session: Session, revision_id: str, request_id: str, owner_id: str,
) -> RevisionFeedbackEvaluation:
    feedback = _owned_feedback(session, revision_id, request_id, owner_id)
    row = session.scalar(select(RevisionFeedbackEvaluationRecord).where(
        RevisionFeedbackEvaluationRecord.feedback_id == feedback.feedback_id
    ).execution_options(populate_existing=True))
    return _verify(session, feedback, row) if row is not None else _legacy(feedback)


def read_latest_revision_feedback_evaluation_in_session(
    session: Session, revision_id: str, owner_id: str,
) -> RevisionFeedbackEvaluation | None:
    row = session.scalar(select(RevisionFeedbackEvaluationRecord).where(
        RevisionFeedbackEvaluationRecord.owner_id == owner_id,
        RevisionFeedbackEvaluationRecord.revision_id == revision_id,
    ).order_by(RevisionFeedbackEvaluationRecord.binding_seq.desc()).limit(1)
        .execution_options(populate_existing=True))
    if row is not None:
        feedback = _owned_feedback(session, revision_id, row.request_id, owner_id)
        return _verify(session, feedback, row)
    legacy = session.scalar(select(FeedbackRecord).where(
        FeedbackRecord.created_by == owner_id, FeedbackRecord.target_type == "report_revision",
        FeedbackRecord.target_id == revision_id,
    ).order_by(FeedbackRecord.created_at.desc(), FeedbackRecord.feedback_id.desc()).limit(1)
        .execution_options(populate_existing=True))
    return _legacy(legacy) if legacy is not None else None


class RevisionFeedbackEvaluationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, revision_id: str, request_id: str, owner_id: str) -> RevisionFeedbackEvaluation:
        with self.database.session() as session:
            _begin_read(session)
            return read_revision_feedback_evaluation_in_session(
                session, revision_id, request_id, owner_id,
            )

    def latest(self, revision_id: str, owner_id: str) -> RevisionFeedbackEvaluation | None:
        with self.database.session() as session:
            _begin_read(session)
            return read_latest_revision_feedback_evaluation_in_session(
                session, revision_id, owner_id,
            )

    def basis(
        self, revision_id: str, request_id: str, owner_id: str,
    ) -> RevisionFeedbackEvaluationBasis:
        with self.database.session() as session:
            _begin_read(session)
            typed = read_revision_feedback_evaluation_in_session(
                session, revision_id, request_id, owner_id,
            )
            if typed.binding is None:
                raise KeyError("revision_feedback_evaluation_not_recorded")
            row = session.scalar(select(RevisionFeedbackEvaluationRecord).where(
                RevisionFeedbackEvaluationRecord.binding_id == typed.binding.binding_id
            ).execution_options(populate_existing=True))
            if row is None:
                raise RevisionFeedbackEvaluationError("revision_feedback_evaluation_invalid")
            return RevisionFeedbackEvaluationBasis.model_validate_json(_canonical({
                "schema_version": BASIS_SCHEMA, "owner_id": owner_id, "revision_id": revision_id,
                "request_id": request_id, "binding_id": row.binding_id,
                "basis_ref": typed.binding.basis_ref, "basis_hash": row.basis_hash,
                "basis_json": row.basis_json,
            }))


def _begin_read(session: Session) -> None:
    # Python's legacy sqlite transaction mode does not BEGIN on a SELECT.
    # Pin all immutable attachment reads to one physical read transaction.
    native = session.connection().connection.driver_connection
    if isinstance(native, sqlite3.Connection) and not native.in_transaction:
        session.connection().exec_driver_sql("BEGIN")
