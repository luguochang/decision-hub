from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.models import (
    DshRunSessionLinkView,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    as_utc,
    utcnow,
)

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class DshSessionLinkService:
    """Own the durable correlation boundary without copying DSH Session logs."""

    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

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
            turn_material = (
                f"dsh-host-turn.v1\0{run_id}\0{request_hash}\0{generation}"
            ).encode()
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
            if session.get(RunRecord, submit.run_id) is None:
                raise KeyError(submit.run_id)
            existing = session.get(DshSessionLinkRecord, submit.run_id)
            if existing is not None:
                self._advance_or_assert_reservation(existing, submit, upstream_identity)
                session.flush()
                return self._view(existing)
            session_conflict = session.scalar(
                select(DshSessionLinkRecord).where(
                    DshSessionLinkRecord.dsh_session_id == submit.deterministic_session_id
                )
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
                tool_calls_started=0,
                accepted_at=None,
                last_seen_at=None,
                terminal_at=None,
                last_seq=0,
                trace_ref=None,
                result_ref=None,
                result_hash=None,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return self._view(row)

    def accepted(self, callback: DshSessionAccepted) -> DshRunSessionLinkView:
        with self.database.session() as session:
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

    def store_prompt(self, prompt: DshSessionPrompt) -> DshSessionPrompt:
        """Persist the bounded prompt projection so a separate DSH Host can fetch it."""

        with self.database.session() as session:
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
            existing = session.get(
                DshSessionPromptRecord, (prompt.run_id, prompt.generation)
            )
            if existing is not None:
                current = self._prompt_view(existing)
                if current != prompt:
                    raise ValueError("dsh_prompt_payload_conflict")
                return current
            session.add(
                DshSessionPromptRecord(
                    run_id=prompt.run_id,
                    generation=prompt.generation,
                    dsh_session_id=prompt.dsh_session_id,
                    request_id=prompt.request_id,
                    request_hash=prompt.request_hash,
                    prompt=prompt.prompt,
                    created_at=self.clock(),
                )
            )
            session.flush()
            return prompt

    def get_prompt(self, run_id: str, generation: int | None = None) -> DshSessionPrompt | None:
        with self.database.session() as session:
            effective_generation = generation
            if effective_generation is None:
                link = session.get(DshSessionLinkRecord, run_id)
                if link is None:
                    return None
                effective_generation = link.generation
            row = session.get(DshSessionPromptRecord, (run_id, effective_generation))
            return self._prompt_view(row) if row is not None else None

    def observe(self, status: DshSessionStatus) -> DshRunSessionLinkView:
        if status.state in TERMINAL_STATES:
            raise ValueError("dsh_terminal_requires_completion")
        with self.database.session() as session:
            row = self._required(session.get(DshSessionLinkRecord, status.run_id))
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

    def complete(self, callback: DshSessionCompletion) -> DshRunSessionLinkView:
        if callback.terminal_status == "completed" and (
            callback.result_ref is None or callback.result_hash is None
        ):
            raise ValueError("dsh_completed_result_required")
        if callback.terminal_status == "failed" and callback.error is None:
            raise ValueError("dsh_failed_error_required")
        with self.database.session() as session:
            row = self._required(session.get(DshSessionLinkRecord, callback.run_id))
            if self._is_stale_callback(row, callback.dsh_session_id, callback.generation):
                return self._view(row)
            self._assert_callback_identity(row, callback.dsh_session_id, callback.generation)
            if row.state in TERMINAL_STATES:
                if (
                    row.state == callback.terminal_status
                    and row.last_seq == callback.last_seq
                    and row.result_hash == callback.result_hash
                    and row.error_code == (callback.error.code if callback.error else None)
                ):
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
        row.updated_at = self.clock()

    @staticmethod
    def _is_stale_callback(
        row: DshSessionLinkRecord, dsh_session_id: str, generation: int
    ) -> bool:
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
