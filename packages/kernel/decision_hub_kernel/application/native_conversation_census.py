"""Immutable authenticated Host lifecycle originals, independent of input rows.

The Host adapter observes official local Agent/Session objects. This boundary is
an authenticated software observation, not independent OS/provider attestation.
No history/result hash is modified and no missing old census is manufactured.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..persistence.db import Database, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.original_preparation import (
    ExecutionBoundary,
    begin_immediate,
    current_execution_boundary,
    prepare_originals,
)
from ..ports.native_conversation_census import (
    NativeConversationCensusOriginal,
    NativeConversationCensusRecorded,
    NativeConversationCensusV1,
)
from ..ports.research_observation import ObservedExecutionRef
from .model_dispatch import CHILD_ROLE, ROLE, ModelDispatchService
from .provider_input_receipts import (
    _fence,  # pyright: ignore[reportPrivateUsage]
    _permission,  # pyright: ignore[reportPrivateUsage]
    borrowed_provider_read,
    row_reference,
    sha,
    tool_arguments_json,
)
from .research_execution_binding import read_prepared_execution_in_session
from .research_publication_binding import native_observation_context_in_session, observation_json
from .research_recipe_read_scope import recipe_original_read_scope

SCHEMA = "research-native-conversation-census.v1"
MAX_BYTES = 8_388_608
KINDS = {"step/start", "step/end", "turn/start", "turn/end"}


def role(generation: int) -> str:
    return f"native_conversation_census:{generation}"


def _decode(text: str) -> Any:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("research_provider_census_bytes_exceeded")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        body = {}
        for key, value in values:
            if key in body:
                raise ValueError("research_provider_census_duplicate_key")
            body[key] = value
        return body

    def invalid(_value: str) -> Any:
        raise ValueError("research_provider_census_json_invalid")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
        tool_arguments_json(value)
    except (RecursionError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("research_provider_census_json_invalid") from exc
    # Existing ECMAScript codec rejects unsafe/nonfinite numbers and unsupported
    # values. The event strings remain exact separate immutable originals.
    return value


def validate_census(
    census: NativeConversationCensusV1,
) -> tuple[Counter[str], dict[str, list[dict[str, Any]]]]:
    body = census.model_dump(mode="json", exclude={"census_sha256"})
    encoded = tool_arguments_json(body)
    if len(encoded.encode("utf-8")) > MAX_BYTES or sha(encoded) != census.census_sha256:
        raise ValueError("research_provider_census_hash_invalid")
    ids = list(census.registered_session_ids)
    sessions = {item.session_id: item for item in census.sessions}
    if (
        len(set(ids)) != len(ids)
        or set(ids) != set(sessions)
        or len(sessions) != len(census.sessions)
        or census.root_session_id not in sessions
        or len({item.lifecycle_id for item in census.sessions}) != len(sessions)
    ):
        raise ValueError("research_provider_census_registry_invalid")
    root = census.root_session_id
    for item in census.sessions:
        current, visited = item.session_id, set()
        while current != root:
            if current in visited or current not in sessions:
                raise ValueError("research_provider_census_parent_invalid")
            visited.add(current)
            current = sessions[current].parent_session_id
        if sessions[root].parent_session_id is not None:
            raise ValueError("research_provider_census_parent_invalid")
    counts: Counter[str] = Counter()
    projections = {}
    for item in census.sessions:
        if (item.events_json is None) != (item.events_sha256 is None):
            raise ValueError("research_provider_census_projection_invalid")
        events = [] if item.events_json is None else _decode(item.events_json)
        if (
            not isinstance(events, list)
            or len(events) > 50_000
            or (item.events_json is not None and sha(item.events_json) != item.events_sha256)
        ):
            raise ValueError("research_provider_census_projection_invalid")
        turns, turn_ends, steps, step_ends = {}, {}, {}, {}
        seqs = []
        for notification in events:
            if not isinstance(notification, dict) or set(notification) != {"method", "payload"}:
                raise ValueError("research_provider_census_event_invalid")
            payload = notification["payload"]
            if (
                notification["method"] != "session.event"
                or not isinstance(payload, dict)
                or (
                    set(payload) != {"sessionId", "event"}
                    or payload["sessionId"] != item.session_id
                )
            ):
                raise ValueError("research_provider_census_event_invalid")
            native = payload["event"]
            if (
                not isinstance(native, dict)
                or set(native) != {"type", "seq", "time", "data"}
                or (
                    native["type"] not in KINDS
                    or type(native["seq"]) is not int
                    or native["seq"] < 0
                    or type(native["time"]) not in {int, float}
                    or not isinstance(native["data"], dict)
                )
            ):
                raise ValueError("research_provider_census_event_invalid")
            seq = native["seq"]
            if (
                seqs
                and seq <= seqs[-1]
                or item.first_live_seq is None
                or (
                    item.native_first_seq is None
                    or item.native_last_seq is None
                    or not item.first_live_seq
                    <= item.native_first_seq
                    <= seq
                    <= item.native_last_seq
                )
            ):
                raise ValueError("research_provider_census_sequence_invalid")
            seqs.append(seq)
            turn = native["data"].get("turn")
            if type(turn) is not int or turn < 0:
                raise ValueError("research_provider_census_turn_invalid")
            if native["type"].startswith("turn/"):
                table, key = (turns if native["type"] == "turn/start" else turn_ends), turn
            else:
                step = native["data"].get("step")
                if type(step) is not int or step < 0:
                    raise ValueError("research_provider_census_step_invalid")
                table, key = (steps if native["type"] == "step/start" else step_ends), (turn, step)
                if native["type"] == "step/start":
                    counts[item.session_id] += 1
            if key in table:
                raise ValueError("research_provider_census_control_duplicate")
            table[key] = seq
        if item.capture_status == "complete":
            if (
                item.birth_status != "observed"
                or not item.settled
                or item.missing_codes
                or (
                    not turns
                    or turns.keys() != turn_ends.keys()
                    or steps.keys() != step_ends.keys()
                    or any(turns[key] >= turn_ends[key] for key in turns)
                    or any(steps[key] >= step_ends[key] for key in steps)
                    or any(
                        key[0] not in turns
                        or not turns[key[0]] < steps[key] < step_ends[key] < turn_ends[key[0]]
                        for key in steps
                    )
                )
            ):
                raise ValueError("research_provider_census_complete_invalid")
            terminal = events[-1]["payload"]["event"]
            reason = terminal["data"].get("reason")
            if (
                terminal["type"] != "turn/end"
                or terminal["seq"] != item.terminal_seq
                or (
                    not isinstance(reason, dict)
                    or reason.get("kind") != item.terminal_kind
                    or item.terminal_kind != "completed"
                    or item.session_id == root
                    and item.first_live_seq != 0
                )
            ):
                raise ValueError("research_provider_census_terminal_invalid")
            counts.setdefault(item.session_id, 0)
        elif not item.missing_codes:
            raise ValueError("research_provider_census_unknown_reason_missing")
        projections[item.session_id] = events
    if census.registry_status == "complete" and (
        census.missing_codes
        or not census.observer_installed_before_root_birth
        or census.generation != 1
        or any(item.capture_status != "complete" for item in census.sessions)
    ):
        raise ValueError("research_provider_census_registry_complete_invalid")
    if census.registry_status == "unknown" and not census.missing_codes:
        raise ValueError("research_provider_census_unknown_reason_missing")
    return counts, projections


def _bindings(session: Session, run_id: str) -> list[RunRoleOutputRecord]:
    return list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                (RunRoleOutputRecord.role == ROLE)
                | RunRoleOutputRecord.role.startswith(CHILD_ROLE),
            )
            .order_by(RunRoleOutputRecord.output_id)
            .execution_options(populate_existing=True)
        )
    )


def _binding_ids(rows: list[RunRoleOutputRecord], census: NativeConversationCensusV1):
    bindings = {}
    for row in rows:
        binding = ModelDispatchService._read(row)  # pyright: ignore[reportPrivateUsage]
        execution = binding.execution_session_id or binding.dsh_session_id
        if (
            binding.run_id,
            binding.generation,
            binding.dsh_session_id,
            binding.request_hash,
            binding.plugin_build_hash,
            binding.purpose,
        ) != (
            census.run_id,
            census.generation,
            census.root_session_id,
            census.request_hash,
            census.plugin_build_hash,
            "conversation",
        ) or execution in bindings:
            raise ValueError("research_provider_census_model_binding_invalid")
        bindings[execution] = binding
    return bindings


@borrowed_provider_read
def read_native_census_in_session(
    session: Session,
    *,
    run_id: str,
    generation: int,
    as_of: datetime | None = None,
) -> tuple[NativeConversationCensusOriginal, ObservedExecutionRef] | None:
    row = session.scalar(
        select(RunRoleOutputRecord)
        .filter_by(
            run_id=run_id,
            role=role(generation),
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    value = NativeConversationCensusOriginal.model_validate_json(row.payload_json)
    census = value.census
    if (
        row.schema_version != SCHEMA
        or row.output_id != "ncensus_" + sha(f"{run_id}:{generation}")
        or row.payload_json != observation_json(value.model_dump(mode="json"))
        or (
            sha(row.payload_json) != row.payload_hash
            or as_utc(row.created_at) != value.known_at
            or (census.run_id, census.generation) != (run_id, generation)
            or (
                value.native_context.run_id,
                value.native_context.generation,
                value.native_context.dsh_session_id,
                value.native_context.first_linked_request_hash,
            )
            != (run_id, generation, census.root_session_id, census.request_hash)
        )
    ):
        raise ValueError("research_provider_census_original_changed")
    if as_of is not None and value.known_at > as_of:
        return None
    validate_census(census)
    for ref in (*value.source_refs, *value.model_binding_refs):
        source = session.get(RunRoleOutputRecord, ref.output_id, populate_existing=True)
        if (
            source is None
            or source.run_id != run_id
            or row_reference(source) != ref
            or sha(source.payload_json) != source.payload_hash
        ):
            raise ValueError("research_provider_census_source_changed")
    actual = _bindings(session, run_id)
    if tuple(row_reference(item) for item in actual) != value.model_binding_refs:
        raise ValueError("research_provider_census_binding_set_changed")
    bindings = _binding_ids(actual, census)
    for item in census.sessions:
        binding = bindings.get(item.session_id)
        if binding is not None and binding.parent_session_id != item.parent_session_id:
            raise ValueError("research_provider_census_binding_parent_changed")
    if not set(value.native_context.rows).issubset(set(value.source_refs)):
        raise ValueError("research_provider_census_source_missing")
    return value, row_reference(row)


@dataclass(frozen=True)
class _Prepared:
    boundary: ExecutionBoundary
    value: NativeConversationCensusOriginal
    owner: str


class NativeConversationCensusService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow):
        self.database, self.clock = database, clock

    def _prepare(self, session: Session, census: NativeConversationCensusV1) -> _Prepared:
        with recipe_original_read_scope(session):
            validate_census(census)
            rows = _bindings(session, census.run_id)
            bindings = _binding_ids(rows, census)
            root = bindings.get(census.root_session_id)
            if root is None:
                raise ValueError("research_provider_census_root_binding_missing")
            saved = read_prepared_execution_in_session(session, census.run_id)
            boundary = current_execution_boundary(session, census.run_id, prepared=saved)
            run = session.get(RunRecord, census.run_id, populate_existing=True)
            if run is None:
                raise KeyError(census.run_id)
            _permission(session, census.run_id, run.lease_owner)
            ModelDispatchService(self.database)._validate_in_session(  # pyright: ignore[reportPrivateUsage]
                session,
                root,
            )
            context = native_observation_context_in_session(session, census.run_id, prepared=saved)
            if context.first_linked_request_hash != census.request_hash or (
                context.dsh_session_id != census.root_session_id
                or context.generation != census.generation
            ):
                raise ValueError("research_provider_census_current_execution_mismatch")
            refs = list(context.rows)
            selection = getattr(saved.execution, "current_selection_row", None)
            if selection is not None:
                selected = session.get(
                    RunRoleOutputRecord, selection.output_id, populate_existing=True
                )
                if selected is None or selected.payload_hash != selection.payload_hash:
                    raise ValueError("research_provider_census_selection_missing")
                refs.append(row_reference(selected))
            for name in ("graph_recipe_selection",):
                row = session.scalar(
                    select(RunRoleOutputRecord)
                    .filter_by(
                        run_id=census.run_id,
                        role=name,
                    )
                    .execution_options(populate_existing=True)
                )
                if row is not None:
                    refs.append(row_reference(row))
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("research_provider_census_clock_invalid")
            assert run.lease_owner is not None
            return _Prepared(
                boundary,
                NativeConversationCensusOriginal(
                    schema_version=SCHEMA,
                    census=census,
                    native_context=context,
                    source_refs=tuple(refs),
                    model_binding_refs=tuple(row_reference(row) for row in rows),
                    known_at=now,
                ),
                run.lease_owner,
            )

    def record(
        self,
        census: NativeConversationCensusV1,
        *,
        request_check: Callable[[], None] | None = None,
        diagnostics: Any = None,
    ) -> NativeConversationCensusRecorded:
        check = request_check or (lambda: None)
        census = NativeConversationCensusV1.model_validate_json(census.model_dump_json())
        check()
        prepared = prepare_originals(
            self.database,
            lambda session: self._prepare(session, census),
            link_activity_observations=True,
            link_tool_counter_observations=True,
        )
        check()
        with self.database.session() as session:

            def before_commit(_session: Session) -> None:
                check()

            event.listen(session, "before_commit", before_commit)
            begin_immediate(session)
            prepared.verify(session)
            prepared.value.boundary.require_current_link(session, census.run_id)
            _permission(session, census.run_id, prepared.value.owner)
            check()
            existing = read_native_census_in_session(
                session,
                run_id=census.run_id,
                generation=census.generation,
            )
            if existing is not None:
                value, reference = existing
                if value.census != census:
                    raise ValueError("research_provider_census_conflict")
            else:
                value = prepared.value.value
                encoded = observation_json(value.model_dump(mode="json"))
                row = RunRoleOutputRecord(
                    output_id="ncensus_" + sha(f"{census.run_id}:{census.generation}"),
                    run_id=census.run_id,
                    role=role(census.generation),
                    schema_version=SCHEMA,
                    payload_json=encoded,
                    payload_hash=sha(encoded),
                    created_at=value.known_at,
                )
                session.add(row)
                session.flush()
                reference = row_reference(row)
                read_native_census_in_session(
                    session,
                    run_id=census.run_id,
                    generation=census.generation,
                )
            prepared.verify_after_writes(session)
            prepared.value.boundary.require_current_link(session, census.run_id)
            _fence(session, census.run_id, prepared.value.owner)
            check()
        return NativeConversationCensusRecorded(
            run_id=census.run_id,
            generation=census.generation,
            census_sha256=census.census_sha256,
            reference=reference,
        )
