"""Persistent skill callbacks using the original preparation and domain writers."""

from __future__ import annotations

import json
from dataclasses import replace

from ..persistence.callback_source_lease import callback_source_lease_scope
from ..persistence.operation_phases import OperationPhases
from ..persistence.db import as_utc
from ..persistence.original_preparation import begin_immediate
from .research_skill_package import decoded
from .research_skill_selected_delivery import record_contract


class ResearchSkillCallbackService:
    def __init__(
        self, skills, jobs, *, producer_identity, diagnostics_factory=None, source_observer=None
    ):
        if skills.database is not jobs.database:
            raise ValueError("skill_callback_database_mismatch")
        self.skills, self.jobs, self.database = skills, jobs, jobs.database
        self.producer_identity = producer_identity
        self.diagnostics_factory = diagnostics_factory or OperationPhases
        self.source_observer = source_observer

    def accept(self, raw):
        if not isinstance(raw, bytes) or len(raw) > 1_002_048:
            raise ValueError("skill_callback_request_too_large")
        value = decoded(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"protocol_version", "kind", "body"}
            or value["protocol_version"] != "research-skill-callback.v1"
            or value["kind"] not in {"tool", "records"}
        ):
            raise ValueError("skill_callback_protocol_invalid")
        # Preserve the old 1 MB BUSINESS body capacity after adding the envelope.
        # Count exact raw value bytes; Python/JS float re-encoding can differ.
        text, decoder = raw.decode(), json.JSONDecoder()
        cursor, body_size = text.index("{") + 1, None
        while cursor < len(text):
            while text[cursor].isspace():
                cursor += 1
            key, cursor = decoder.raw_decode(text, cursor)
            while text[cursor].isspace():
                cursor += 1
            if text[cursor] != ":":
                raise ValueError("skill_callback_protocol_invalid")
            cursor += 1
            while text[cursor].isspace():
                cursor += 1
            begin = cursor
            _, cursor = decoder.raw_decode(text, cursor)
            if key == "body":
                body_size = len(text[begin:cursor].encode())
            while text[cursor].isspace():
                cursor += 1
            if text[cursor] == "}":
                break
            cursor += 1
        if body_size is None or body_size > 1_000_000:
            raise ValueError("skill_callback_request_too_large")
        self.skills.packages.contracts.check(
            "tool_request_v1" if value["kind"] == "tool" else record_contract(value["body"]),
            value["body"],
        )
        # Preliminary queue admission is not authentication of a native call.
        # The worker re-reads every original before any domain/result commit.
        return self.jobs.accept(raw, self.producer_identity())

    def get(self, *args, **kwargs):
        return self.jobs.get(*args, **kwargs)


    def _commit(
        self, lease, binding, prepared, freezer, projection, *, execution_check=lambda: None,
        diagnostics=None
    ):
        phases = diagnostics if diagnostics is not None else OperationPhases()
        execution_check()
        with self.database.session() as session:
            with phases.phase("writer_wait"):
                begin_immediate(session)
            phases.writer_acquired(session)
            execution_check()
            row = self.jobs.fence(session, lease)
            if row.producer_identity != self.producer_identity():
                raise ValueError("skill_callback_producer_changed")
            if binding.value["deadline_at"] != as_utc(row.deadline_at).isoformat():
                raise ValueError("skill_callback_frozen_deadline_mismatch")
            binding.verify(session, diagnostics=phases, checkpoint=execution_check)
            result = projection(freezer(session, prepared))
            execution_check()
            self.jobs.fence(session, lease)
            self.jobs.complete_in_session(session, lease, result, checkpoint=binding.value)
            prepared.verify_after_writes(session, diagnostics=phases, checkpoint=execution_check)
            binding.verify_after_writes(session, diagnostics=phases, checkpoint=execution_check)
            execution_check()
            return result

    def _observe_source(self, source_lease, phases, phase):
        if self.source_observer is None:
            return
        try:
            self.source_observer(dict(
                schema_version="research-skill-callback-source-timing.v1",
                operation_id=phases.operation_id, phase=phase,
                native_source_handles=source_lease is not None,
                hashed_bytes=source_lease.hashed_bytes if source_lease is not None else None,
                hash_observations=len(source_lease.hash_observations) if source_lease is not None else None,
                hash_seconds=sum(row[2] for row in source_lease.hash_observations) if source_lease is not None else None,
                identity_checks=source_lease.identity_checks if source_lease is not None else None,
                authority="diagnostic_only_not_permission_or_acceptance",
            ))
        except Exception:
            # No input bodies, SQL, credentials, source paths or diagnostic
            # failures may replace the original domain outcome.
            pass

    def process_claimed(self, lease, *, execution_check=lambda: None, diagnostics=None):
        phases = diagnostics if diagnostics is not None else self.diagnostics_factory()
        envelope = self.jobs.read_claimed(lease)
        kind, body = envelope["kind"], envelope["body"]

        def check():
            execution_check()
            self.jobs.check(lease)

        # One native worker thread owns all HANDLEs from source capture through
        # the physical transaction context's COMMIT/ROLLBACK and finally close.
        # Cancellation only checkpoints here; it cannot release another thread's
        # handles or widen an unconfirmed lease/original task deadline.
        with callback_source_lease_scope(execution_check) as source_lease:
            try:
                prepared = self.skills.prepare_callback(
                    kind, body, request_check=check, preparation_check=execution_check,
                    source_lease=source_lease, diagnostics=phases
                )
                self._observe_source(source_lease, phases, "prepared")
                check()
                binding = replace(prepared, value=decoded(prepared.value.binding_json))
                self.jobs.checkpoint(lease, "binding_authenticated", binding.value)
                self.jobs.checkpoint(lease, "originals_prepared", binding.value)

                def writer(material, freezer, projection):
                    return self._commit(
                        lease, binding, material, freezer, projection,
                        execution_check=execution_check, diagnostics=phases,
                    )

                return self.skills.execute_prepared_callback(
                    prepared, request_check=check, callback_writer=writer
                )
            finally:
                self._observe_source(source_lease, phases, "callback_exit_before_handles_close")
