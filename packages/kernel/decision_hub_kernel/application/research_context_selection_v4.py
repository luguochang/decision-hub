"""Standalone T1 selection originals, not an RPC or execution permission.

Owned selection authenticates in RO, then rechecks originals in a short writer.
Borrowed methods never begin, commit or close a caller session. Their no-op CAS obtains a writer lock;
in_transaction alone cannot identify SQLite's BEGIN mode. Historical reads
check fixed originals and grant no current execution permission.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import DateTime, String, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCompiledPolicyReceiptV1,
    ResearchPreparedBundleIdentity,
    ResearchRequestPackReceiptV2,
    ResearchSessionRequest,
    ResearchTaskReceiptV2,
)
from packages.contracts_py.decision_hub_contracts.context_selection_semantics import (
    MAX_CONSIDERED_INPUTS,
    SELECTION_MAX_BYTES,
    parse_rfc3339_microseconds,
)
from packages.contracts_py.decision_hub_contracts.generated.research_context_selection_v4 import (
    ResearchContextSelectionReceiptV4,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    BUNDLE_ROLES,
    TASK_ROLE,
    FrozenResearchBundle,
)
from packages.kernel.decision_hub_kernel.application.research_context import build_task_policy
from packages.kernel.decision_hub_kernel.application.research_context_selection_versions import (
    replay_retained_context_selection_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context_selector_v3 import (
    RANKING_RULES_REF,
    ContextSelectionComputation,
    input_from_candidate,
    select_context_v3,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_bundle_identity_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphRoundAdmissionRef,
    read_graph_round_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_dispatch import (
    read_graph_recipe_selection_for_run_in_session as read_graph_recipe_selection_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_history_versions import (
    replay_retained_context_selection_in_session as replay_graph_context_selection,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    AgenticResearchState,
    initial_research_state,
    request_for_state,
    requirements_for_round,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_initial_begin_busy
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    PreparedOriginals, prepare_originals,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

SCHEMA = "research-context-selection.v4"
SELF_HASH_KIND = "canonical_context_selection.v4"
MAX_STORED_SELECTION_BYTES = SELECTION_MAX_BYTES
_ROLE = re.compile(r"context_select\.r([1-9]|10)\.a([1-9][0-9]{0,9})")
_TIME_KEYS = {
    "published_at",
    "observed_at",
    "received_at",
    "accepted_at",
    "created_at",
    "recorded_at",
    "pit_cutoff_at",
    "deadline_at",
    "decision_cutoff_at",
    "cutoff_at",
}
_ROOT = Path(__file__).resolve().parents[4]
_IDENTITY_PATHS = (
    "packages/kernel/decision_hub_kernel/application/research_context.py",
    "packages/kernel/decision_hub_kernel/application/research_context_selector_v3.py",
    "packages/kernel/decision_hub_kernel/application/research_context_selection_v4.py",
    "packages/kernel/decision_hub_kernel/application/research_graph_projection.py",
    "packages/kernel/decision_hub_kernel/application/research_graph_binding.py",
    "packages/kernel/decision_hub_kernel/application/research_binding.py",
    "packages/kernel/decision_hub_kernel/application/research_execution_binding.py",
    "packages/contracts_py/decision_hub_contracts/context_selection_semantics.py",
    "packages/contracts_py/decision_hub_contracts/context_selection_v4_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/generated/agentic_research.py",
    "packages/contracts_py/decision_hub_contracts/generated/research_context_selection_v4.py",
    "contracts/schemas/research_context_selection_v4.schema.yaml",
    "packages/kernel/decision_hub_kernel/application/research_context_selection_versions.py",
    "packages/kernel/decision_hub_kernel/application/research_graph_recipe.py",
)


def _selector_file_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((_ROOT / name).read_bytes()).hexdigest() for name in _IDENTITY_PATHS
    }


# A complete process starts from sealed source. A later filesystem change must
# not let this loaded selector mint the new disk implementation's identity.
_LOADED_SELECTOR_FILES = _selector_file_hashes()


class ContextSelectionError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


class ContextSelectionReference(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )
    output_id: str
    run_id: str
    role: str
    schema_version: Literal["research-context-selection.v4"] = SCHEMA
    payload_hash: str
    recorded_at: AwareDatetime


@dataclass(frozen=True)
class FrozenContextSelection:
    reference: ContextSelectionReference
    receipt: ResearchContextSelectionReceiptV4
    payload_json: str

    @property
    def request(self) -> ResearchSessionRequest:
        return ResearchSessionRequest.model_validate_json(
            self.receipt.projected_request.model_dump_json()
        )


@dataclass(frozen=True)
class _PreparedSelection:
    original: FrozenContextSelection
    existing: bool
    run_id: str
    owner: str
    deadline_at: datetime


@dataclass(frozen=True)
class _Source:
    bundle: FrozenResearchBundle
    identity: ResearchPreparedBundleIdentity
    task_row: RunRoleOutputRecord
    state: AgenticResearchState
    prior: GraphRoundAdmissionRef | None
    raw_rows: tuple[dict[str, Any], ...]
    candidates: tuple[EvidenceCandidate, ...]
    protected_refs: tuple[str, ...]


def _json(model: BaseModel) -> dict[str, Any]:
    return json.loads(model.model_dump_json())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()


def _time(value: str) -> datetime:
    micros = parse_rfc3339_microseconds(value, inherited=True)
    return datetime(1, 1, 1, tzinfo=UTC) + timedelta(microseconds=micros)


def _sql_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ContextSelectionError("context_selection_raw_sql_time_invalid")
    # Existing SQLite DateTime storage is naive UTC. Preserve the raw string in
    # the row hash; this conversion is only a lossless value projection.
    raw = value.replace(" ", "T", 1)
    if not re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", raw):
        raw += "Z"
    return _time(raw)


def _guard_raw_times(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _TIME_KEYS and item is not None:
                if not isinstance(item, str):
                    raise ContextSelectionError("context_selection_raw_time_required")
                _time(item)
            else:
                _guard_raw_times(item)
    elif isinstance(value, list):
        for item in value:
            _guard_raw_times(item)


def _raw_rows(session: Session, table: Any, predicates: tuple[Any, ...]) -> list[dict[str, Any]]:
    columns = [
        sql_cast(column, String).label(column.name) if isinstance(column.type, DateTime) else column
        for column in table.columns
    ]
    return [dict(row) for row in session.execute(select(*columns).where(*predicates)).mappings()]


def _role_row(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    return session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == role,
        )
        .execution_options(populate_existing=True)
    )


def _physical_transaction(session: Session) -> None:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ContextSelectionError("context_selection_writer_transaction_required")


def _selection_id(run_id: str, graph_round: int, attempt_no: int) -> str:
    return "ctxsel_" + recipe_hash(
        {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "graph_round": graph_round,
            "attempt_no": attempt_no,
        }
    )


def _selector_identity() -> dict[str, Any]:
    try:
        files = _selector_file_hashes()
    except OSError as error:
        raise ContextSelectionError("context_selection_loaded_source_changed") from error
    if files != _LOADED_SELECTOR_FILES:
        raise ContextSelectionError("context_selection_loaded_source_changed")
    return {
        "time_rule_ref": "research-context.received-cutoff.v1",
        "ranking_rules_ref": RANKING_RULES_REF,
        "context_policy_source_hash": files[_IDENTITY_PATHS[0]],
        "producer_source_hash": files[_IDENTITY_PATHS[2]],
        "implementation_hash": recipe_hash({"files": files, "codec": SELF_HASH_KIND}),
        "implementation_hash_kind": "canonical_context_selector_sources.v1",
    }


def _raw_bundle(session: Session, run_id: str) -> dict[str, RunRoleOutputRecord]:
    table = RunRoleOutputRecord.__table__
    raw = _raw_rows(session, table, (table.c.run_id == run_id, table.c.role.in_(BUNDLE_ROLES)))
    if len(raw) != 3:
        raise ContextSelectionError("context_selection_prepared_bundle_required")
    for item in raw:
        _sql_time(item["created_at"])
        _guard_raw_times(json.loads(item["payload_json"]))
    rows = {
        row.role: row
        for row in session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_(BUNDLE_ROLES),
            )
            .execution_options(populate_existing=True)
        )
    }
    if set(rows) != set(BUNDLE_ROLES):
        raise ContextSelectionError("context_selection_prepared_bundle_required")
    return rows


def _audit_bundle(
    rows: dict[str, RunRoleOutputRecord],
    saved: ResearchPreparedBundleIdentity,
) -> FrozenResearchBundle:
    # Pure raw integrity, not a replacement B1 authorization validator.
    values = (saved.pack, saved.task, saved.compiled)
    for anchor in values:
        row = rows.get(anchor.role)
        if (
            row is None
            or row.output_id != anchor.output_id
            or row.run_id != anchor.run_id
            or row.schema_version != anchor.row_schema_version
            or row.payload_hash != anchor.payload_hash
            or _sha(row.payload_json) != anchor.payload_hash
            or as_utc(row.created_at) != anchor.recorded_at
        ):
            raise ContextSelectionError("context_selection_bundle_anchor_changed")
    original_identity = _json(saved)
    if saved.bundle_hash != recipe_hash(
        {k: v for k, v in original_identity.items() if k != "bundle_hash"}
    ):
        raise ContextSelectionError("context_selection_bundle_identity_invalid")
    return FrozenResearchBundle(
        ResearchRequestPackReceiptV2.model_validate_json(rows[saved.pack.role].payload_json),
        ResearchTaskReceiptV2.model_validate_json(rows[saved.task.role].payload_json),
        ResearchCompiledPolicyReceiptV1.model_validate_json(rows[saved.compiled.role].payload_json),
        saved.recorded_at,
    )


def _source(
    session: Session,
    *,
    run_id: str,
    graph_round: int,
    previous_graph_ref: GraphRoundAdmissionRef | None,
    owner: str | None,
    active: bool,
    original_identity: ResearchPreparedBundleIdentity | None = None,
) -> _Source:
    rows = _raw_bundle(session, run_id)
    if active:
        bundle, identity = read_bundle_identity_in_session(session, run_id)
        if original_identity is not None and _json(identity) != _json(original_identity):
            raise ContextSelectionError("context_selection_bundle_anchor_changed")
    else:
        if original_identity is None:
            raise ContextSelectionError("context_selection_original_bundle_anchor_required")
        identity = original_identity
        bundle = _audit_bundle(rows, identity)
    if graph_round > bundle.request.execution_budget.max_evidence_rounds:
        raise ContextSelectionError("context_selection_round_budget_exhausted")
    recipe = read_graph_recipe_selection_in_session(session, run_id)
    if recipe is None or recipe.receipt.recipe_version != "research-graph-projection.v3":
        raise ContextSelectionError("context_selection_v4_graph_recipe_required")
    raw_rows: list[dict[str, Any]] = []
    candidates: tuple[EvidenceCandidate, ...] = ()
    state: AgenticResearchState
    if previous_graph_ref is None:
        if graph_round != 1:
            raise ContextSelectionError("context_selection_graph_prior_required")
        state = initial_research_state(bundle.request)
    else:
        # An explicit wire union permits both releases. Only the real Task's
        # frozen Graph selection determines which prior is legal for this Run.
        recipe = read_graph_recipe_selection_in_session(session, run_id)
        if recipe is None or recipe.receipt.recipe_version != "research-graph-projection.v3":
            raise ContextSelectionError("context_selection_v4_graph_recipe_required")
        expected_schema = "research-graph-round-admission.v3"
        if previous_graph_ref.row.schema_version != expected_schema:
            raise ContextSelectionError("context_selection_graph_prior_version_mismatch")
        graph_table = RunRoleOutputRecord.__table__
        originals = _raw_rows(
            session, graph_table, (graph_table.c.output_id == previous_graph_ref.row.output_id,)
        )
        if len(originals) != 1 or originals[0]["run_id"] != run_id:
            raise ContextSelectionError("context_selection_graph_prior_invalid")
        _guard_raw_times(json.loads(originals[0]["payload_json"]))
        _sql_time(originals[0]["created_at"])
        prior = read_graph_round_in_session(
            session,
            run_id=run_id,
            reference=previous_graph_ref,
            expected_lease_owner=owner,
            require_current_permission=active,
        )
        if prior.receipt.logical_round + 1 != graph_round:
            raise ContextSelectionError("context_selection_graph_prior_round_invalid")
        state = {**prior.state, "current_round": graph_round}
        candidates = tuple(
            EvidenceCandidate.model_validate(item) for item in state.get("evidence", [])
        )
        if len(candidates) + len(bundle.request.input_evidence) > MAX_CONSIDERED_INPUTS:
            raise ContextSelectionError("context_selection_considered_limit")
        evidence_table = ResearchEvidenceRecord.__table__
        for candidate in candidates:
            found = _raw_rows(
                session,
                evidence_table,
                (
                    evidence_table.c.run_id == run_id,
                    evidence_table.c.evidence_id == candidate.evidence_id,
                ),
            )
            if len(found) != 1:
                raise ContextSelectionError("context_selection_evidence_source_missing")
            row = found[0]
            for column in evidence_table.columns:
                if isinstance(column.type, DateTime) and row[column.name] is not None:
                    _sql_time(row[column.name])
            raw_rows.append(row)
    return _Source(
        bundle,
        identity,
        rows[TASK_ROLE],
        state,
        previous_graph_ref,
        tuple(raw_rows),
        candidates,
        tuple(recipe.receipt.quality_context.additional_required_context_refs),
    )


def _witnesses(
    source: _Source, computation: ContextSelectionComputation
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task_request = source.bundle.request
    task_anchor = {
        "output_id": source.task_row.output_id,
        "run_id": source.task_row.run_id,
        "role": TASK_ROLE,
        "row_schema_version": source.task_row.schema_version,
        "payload_hash": source.task_row.payload_hash,
        "recorded_at": source.identity.task.recorded_at.isoformat(),
        "payload_hash_kind": "raw_run_role_output_payload_utf8.v1",
        "original_task_request_hash": recipe_hash(_json(task_request)),
        "original_task_request_hash_kind": "canonical_research_session_request.v1",
    }
    witnesses: list[dict[str, Any]] = []
    for index, item in enumerate(computation.considered):
        body = _json(item)
        witness: dict[str, Any] = {
            "source_kind": "original_task_input"
            if index < computation.task_count
            else "same_run_evidence",
            "origin_basis": "frozen_task_declaration"
            if index < computation.task_count
            else "durable_evidence_declaration",
            "input": body,
            "input_hash": recipe_hash(body),
            "input_hash_kind": "canonical_context_input.v1",
            "task_anchor": task_anchor if index < computation.task_count else None,
            "row": None,
            "row_hash": None,
            "row_hash_kind": None,
        }
        if index >= computation.task_count:
            raw = source.raw_rows[index - computation.task_count]
            declaration = dict(raw)
            for column in ResearchEvidenceRecord.__table__.columns:
                if isinstance(column.type, DateTime) and declaration[column.name] is not None:
                    declaration[column.name] = _sql_time(declaration[column.name]).isoformat()
            witness.update(
                row=declaration,
                row_hash=recipe_hash(raw),
                row_hash_kind="canonical_research_evidence_row_witness.v1",
            )
            expected = input_from_candidate(source.candidates[index - computation.task_count])
            if _json(expected) != body:
                raise ContextSelectionError("context_selection_evidence_input_changed")
        witnesses.append(witness)
    return task_anchor, witnesses


def _body(
    source: _Source,
    *,
    graph_round: int,
    attempt_no: int,
    attempt_basis: str,
    parent_selection_id: str | None,
    selection_clock: datetime | None,
    recorded_at: datetime,
) -> dict[str, Any]:
    original = source.bundle.request
    pit = _time(original.pit_cutoff_at.isoformat())
    cutoff = min(pit, selection_clock) if selection_clock is not None else pit
    decision = _time(source.state["decision_cutoff_at"])
    requirements = requirements_for_round(
        original.evidence_requirements,
        source.state,
        allowed_capabilities=original.allowed_capabilities,
    )
    policy = build_task_policy(
        research_mode=original.research_mode or "current_decision",
        requirements=requirements,
        created_at=decision,
        max_evidence_rounds=original.execution_budget.max_evidence_rounds,
        context_inputs=original.input_evidence,
    )
    prior_exclusions = (
        original.context_manifest.excluded_inputs if original.context_manifest else ()
    )
    computed = select_context_v3(
        original.input_evidence,
        source.candidates,
        requirements=requirements,
        policy=policy,
        manifest_prefix=f"context:{original.run_id}",
        revision=graph_round,
        created_at=decision,
        allowed_capabilities=original.allowed_capabilities,
        prior_exclusions=prior_exclusions,
        eligibility_cutoff_at=cutoff,
        protected_refs=source.protected_refs,
    )
    projected = request_for_state(source.state, eligibility_cutoff=cutoff).model_copy(
        update={
            "input_evidence": list(computed.projected),
            "context_manifest": computed.manifest,
        }
    )
    projected = ResearchSessionRequest.model_validate_json(projected.model_dump_json())
    task_anchor, witnesses = _witnesses(source, computed)
    prior = (
        None
        if source.prior is None
        else {
            "kind": "graph_round_admission",
            "run_id": original.run_id,
            "graph_round": graph_round - 1,
            "row": _json(source.prior.row),
        }
    )
    return {
        "schema_version": SCHEMA,
        "receipt_state": "context_selected",
        "selection_id": _selection_id(original.run_id, graph_round, attempt_no),
        "run_id": original.run_id,
        "event_id": original.event_id,
        "graph_round": graph_round,
        "attempt_no": attempt_no,
        "attempt_basis": attempt_basis,
        "parent_selection_id": parent_selection_id,
        "task_anchor": task_anchor,
        "prepared_bundle_identity": _json(source.identity),
        "execution_mode": original.execution_mode,
        "original_pit_cutoff_at": pit.isoformat(),
        "live_selection_clock_at": selection_clock.isoformat()
        if selection_clock is not None
        else None,
        "effective_eligibility_cutoff_at": cutoff.isoformat(),
        "cutoff_basis": "live_clock_min_original_pit"
        if original.execution_mode == "live"
        else "replay_original_pit",
        "decision_cutoff_before_selection": decision.isoformat(),
        "decision_cutoff_basis": "replay_original_pit"
        if original.execution_mode == "replay"
        else ("original_task_max_received" if source.prior is None else "graph_state_declared"),
        "decision_basis_ref": prior,
        "selector_identity": _selector_identity(),
        "context_policy": _json(policy),
        "considered_inputs": witnesses,
        "inherited_exclusions": [_json(item) for item in prior_exclusions],
        "projected_request": _json(projected),
        "current_full_request_hash": recipe_hash(_json(projected)),
        "current_full_request_hash_kind": "canonical_research_session_request.v1",
        "recorded_at": recorded_at.isoformat(),
        "selection_payload_hash": "0" * 64,
        "selection_payload_hash_kind": SELF_HASH_KIND,
    }


def _encode(body: dict[str, Any]) -> tuple[ResearchContextSelectionReceiptV4, str]:
    normalized = _json(ResearchContextSelectionReceiptV4.model_validate(body))
    normalized["selection_payload_hash"] = recipe_hash(
        {k: v for k, v in normalized.items() if k != "selection_payload_hash"}
    )
    receipt = ResearchContextSelectionReceiptV4.model_validate(normalized)
    payload = canonical_recipe(_json(receipt))
    if len(payload.encode("utf-8", errors="strict")) > MAX_STORED_SELECTION_BYTES:
        raise ContextSelectionError("context_selection_final_payload_limit")
    if receipt.selection_payload_hash != recipe_hash(
        {k: v for k, v in _json(receipt).items() if k != "selection_payload_hash"}
    ):
        raise ContextSelectionError("context_selection_canonicalization_changed")
    return receipt, payload


def _insert_selection_in_session(
    session: Session, receipt: ResearchContextSelectionReceiptV4, payload: str
) -> RunRoleOutputRecord:
    row = RunRoleOutputRecord(
        output_id=receipt.selection_id,
        run_id=receipt.run_id,
        role=f"context_select.r{receipt.graph_round}.a{receipt.attempt_no}",
        schema_version=SCHEMA,
        payload_json=payload,
        payload_hash=_sha(payload),
        created_at=receipt.recorded_at,
    )
    session.add(row)
    session.flush()
    return row


def _frozen(
    row: RunRoleOutputRecord, receipt: ResearchContextSelectionReceiptV4
) -> FrozenContextSelection:
    return FrozenContextSelection(
        ContextSelectionReference(
            output_id=row.output_id,
            run_id=row.run_id,
            role=row.role,
            schema_version=SCHEMA,
            payload_hash=row.payload_hash,
            recorded_at=receipt.recorded_at,
        ),
        receipt,
        row.payload_json,
    )


class ContextSelectionService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ContextSelectionError("context_selection_clock_timezone_required")
        return _time(now.isoformat())

    def _fence(
        self, session: Session, run_id: str, owner: str | None, deadline: datetime | None = None
    ) -> None:
        if not owner:
            raise ContextSelectionError("context_selection_expected_owner_required")
        now = self._now()
        if deadline is not None and now >= deadline:
            raise PermissionError("context_selection_deadline_elapsed")
        result = session.execute(
            update(RunRecord)
            .where(
                RunRecord.run_id == run_id,
                RunRecord.status == "running",
                RunRecord.artifact_id.is_(None),
                RunRecord.lease_owner == owner,
                RunRecord.lease_expires_at > now,
            )
            .values(lease_owner=owner)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", None) != 1:
            raise PermissionError("context_selection_writer_permission_lost")
        session.get(RunRecord, run_id, populate_existing=True)

    def select(self, **kwargs: Any) -> FrozenContextSelection:
        # One owned native callable/thread holds the source HANDLE lease from
        # complete RO authentication through the real writer's COMMIT exit.
        with callback_source_lease_scope() as source_lease:
            def prepare(session: Session) -> _PreparedSelection:
                result = self._select_in_session(session, **kwargs, _prepare_only=True)
                assert type(result) is _PreparedSelection
                if source_lease is not None:
                    source_lease.extend_authenticated_selection(session, result.run_id)
                return result

            prepared = prepare_originals(self.database, prepare, source_lease=source_lease)
            return self._commit_prepared(prepared)

    def _commit_prepared(
        self, prepared: PreparedOriginals[_PreparedSelection],
    ) -> FrozenContextSelection:
        with self.database.session() as session:
            # A forged/replaced Prepared object is not an original proof. The
            # issuer also binds this exact Engine, value, source and readset.
            if type(prepared) is not PreparedOriginals:
                raise ContextSelectionError("context_selection_preparation_unissued")
            prepared._require_issued(session)
            value = prepared.value
            if type(value) is not _PreparedSelection:
                raise ContextSelectionError("context_selection_preparation_invalid")
            connection = session.connection()
            native = connection.connection.driver_connection
            clean = not session.new and not session.dirty and not session.deleted
            initial = isinstance(native, sqlite3.Connection) and not native.in_transaction
            changes = native.total_changes if isinstance(native, sqlite3.Connection) else None
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except OperationalError as error:
                # Only this initial statement is recoverable. No selection SQL
                # ran; the Worker must still authenticate its official checkpoint
                # and original permissions before it can retain a pending state.
                if (
                    is_sqlite_initial_begin_busy(error)
                    and clean
                    and initial
                    and isinstance(native, sqlite3.Connection)
                    and not native.in_transaction
                    and native.total_changes == changes
                    and not session.new
                    and not session.dirty
                    and not session.deleted
                ):
                    raise AgentExecutionError(
                        "research_context_selection_pending",
                        "Context selection preparation was not confirmed",
                        provider_id="dsh-web",
                        origin="orchestration",
                        retryable=True,
                        cause_code="sqlite_initial_begin_busy",
                    ) from error
                raise
            try:
                # All source/range/absence/raw-byte queries are replayed here;
                # no _source, historical rebuilding or selector runs in writer.
                prepared.verify(session)
                self._fence(session, value.run_id, value.owner, value.deadline_at)
                frozen = value.original
                if value.existing:
                    result = frozen
                else:
                    row = _insert_selection_in_session(session, frozen.receipt, frozen.payload_json)
                    result = _frozen(row, frozen.receipt)
                self._fence(session, value.run_id, value.owner, value.deadline_at)
                session.commit()
                return result
            except BaseException:
                session.rollback()
                raise

    def select_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        graph_round: int,
        attempt_no: int = 1,
        attempt_basis: Literal[
            "initial_round", "next_round", "explicit_reselection"
        ] = "initial_round",
        previous_graph_ref: GraphRoundAdmissionRef | None = None,
        expected_lease_owner: str | None,
        parent_selection_ref: ContextSelectionReference | None = None,
    ) -> FrozenContextSelection:
        result = self._select_in_session(
            session, run_id=run_id, graph_round=graph_round, attempt_no=attempt_no,
            attempt_basis=attempt_basis, previous_graph_ref=previous_graph_ref,
            expected_lease_owner=expected_lease_owner, parent_selection_ref=parent_selection_ref,
        )
        assert type(result) is FrozenContextSelection
        return result

    def _read_permission(
        self, session: Session, run_id: str, owner: str | None, deadline: datetime | None = None,
    ) -> None:
        # This RO check rejects unusable work early; only the fresh writer CAS
        # after all readset verification can authorize inserting a selection.
        if not owner:
            raise ContextSelectionError("context_selection_expected_owner_required")
        now = self._now()
        run = session.get(RunRecord, run_id, populate_existing=True)
        if (run is None or run.status != "running" or run.artifact_id is not None
                or run.lease_owner != owner or as_utc(run.lease_expires_at) is None
                or as_utc(run.lease_expires_at) <= now):
            raise PermissionError("context_selection_writer_permission_lost")
        if deadline is not None and now >= deadline:
            raise PermissionError("context_selection_deadline_elapsed")

    def _select_in_session(
        self, session: Session, *, run_id: str, graph_round: int, attempt_no: int = 1,
        attempt_basis: Literal["initial_round", "next_round", "explicit_reselection"] = "initial_round",
        previous_graph_ref: GraphRoundAdmissionRef | None = None,
        expected_lease_owner: str | None,
        parent_selection_ref: ContextSelectionReference | None = None,
        _prepare_only: bool = False,
    ) -> FrozenContextSelection | _PreparedSelection:
        _physical_transaction(session)
        if (
            type(graph_round) is not int
            or not 1 <= graph_round <= 10
            or type(attempt_no) is not int
            or not 1 <= attempt_no <= 2_147_483_647
        ):
            raise ContextSelectionError("context_selection_attempt_identity_invalid")
        fence = self._read_permission if _prepare_only else self._fence
        fence(session, run_id, expected_lease_owner)
        role = f"context_select.r{graph_round}.a{attempt_no}"
        existing = _role_row(session, run_id, role)
        if existing is not None:
            if existing.schema_version != SCHEMA:
                raise ContextSelectionError("context_selection_existing_schema_conflict")
            original = ResearchContextSelectionReceiptV4.model_validate_json(existing.payload_json)
            frozen = self.read_in_session(
                session,
                run_id=run_id,
                reference=_frozen(existing, original).reference,
                require_current_permission=True,
                expected_lease_owner=expected_lease_owner,
                _read_only_preparation=_prepare_only,
            )
            expected_prior = None if previous_graph_ref is None else _json(previous_graph_ref.row)
            saved_prior = (
                None
                if original.decision_basis_ref is None
                else _json(original.decision_basis_ref.row)
            )
            if (
                original.attempt_basis != attempt_basis
                or expected_prior != saved_prior
                or original.parent_selection_id
                != (parent_selection_ref.output_id if parent_selection_ref else None)
            ):
                raise ContextSelectionError("context_selection_existing_binding_conflict")
            if _prepare_only:
                assert expected_lease_owner is not None
                return _PreparedSelection(frozen, True, run_id, expected_lease_owner,
                                          frozen.receipt.projected_request.deadline_at)
            return frozen
        parent_id = None
        if attempt_basis == "explicit_reselection":
            if attempt_no < 2 or parent_selection_ref is None:
                raise ContextSelectionError("context_selection_parent_required")
            parent = self.read_in_session(
                session,
                run_id=run_id,
                reference=parent_selection_ref,
                require_current_permission=True,
                expected_lease_owner=expected_lease_owner,
                _read_only_preparation=_prepare_only,
            )
            if (
                parent.receipt.graph_round != graph_round
                or parent.receipt.attempt_no + 1 != attempt_no
            ):
                raise ContextSelectionError("context_selection_parent_attempt_invalid")
            parent_id = parent.reference.output_id
        elif (
            attempt_no != 1
            or parent_selection_ref is not None
            or (attempt_basis == "initial_round") != (graph_round == 1)
        ):
            raise ContextSelectionError("context_selection_attempt_basis_invalid")
        source = _source(
            session,
            run_id=run_id,
            graph_round=graph_round,
            previous_graph_ref=previous_graph_ref,
            owner=expected_lease_owner,
            active=True,
        )
        fence(session, run_id, expected_lease_owner, source.bundle.request.deadline_at)
        selection_clock = self._now() if source.bundle.request.execution_mode == "live" else None
        receipt, payload = _encode(
            _body(
                source,
                graph_round=graph_round,
                attempt_no=attempt_no,
                attempt_basis=attempt_basis,
                parent_selection_id=parent_id,
                selection_clock=selection_clock,
                recorded_at=self._now(),
            )
        )
        if _prepare_only:
            if session.get(RunRoleOutputRecord, receipt.selection_id, populate_existing=True) is not None:
                raise ContextSelectionError("context_selection_id_collision")
            frozen = FrozenContextSelection(
                ContextSelectionReference(output_id=receipt.selection_id, run_id=run_id,
                    role=role, payload_hash=_sha(payload), recorded_at=receipt.recorded_at),
                receipt, payload,
            )
            assert expected_lease_owner is not None
            return _PreparedSelection(frozen, False, run_id, expected_lease_owner,
                                      source.bundle.request.deadline_at)
        row = _insert_selection_in_session(session, receipt, payload)
        fence(session, run_id, expected_lease_owner, source.bundle.request.deadline_at)
        return _frozen(row, receipt)

    def read_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        reference: ContextSelectionReference,
        require_current_permission: bool,
        expected_lease_owner: str | None = None,
        _read_only_preparation: bool = False,
    ) -> FrozenContextSelection:
        reference = ContextSelectionReference.model_validate_json(reference.model_dump_json())
        row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
        if (
            row is None
            or row.run_id != run_id
            or reference.run_id != run_id
            or row.role != reference.role
            or row.schema_version != SCHEMA
            or row.payload_hash != reference.payload_hash
            or _sha(row.payload_json) != reference.payload_hash
            or as_utc(row.created_at) != reference.recorded_at
        ):
            raise ContextSelectionError("context_selection_original_row_changed")
        if len(row.payload_json.encode("utf-8", errors="strict")) > MAX_STORED_SELECTION_BYTES:
            raise ContextSelectionError("context_selection_final_payload_limit")
        receipt = ResearchContextSelectionReceiptV4.model_validate_json(row.payload_json)
        if (
            receipt.run_id != run_id
            or row.role != f"context_select.r{receipt.graph_round}.a{receipt.attempt_no}"
            or receipt.selection_id != row.output_id
            or receipt.selection_id
            != _selection_id(run_id, receipt.graph_round, receipt.attempt_no)
            or canonical_recipe(_json(receipt)) != row.payload_json
            or receipt.selection_payload_hash
            != recipe_hash(
                {k: v for k, v in _json(receipt).items() if k != "selection_payload_hash"}
            )
        ):
            raise ContextSelectionError("context_selection_original_payload_invalid")
        if _json(receipt.selector_identity) != _selector_identity():
            if require_current_permission:
                raise ContextSelectionError("context_selection_implementation_unavailable")
            retained = replay_graph_context_selection(
                session,
                selector_identity=_json(receipt.selector_identity),
                run_id=run_id,
                reference_json=reference.model_dump_json(),
                clock=self.clock,
            )
            if retained is None:
                retained = replay_retained_context_selection_in_session(
                    session,
                    selector_identity=_json(receipt.selector_identity),
                    run_id=run_id,
                    reference_json=reference.model_dump_json(),
                    clock=self.clock,
                )
            if retained is None:
                raise ContextSelectionError("context_selection_implementation_unavailable")
            if retained != row.payload_json:
                raise ContextSelectionError("context_selection_source_projection_changed")
            return _frozen(row, receipt)
        if receipt.prepared_bundle_identity is None:
            raise ContextSelectionError("context_selection_original_bundle_anchor_required")
        identity = ResearchPreparedBundleIdentity.model_validate_json(
            receipt.prepared_bundle_identity.model_dump_json()
        )
        prior = (
            None
            if receipt.decision_basis_ref is None
            else GraphRoundAdmissionRef.model_validate_json(
                canonical_recipe(
                    {
                        "kind": "graph_round_admission",
                        "row": _json(receipt.decision_basis_ref.row),
                    }
                )
            )
        )
        if require_current_permission:
            _physical_transaction(session)
            (self._read_permission if _read_only_preparation else self._fence)(
                session, run_id, expected_lease_owner)
        source = _source(
            session,
            run_id=run_id,
            graph_round=receipt.graph_round,
            previous_graph_ref=prior,
            owner=expected_lease_owner,
            active=require_current_permission,
            original_identity=identity,
        )
        rebuilt, text = _encode(
            _body(
                source,
                graph_round=receipt.graph_round,
                attempt_no=receipt.attempt_no,
                attempt_basis=receipt.attempt_basis,
                parent_selection_id=receipt.parent_selection_id,
                selection_clock=receipt.live_selection_clock_at,
                recorded_at=receipt.recorded_at,
            )
        )
        if (
            text != row.payload_json
            or rebuilt.selection_payload_hash != receipt.selection_payload_hash
        ):
            raise ContextSelectionError("context_selection_source_projection_changed")
        if require_current_permission:
            (self._read_permission if _read_only_preparation else self._fence)(
                session, run_id, expected_lease_owner, source.bundle.request.deadline_at)
        return _frozen(row, receipt)
