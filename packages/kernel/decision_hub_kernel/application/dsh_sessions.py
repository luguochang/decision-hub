from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import (
    DshRunSessionLinkView,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
PROMPT_INTEGRITY_SCHEMA = "dsh-prompt-integrity.v1"
EXECUTION_REQUEST_SCHEMA = "dsh-execution-request.v1"


def execution_request_role(generation: int) -> str:
    return f"dsh_execution_request:{generation}"


def prompt_integrity_role(generation: int) -> str:
    return f"dsh_prompt_integrity:{generation}"


def _prompt_integrity_id(run_id: str, generation: int) -> str:
    return "prompt_" + hashlib.sha256(f"{run_id}\0{generation}".encode()).hexdigest()


def _prompt_integrity_json(
    prompt: DshSessionPrompt,
    execution_receipt_hash: str | None = None,
) -> str:
    body = {
        "schema_version": PROMPT_INTEGRITY_SCHEMA,
        "run_id": prompt.run_id,
        "dsh_session_id": prompt.dsh_session_id,
        "request_id": prompt.request_id,
        "request_hash": prompt.request_hash,
        "generation": prompt.generation,
        "prompt_hash": hashlib.sha256(prompt.prompt.encode("utf-8")).hexdigest(),
    }
    if execution_receipt_hash is not None:
        body["execution_request_receipt_hash"] = execution_receipt_hash
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _execution_id(run_id: str, generation: int) -> str:
    return "execution_" + hashlib.sha256(f"{run_id}\0{generation}".encode()).hexdigest()


def _execution_json(
    prompt: DshSessionPrompt,
    request: ResearchSessionRequest,
    task_hash: str,
) -> str:
    payload = request.model_dump(mode="json")
    encoded_request = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(
        {
            "schema_version": EXECUTION_REQUEST_SCHEMA,
            "run_id": prompt.run_id,
            "dsh_session_id": prompt.dsh_session_id,
            "request_id": prompt.request_id,
            "generation": prompt.generation,
            "link_request_hash": prompt.request_hash,
            "task_request_hash": task_hash,
            "request": payload,
            "request_hash": hashlib.sha256(encoded_request.encode()).hexdigest(),
            "execution_request_hash": research_request_hash(request),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def read_execution_request(
    session: Session,
    prompt: DshSessionPromptRecord,
    task_hash: str,
) -> ResearchSessionRequest:
    row = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == prompt.run_id,
            RunRoleOutputRecord.role == execution_request_role(prompt.generation),
        )
    )
    if row is None:
        raise ValueError("recovery_execution_request_missing")
    try:
        body = json.loads(row.payload_json)
        request = ResearchSessionRequest.model_validate(body["request"])
        expected = _execution_json(
            DshSessionLinkService._prompt_view(prompt),  # pyright: ignore[reportPrivateUsage]
            request,
            task_hash,
        )
        if (
            row.output_id != _execution_id(prompt.run_id, prompt.generation)
            or row.schema_version != EXECUTION_REQUEST_SCHEMA
            or row.payload_json != expected
            or row.payload_hash != hashlib.sha256(expected.encode()).hexdigest()
            or as_utc(row.created_at) != as_utc(prompt.created_at)
            or request.run_id != prompt.run_id
        ):
            raise ValueError("invalid_execution_request")
        return request
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("recovery_execution_request_invalid") from exc


def read_prompt_integrity(session: Session, prompt: DshSessionPromptRecord) -> str:
    """Verify an original atomic receipt; never certify a legacy prompt now."""
    row = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == prompt.run_id,
            RunRoleOutputRecord.role == prompt_integrity_role(prompt.generation),
        )
    )
    if row is None:
        raise ValueError("prompt_integrity_missing")
    try:
        execution_hash = json.loads(row.payload_json).get("execution_request_receipt_hash")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("prompt_integrity_invalid") from exc
    if execution_hash is not None:
        execution_row = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == prompt.run_id,
                RunRoleOutputRecord.role == execution_request_role(prompt.generation),
            )
        )
        if (
            execution_row is None
            or execution_row.payload_hash != execution_hash
            or hashlib.sha256(execution_row.payload_json.encode()).hexdigest() != execution_hash
        ):
            raise ValueError("prompt_execution_integrity_invalid")
    encoded = _prompt_integrity_json(
        DshSessionLinkService._prompt_view(prompt),  # pyright: ignore[reportPrivateUsage]
        execution_hash,
    )
    if (
        row.output_id != _prompt_integrity_id(prompt.run_id, prompt.generation)
        or row.schema_version != PROMPT_INTEGRITY_SCHEMA
        or row.payload_json != encoded
        or row.payload_hash != hashlib.sha256(encoded.encode()).hexdigest()
        or as_utc(row.created_at) != as_utc(prompt.created_at)
    ):
        raise ValueError("prompt_integrity_invalid")
    return hashlib.sha256(prompt.prompt.encode("utf-8")).hexdigest()


class DshSessionLinkService:
    """Own the durable correlation boundary without copying DSH Session logs."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        completion_sessions: Callable[[], AbstractContextManager[Session]] | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self._completion_sessions = completion_sessions or database.session

    @staticmethod
    def _begin_write(session: Session) -> None:
        if session.get_bind().dialect.name == "sqlite":
            # Identity, generation and terminal checks belong to the same
            # writer transaction as mutation, including reservation advances.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def deterministic_ids(
        run_id: str,
        request_hash: str,
        generation: int = 1,
        contract_version: str = "dsh-host-bridge.v1",
    ) -> tuple[str, str]:
        if generation < 1:
            raise ValueError("dsh_generation_invalid")
        material = f"{contract_version}\0{run_id}\0{request_hash}".encode()
        digest = hashlib.sha256(material).hexdigest()
        if generation == 1:
            request_digest = digest
        else:
            turn_material = (f"dsh-host-turn.v1\0{run_id}\0{request_hash}\0{generation}").encode()
            request_digest = hashlib.sha256(turn_material).hexdigest()
        return f"dsh_{digest}", f"req_{request_digest}"

    def reserve(
        self, submit: DshSessionSubmit, upstream_identity: DshUpstreamIdentity
    ) -> DshRunSessionLinkView:
        expected_session_id, expected_request_id = self.deterministic_ids(
            submit.run_id, submit.request_hash, submit.generation
        )
        if submit.deterministic_session_id != expected_session_id:
            raise ValueError("dsh_session_id_not_deterministic")
        if submit.deterministic_request_id != expected_request_id:
            raise ValueError("dsh_request_id_not_deterministic")
        now = self.clock()
        with self.database.session() as session:
            self._begin_write(session)
            method = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == submit.run_id,
                    RunRoleOutputRecord.role == "research_execution_method",
                )
                .execution_options(populate_existing=True)
            )
            if method is not None and method.schema_version in {
                "research-execution-method.v2",
                "research-execution-method.v3",
            }:
                raise ValueError("dsh_v2_preparation_required")
            return self.reserve_in_session(session, submit, upstream_identity, recorded_at=now)

    def reserve_in_session(
        self,
        session: Session,
        submit: DshSessionSubmit,
        upstream_identity: DshUpstreamIdentity,
        *,
        recorded_at: datetime,
    ) -> DshRunSessionLinkView:
        """Borrow a writer Session; this low-level helper is not V2 admission proof."""
        if not session.in_transaction():
            raise ValueError("dsh_reservation_transaction_required")
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            driver = connection.connection.driver_connection
            if not isinstance(driver, sqlite3.Connection) or not driver.in_transaction:
                raise ValueError("dsh_reservation_transaction_required")
        expected_session_id, expected_request_id = self.deterministic_ids(
            submit.run_id,
            submit.request_hash,
            submit.generation,
        )
        if submit.deterministic_session_id != expected_session_id:
            raise ValueError("dsh_session_id_not_deterministic")
        if submit.deterministic_request_id != expected_request_id:
            raise ValueError("dsh_request_id_not_deterministic")
        run = session.get(RunRecord, submit.run_id, populate_existing=True)
        if run is None:
            raise KeyError(submit.run_id)
        existing = session.get(DshSessionLinkRecord, submit.run_id, populate_existing=True)
        if existing is not None:
            self._advance_or_assert_reservation(
                existing,
                submit,
                upstream_identity,
                recorded_at=recorded_at,
            )
            session.flush()
            return self._view(existing)
        session_conflict = session.scalar(
            select(DshSessionLinkRecord)
            .where(
                DshSessionLinkRecord.dsh_session_id == submit.deterministic_session_id,
            )
            .execution_options(populate_existing=True)
        )
        if session_conflict is not None:
            raise ValueError("dsh_session_already_linked")
        row = DshSessionLinkRecord(
            run_id=submit.run_id,
            dsh_session_id=submit.deterministic_session_id,
            request_hash=submit.request_hash,
            state="admitted",
            generation=submit.generation,
            upstream_identity_json=self._identity_json(upstream_identity),
            plugin_build_hash=upstream_identity.plugin_build_hash,
            deadline_at=submit.deadline_at,
            max_tool_calls=submit.max_tool_calls,
            model_step_timeout_ms=submit.model_step_timeout_ms,
            tool_calls_started=0,
            accepted_at=None,
            last_seen_at=None,
            terminal_at=None,
            last_seq=0,
            trace_ref=None,
            result_ref=None,
            result_hash=None,
            error_code=None,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(row)
        run.runtime_version = (
            f"dsh-web-{upstream_identity.source_version}+{upstream_identity.plugin_build_hash[:12]}"
        )
        session.flush()
        return self._view(row)

    def accepted(self, callback: DshSessionAccepted) -> DshRunSessionLinkView:
        with self.database.session() as session:
            self._begin_write(session)
            row = self._required(session.get(DshSessionLinkRecord, callback.run_id))
            if self._is_stale_callback(row, callback.dsh_session_id, callback.generation):
                return self._view(row)
            self._assert_callback_identity(row, callback.dsh_session_id, callback.generation)
            changed = False
            if row.accepted_at is None:
                row.accepted_at = callback.accepted_at
                changed = True
            last_seen_at = self._latest(row.last_seen_at, callback.accepted_at)
            if as_utc(row.last_seen_at) != last_seen_at:
                row.last_seen_at = last_seen_at
                changed = True
            if changed:
                row.updated_at = self.clock()
            session.flush()
            return self._view(row)

    def store_prompt(
        self,
        prompt: DshSessionPrompt,
        *,
        execution_request: ResearchSessionRequest | None = None,
    ) -> DshSessionPrompt:
        """Persist the bounded prompt projection so a separate DSH Host can fetch it."""

        with self.database.session() as session:
            self._begin_write(session)
            method = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == prompt.run_id,
                    RunRoleOutputRecord.role == "research_execution_method",
                )
                .execution_options(populate_existing=True)
            )
            if method is not None and method.schema_version in {
                "research-execution-method.v2",
                "research-execution-method.v3",
            }:
                raise ValueError("dsh_v2_preparation_required")
            link = self._required(session.get(DshSessionLinkRecord, prompt.run_id))
            expected_session_id, expected_request_id = self.deterministic_ids(
                prompt.run_id, prompt.request_hash, prompt.generation
            )
            if (
                prompt.dsh_session_id != expected_session_id
                or link.dsh_session_id != expected_session_id
            ):
                raise ValueError("dsh_prompt_session_conflict")
            if prompt.request_id != expected_request_id:
                raise ValueError("dsh_prompt_request_id_not_deterministic")
            if link.request_hash != prompt.request_hash:
                raise ValueError("dsh_prompt_request_hash_conflict")
            if link.generation != prompt.generation:
                raise ValueError("dsh_prompt_generation_conflict")
            existing = session.get(DshSessionPromptRecord, (prompt.run_id, prompt.generation))
            if existing is not None:
                current = self._prompt_view(existing)
                if current != prompt:
                    raise ValueError("dsh_prompt_payload_conflict")
                # Missing legacy integrity remains unknown; duplicate callers
                # cannot manufacture a pre-execution certificate after the fact.
                receipt = session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == prompt.run_id,
                        RunRoleOutputRecord.role == prompt_integrity_role(prompt.generation),
                    )
                )
                if receipt is not None:
                    read_prompt_integrity(session, existing)
                execution = session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == prompt.run_id,
                        RunRoleOutputRecord.role == execution_request_role(prompt.generation),
                    )
                )
                if execution is not None and execution_request is not None:
                    run = session.get(RunRecord, prompt.run_id)
                    if run is None:
                        raise KeyError(prompt.run_id)
                    saved = read_task_request(session, run)
                    if saved is None or read_execution_request(session, existing, saved[1]) != (
                        execution_request
                    ):
                        raise ValueError("dsh_execution_request_payload_conflict")
                return current
            created_at = self.clock()
            execution_hash = None
            if execution_request is not None:
                run = session.get(RunRecord, prompt.run_id)
                if run is None:
                    raise KeyError(prompt.run_id)
                saved = read_task_request(session, run)
                if saved is not None:
                    if (
                        execution_request.run_id != prompt.run_id
                        or execution_request.event_id != saved[0].event_id
                        or (
                            prompt.generation == 1
                            and (research_request_hash(execution_request) != prompt.request_hash)
                        )
                    ):
                        raise ValueError("dsh_execution_request_identity_conflict")
                    execution_json = _execution_json(prompt, execution_request, saved[1])
                    execution_hash = hashlib.sha256(execution_json.encode()).hexdigest()
                    session.add(
                        RunRoleOutputRecord(
                            output_id=_execution_id(prompt.run_id, prompt.generation),
                            run_id=prompt.run_id,
                            role=execution_request_role(prompt.generation),
                            schema_version=EXECUTION_REQUEST_SCHEMA,
                            payload_json=execution_json,
                            payload_hash=execution_hash,
                            created_at=created_at,
                        )
                    )
            encoded = _prompt_integrity_json(prompt, execution_hash)
            session.add(
                DshSessionPromptRecord(
                    run_id=prompt.run_id,
                    generation=prompt.generation,
                    dsh_session_id=prompt.dsh_session_id,
                    request_id=prompt.request_id,
                    request_hash=prompt.request_hash,
                    prompt=prompt.prompt,
                    created_at=created_at,
                )
            )
            session.add(
                RunRoleOutputRecord(
                    output_id=_prompt_integrity_id(prompt.run_id, prompt.generation),
                    run_id=prompt.run_id,
                    role=prompt_integrity_role(prompt.generation),
                    schema_version=PROMPT_INTEGRITY_SCHEMA,
                    payload_json=encoded,
                    payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                    created_at=created_at,
                )
            )
            session.flush()
            return prompt

    def get_prompt(self, run_id: str, generation: int | None = None) -> DshSessionPrompt | None:
        with self.database.session() as session:
            method = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == "research_execution_method",
                )
                .execution_options(populate_existing=True)
            )
            v2_sidecar = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.schema_version.in_(
                        [
                            "dsh-execution-request.v2",
                            "dsh-execution-request.v3",
                            "dsh-prompt-integrity.v2",
                            "research-context-selection-execution-link.v2",
                            "research-context-selection-execution-link.v3",
                        ]
                    ),
                )
                .execution_options(populate_existing=True)
            )
            if (
                method is not None
                and method.schema_version
                in {"research-execution-method.v2", "research-execution-method.v3"}
            ) or v2_sidecar is not None:
                from .research_execution_binding import read_prepared_execution_in_session

                return read_prepared_execution_in_session(session, run_id, generation).host_prompt()
            effective_generation = generation
            if effective_generation is None:
                link = session.get(DshSessionLinkRecord, run_id)
                if link is None:
                    return None
                effective_generation = link.generation
            row = session.get(DshSessionPromptRecord, (run_id, effective_generation))
            return self._prompt_view(row) if row is not None else None

    def observe(
        self, status: DshSessionStatus, *, expected_request_hash: str | None = None,
        expected_lease_owner: str | None = None,
    ) -> DshRunSessionLinkView:
        if status.state in TERMINAL_STATES:
            raise ValueError("dsh_terminal_requires_completion")
        with self.database.session() as session:
            self._begin_write(session)
            row = self._required(session.get(DshSessionLinkRecord, status.run_id))
            if expected_request_hash is not None:
                # Runtime retries carry the original authenticated reply. Repeat
                # the small mutable rights fence in the writer, without rerunning
                # the full frozen proof graph while holding SQLite's writer lock.
                if (row.request_hash != expected_request_hash
                    or row.dsh_session_id != status.dsh_session_id
                    or row.generation != status.generation):
                    raise ValueError("dsh_status_original_binding_changed")
                run = session.get(RunRecord, status.run_id)
                if (run is None or run.status not in {"admitted", "running"}
                    or run.artifact_id is not None or run.finished_at is not None):
                    raise ValueError("dsh_status_run_inactive")
                if expected_lease_owner is not None and (
                    run.status != "running" or run.lease_owner != expected_lease_owner
                    or run.lease_expires_at is None
                    or as_utc(run.lease_expires_at) <= as_utc(self.clock())
                ):
                    raise ValueError("dsh_status_original_lease_lost")
            if self._is_stale_callback(row, status.dsh_session_id, status.generation):
                return self._view(row)
            self._assert_callback_identity(row, status.dsh_session_id, status.generation)
            if row.state in TERMINAL_STATES:
                return self._view(row)
            if status.last_seq < row.last_seq:
                return self._view(row)
            row.state = status.state
            row.last_seq = status.last_seq
            row.last_seen_at = self._latest(row.last_seen_at, status.observed_at)
            row.error_code = status.error_code
            row.updated_at = self.clock()
            session.flush()
            return self._view(row)

    def complete(
        self,
        callback: DshSessionCompletion,
        *,
        require_cancelled: bool = False,
        expected_request_hash: str | None = None,
        binding_validator: Callable[[Session], None] | None = None,
        authenticated_host: bool = False,
    ) -> DshRunSessionLinkView:
        if (callback.result_ref is None) != (callback.result_hash is None):
            raise ValueError("dsh_partial_result_identity_invalid")
        if callback.terminal_status == "completed" and (
            callback.result_ref is None or callback.result_hash is None
        ):
            raise ValueError("dsh_completed_result_required")
        if callback.terminal_status == "failed" and callback.error is None:
            raise ValueError("dsh_failed_error_required")
        with self._completion_sessions() as session:
            self._begin_write(session)
            if require_cancelled:
                run = session.get(RunRecord, callback.run_id)
                if run is None or run.status != "cancelled":
                    raise ValueError("dsh_product_cancel_not_authorized")
            if binding_validator is not None:
                binding_validator(session)
            row = self._required(session.get(DshSessionLinkRecord, callback.run_id))
            if expected_request_hash is not None and row.request_hash != expected_request_hash:
                raise ValueError("dsh_run_request_conflict")
            if self._is_stale_callback(row, callback.dsh_session_id, callback.generation):
                return self._view(row)
            self._assert_callback_identity(row, callback.dsh_session_id, callback.generation)

            def freeze_host_callback() -> None:
                if authenticated_host:
                    from .dsh_terminal_receipts import freeze_authenticated_terminal_in_session

                    freeze_authenticated_terminal_in_session(
                        session, row, callback, recorded_at=self.clock(),
                    )

            if row.state in TERMINAL_STATES:
                if (
                    row.state == callback.terminal_status
                    and row.last_seq == callback.last_seq
                    and row.result_hash == callback.result_hash
                    and row.error_code == (callback.error.code if callback.error else None)
                ):
                    freeze_host_callback()
                    return self._view(row)
                if (
                    row.state in {"failed", "cancelled"}
                    and callback.terminal_status == row.state
                    and row.last_seq == callback.last_seq
                    and row.error_code == (callback.error.code if callback.error else None)
                    and row.result_ref is None
                    and row.result_hash is None
                    and callback.result_ref is not None
                    and callback.result_hash is not None
                ):
                    # A newer Host build or a restarted Host can discover that
                    # a failed or deadline-cancelled DSH turn had already
                    # completed trusted research calls. Permit this one-way
                    # enrichment without changing the terminal status,
                    # sequence, error, or original time.
                    row.trace_ref = callback.trace_ref
                    row.result_ref = callback.result_ref
                    row.result_hash = callback.result_hash
                    row.last_seen_at = self._latest(row.last_seen_at, callback.completed_at)
                    row.updated_at = self.clock()
                    freeze_host_callback()
                    session.flush()
                    return self._view(row)
                raise ValueError("dsh_terminal_conflict")
            if callback.last_seq < row.last_seq:
                raise ValueError("dsh_terminal_sequence_regression")
            row.state = callback.terminal_status
            row.last_seq = callback.last_seq
            row.last_seen_at = self._latest(row.last_seen_at, callback.completed_at)
            row.terminal_at = callback.completed_at
            row.trace_ref = callback.trace_ref
            row.result_ref = callback.result_ref
            row.result_hash = callback.result_hash
            row.error_code = callback.error.code if callback.error else None
            row.updated_at = self.clock()
            freeze_host_callback()
            session.flush()
            return self._view(row)

    def get(self, run_id: str) -> DshRunSessionLinkView | None:
        with self.database.session() as session:
            row = session.get(DshSessionLinkRecord, run_id)
            return self._view(row) if row is not None else None

    def get_by_session(self, dsh_session_id: str) -> DshRunSessionLinkView | None:
        """Resolve a DSH session identity to its Hub-owned durable link."""

        with self.database.session() as session:
            row = session.scalar(
                select(DshSessionLinkRecord).where(
                    DshSessionLinkRecord.dsh_session_id == dsh_session_id
                )
            )
            return self._view(row) if row is not None else None

    @staticmethod
    def _required(row: DshSessionLinkRecord | None) -> DshSessionLinkRecord:
        if row is None:
            raise KeyError("dsh_session_link_not_found")
        return row

    @staticmethod
    def _identity_json(identity: DshUpstreamIdentity) -> str:
        return json.dumps(identity.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def _advance_or_assert_reservation(
        self,
        row: DshSessionLinkRecord,
        submit: DshSessionSubmit,
        identity: DshUpstreamIdentity,
        *,
        recorded_at: datetime | None = None,
    ) -> None:
        if row.dsh_session_id != submit.deterministic_session_id:
            raise ValueError("dsh_run_session_conflict")
        if row.request_hash != submit.request_hash:
            raise ValueError("dsh_run_request_conflict")
        if row.upstream_identity_json != self._identity_json(identity):
            raise ValueError("dsh_upstream_identity_conflict")
        if row.deadline_at is None:
            # Rows created before migration 0026 have no trustworthy ceiling.
            # The first re-admission supplies the immutable accepted deadline;
            # until then the capability gateway remains fail-closed.
            row.deadline_at = submit.deadline_at
        elif as_utc(row.deadline_at) != as_utc(submit.deadline_at):
            raise ValueError("dsh_deadline_conflict")
        if row.max_tool_calls is None:
            row.max_tool_calls = submit.max_tool_calls
        elif row.max_tool_calls != submit.max_tool_calls:
            raise ValueError("dsh_tool_budget_conflict")
        if (
            row.model_step_timeout_ms is not None
            and row.model_step_timeout_ms != submit.model_step_timeout_ms
        ):
            raise ValueError("dsh_model_step_budget_conflict")
        # Legacy NULL is retained: a current submission cannot certify an old budget.
        if row.generation == submit.generation:
            return
        if submit.generation != row.generation + 1:
            raise ValueError("dsh_generation_conflict")
        if row.state != "completed":
            raise ValueError("dsh_generation_not_ready")
        row.generation = submit.generation
        row.state = "admitted"
        row.accepted_at = None
        row.terminal_at = None
        row.trace_ref = None
        row.result_ref = None
        row.result_hash = None
        row.error_code = None
        row.updated_at = recorded_at if recorded_at is not None else self.clock()

    @staticmethod
    def _is_stale_callback(row: DshSessionLinkRecord, dsh_session_id: str, generation: int) -> bool:
        if row.dsh_session_id != dsh_session_id:
            raise ValueError("dsh_session_callback_conflict")
        return generation < row.generation

    @staticmethod
    def _assert_callback_identity(
        row: DshSessionLinkRecord, dsh_session_id: str, generation: int
    ) -> None:
        if row.dsh_session_id != dsh_session_id:
            raise ValueError("dsh_session_callback_conflict")
        if row.generation != generation:
            raise ValueError("dsh_generation_conflict")

    @staticmethod
    def _latest(current: datetime | None, candidate: datetime) -> datetime:
        normalized = as_utc(current)
        return candidate if normalized is None or candidate > normalized else normalized

    @staticmethod
    def _view(row: DshSessionLinkRecord) -> DshRunSessionLinkView:
        return DshRunSessionLinkView.model_validate(
            {
                "schema_version": "dsh-run-session-link.v1",
                "run_id": row.run_id,
                "dsh_session_id": row.dsh_session_id,
                "request_hash": row.request_hash,
                "state": row.state,
                "generation": row.generation,
                "upstream_identity": json.loads(row.upstream_identity_json),
                "deadline_at": as_utc(row.deadline_at),
                "max_tool_calls": row.max_tool_calls,
                "model_step_timeout_ms": row.model_step_timeout_ms,
                "tool_calls_started": row.tool_calls_started,
                "accepted_at": as_utc(row.accepted_at),
                "last_seen_at": as_utc(row.last_seen_at),
                "terminal_at": as_utc(row.terminal_at),
                "last_seq": row.last_seq,
                "trace_ref": row.trace_ref,
                "result_ref": row.result_ref,
                "result_hash": row.result_hash,
                "error_code": row.error_code,
                "created_at": as_utc(row.created_at),
                "updated_at": as_utc(row.updated_at),
            }
        )

    @staticmethod
    def _prompt_view(row: DshSessionPromptRecord) -> DshSessionPrompt:
        return DshSessionPrompt(
            schema_version="dsh-session-prompt.v1",
            run_id=row.run_id,
            dsh_session_id=row.dsh_session_id,
            request_id=row.request_id,
            request_hash=row.request_hash,
            generation=row.generation,
            prompt=row.prompt,
        )
