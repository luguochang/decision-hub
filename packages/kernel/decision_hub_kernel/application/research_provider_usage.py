"""Read an exact conversation receipt census. No billing or budget authority.

Root native steps are an independent denominator. Current Host results omit child
inspection events; those Runs remain incomplete even when every saved child input
has a finish. Receipt-row counts never certify their own completeness.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionResult
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputRef,
)

from ..persistence.db import DshSessionPromptRecord, RunRoleOutputRecord, as_utc
from ..ports.market_path import aware_utc
from ..ports.provider_input import (
    ProviderConversationUsageCensusV1,
    ProviderDispatchUsageV1,
    ProviderFinishOriginal,
    ProviderInputOriginal,
)
from ..ports.research_observation import ObservedExecutionRef, ResearchNativeObservationContext
from .dsh_terminal_receipts import (
    SCHEMA as TERMINAL_SCHEMA,
)
from .dsh_terminal_receipts import (
    read_authenticated_terminal_in_session,
    validate_terminal_result_native_metadata,
)
from .model_dispatch import CHILD_ROLE, ROLE, ModelDispatchService
from .provider_input_receipts import (
    _read_row,  # pyright: ignore[reportPrivateUsage]
    borrowed_provider_read,
    read_provider_finish_in_session,
    read_provider_input_in_session,
    row_reference,
    sha,
)
from .research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
)

_LOADED_USAGE_SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _usage_owner() -> tuple[int, Any]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


@dataclass
class _UsageReadScope:
    session: Session
    boundary: Any
    owner: tuple[int, Any]
    marker: str
    # Canonical immutable material, never a shared DTO, permission or clock.
    values: dict[tuple[str, str, bool], str] = field(default_factory=dict)

    def require_unchanged(self) -> None:
        try:
            source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("research_usage_read_scope_changed") from exc
        if (
            self.owner != _usage_owner()
            or not self.boundary.unchanged()
            or self.boundary.connection.connection.driver_connection is not self.boundary.driver
            or source_hash != _LOADED_USAGE_SOURCE_SHA
            or any(
                globals().get(name) is not function or function.__code__ is not code
                or getattr(function, "__wrapped__", None) is not wrapped
                or wrapped is not None and wrapped.__code__ is not wrapped_code
                for name, (function, code, wrapped, wrapped_code) in
                _LOADED_USAGE_FUNCTIONS.items()
            )
        ):
            raise ValueError("research_usage_read_scope_changed")


_USAGE_READ_SCOPE: ContextVar[_UsageReadScope | None] = ContextVar(
    "provider_usage_read_scope", default=None
)


@contextmanager
def provider_usage_read_scope(session: Session) -> Generator[None]:
    """Opt in explicitly; AUTH keeps the original full reader and authorizer.

    The scope owns a native random read marker. A raw COMMIT/BEGIN cannot retain
    its memo, even when ORM transaction identity and every row byte are unchanged.
    """
    from .research_graph_binding import _local_original_read  # pyright: ignore[reportPrivateUsage]

    with _local_original_read(session) as boundary:
        marker = "quality_read_" + uuid4().hex
        scope = _UsageReadScope(session, boundary, _usage_owner(), marker)
        scope.require_unchanged()
        native = cast(sqlite3.Connection, boundary.driver)
        try:
            native.execute(f"SAVEPOINT {marker}")
        except sqlite3.DatabaseError as exc:
            if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_AUTH:
                raise
            token = _USAGE_READ_SCOPE.set(None)
            try:
                yield
            finally:
                _USAGE_READ_SCOPE.reset(token)
            return
        token = _USAGE_READ_SCOPE.set(scope)
        released = False
        try:
            with session.no_autoflush:
                yield
                _USAGE_READ_SCOPE.reset(token)
                token = _USAGE_READ_SCOPE.set(None)
                scope.require_unchanged()
                for (reference, at, require_native), saved in scope.values.items():
                    fresh = _read_usage_material(
                        session,
                        ObservedExecutionRef.model_validate_json(reference),
                        datetime.fromisoformat(at),
                        require_native,
                    )
                    if fresh != saved:
                        raise ValueError("research_usage_frozen_computation_changed")
                    scope.require_unchanged()
                try:
                    native.execute(f"RELEASE SAVEPOINT {marker}")
                except sqlite3.Error as exc:
                    raise ValueError("research_usage_read_scope_changed") from exc
                released = True
        finally:
            scope.values.clear()
            _USAGE_READ_SCOPE.reset(token)
            if not released:
                try:
                    native.execute(f"RELEASE SAVEPOINT {marker}")
                except sqlite3.Error:
                    pass


def _rows(session: Session, run_id: str, at: datetime) -> list[RunRoleOutputRecord]:
    return list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.created_at <= at,
                (
                    RunRoleOutputRecord.role.like("model_input:%")
                    | RunRoleOutputRecord.role.like("model_finish:%")
                    | (RunRoleOutputRecord.role == ROLE)
                    | RunRoleOutputRecord.role.startswith(CHILD_ROLE)
                ),
            )
            .order_by(RunRoleOutputRecord.output_id)
            .execution_options(populate_existing=True)
        )
    )


def _steps(reply: DshSessionResult) -> Counter[str]:
    # Events are exact retained bytes. Unknown event types grant no denominator.
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        body = {}
        for key, value in values:
            if key in body:
                raise ValueError("research_usage_native_duplicate_key")
            body[key] = value
        return body

    # Retained native event strings use their existing 8 MiB DTO bound, not the
    # 1 MiB individual provider input bound.
    events = json.loads(reply.events_json, object_pairs_hook=pairs)
    if not isinstance(events, list):
        raise ValueError("research_usage_native_events_invalid")
    identities: dict[tuple[str, int], str] = {}
    counts: Counter[str] = Counter()
    for item in events:
        if not isinstance(item, dict) or item.get("method") != "session.event":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        event, session_id = payload.get("event"), payload.get("sessionId")
        if not isinstance(event, dict) or event.get("type") != "step/start":
            continue
        seq = event.get("seq")
        if not isinstance(session_id, str) or not session_id or type(seq) is not int or seq < 0:
            raise ValueError("research_usage_native_step_identity_invalid")
        key, digest = (session_id, seq), observation_sha(item)
        prior = identities.get(key)
        if prior is not None:
            if prior != digest:
                raise ValueError("research_usage_native_step_conflict")
            continue
        identities[key] = digest
        counts[session_id] += 1
    return counts


def _declared_children(reply: DshSessionResult) -> tuple[set[str], bool]:
    """Restrict completeness by exact root native delegation returns/notices.

    These declarations cannot authenticate a child or a review. They only make
    omitted independent registry/binding originals visible as missing coverage.
    """
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _returned_review_wrappers,  # pyright: ignore[reportPrivateUsage]
        _tool_result_failed,  # pyright: ignore[reportPrivateUsage]
        _typed_content_items,  # pyright: ignore[reportPrivateUsage]
        _typed_tool_identity,  # pyright: ignore[reportPrivateUsage]
        _typed_tool_result_call_id,  # pyright: ignore[reportPrivateUsage]
    )

    events = []
    children, unresolved = set(), False
    for item in json.loads(reply.events_json):
        if not isinstance(item, dict) or item.get("method") != "session.event":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("sessionId") != reply.dsh_session_id:
            continue
        native = payload.get("event")
        if not isinstance(native, dict) or not isinstance(native.get("data"), dict):
            continue
        events.append(native)
        source = native["data"].get("source")
        if native.get("type") == "user/message" and isinstance(source, dict):
            child = source.get("senderSessionId")
            if source.get("kind") == "subagent-settled" and isinstance(child, str) and child:
                children.add(child)
    for index, native in enumerate(events):
        if native.get("type") != "tool/call":
            continue
        identity = _typed_tool_identity(native["data"], item_type="tool-call")
        if identity is None or not re.search(r"(?:^|[./])subagent(?:_reviewer)?$", identity[1]):
            continue
        results = [
            item["data"]
            for item in events[index + 1 :]
            if item.get("type") in {"tool/result", "tool/call-result"}
            and _typed_tool_result_call_id(item["data"]) == identity[0]
        ]
        if len(results) != 1 or _tool_result_failed(results[0]):
            unresolved = True
            continue
        result, declared = results[0], set()
        for wrapper in _returned_review_wrappers(result, identity[0]):
            child = wrapper.get("child_session_id")
            if isinstance(child, str) and child:
                declared.add(child)
        typed = list(_typed_content_items(result, "tool-result"))
        rows = typed or [result]
        for row in rows:
            if _typed_tool_result_call_id(row) != identity[0]:
                continue
            content = row.get("content")
            texts = (
                [
                    block["text"]
                    for block in content
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ]
                if isinstance(content, list)
                else [row.get("result")]
            )
            for text in texts:
                if not isinstance(text, str):
                    continue
                match = re.fullmatch(r"started subagent ([A-Za-z0-9_-]{1,256})", text)
                if match is not None:
                    declared.add(match[1])
        children.update(declared)
        unresolved |= not declared
    return children, unresolved


def _root_projection_matches(
    session: Session,
    context: ResearchNativeObservationContext,
    reply: DshSessionResult,
    complete: list[dict[str, Any]],
) -> bool:
    """Match retained control bytes, allowing only the Host's exact pre-RPC cut.

    The independent census retains the full lifecycle denominator. Host results
    retain the accepted RPC and its whole tail, omitting controls before that RPC.
    Neither observed provider inputs nor the raw-tail step count supply this cut.
    """
    events = []
    for item in json.loads(reply.events_json):
        if (
            isinstance(item, dict)
            and item.get("method") == "session.event"
            and isinstance(item.get("payload"), dict)
            and item["payload"].get("sessionId") == context.dsh_session_id
            and isinstance(item["payload"].get("event"), dict)
        ):
            events.append(item)
    kinds = {"step/start", "step/end", "turn/start", "turn/end"}
    actual = [item for item in events if item["payload"]["event"].get("type") in kinds]
    if observation_sha(actual) == observation_sha(complete):
        return True  # Preserve the previously supported exact full projection.
    prompt = session.get(
        DshSessionPromptRecord, (context.run_id, context.generation), populate_existing=True
    )
    if (
        prompt is None
        or prompt.dsh_session_id != context.dsh_session_id
        or prompt.request_hash != context.first_linked_request_hash
        or sha(prompt.prompt) != context.prompt_hash
    ):
        return False
    anchors = []
    for item in events:
        native = item["payload"]["event"]
        data = native.get("data")
        source = data.get("source") if isinstance(data, dict) else None
        if (
            native.get("type") == "user/message"
            and isinstance(source, dict)
            and source.get("kind") == "user"
        ):
            # A different or repeated user command cannot share this census cut.
            if source.get("rpcId") != prompt.request_id:
                return False
            anchors.append(item)
    if len(anchors) != 1:
        return False
    anchor = anchors[0]["payload"]["event"]
    seq, content = anchor.get("seq"), anchor["data"].get("content")
    if type(seq) is not int or seq < 0 or not isinstance(content, list) or content != [
        {"type": "text", "text": prompt.prompt}
    ]:
        return False
    # Strict sequence/body comparison also rejects duplicates or any omitted
    # post-RPC control. Prefix controls still remain in the authenticated census.
    retained = [item for item in complete if item["payload"]["event"]["seq"] > seq]
    if not retained or any(
        type(item["payload"]["event"].get("seq")) is not int
        or item["payload"]["event"]["seq"] <= seq
        for item in actual
    ):
        return False
    return observation_sha(actual) == observation_sha(retained)


@borrowed_provider_read
def read_provider_run_usage_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    as_of: datetime,
    require_native_census: bool = True,
) -> ProviderConversationUsageCensusV1:
    scope = _USAGE_READ_SCOPE.get()
    if scope is None:
        return _read_provider_run_usage_original_in_session(
            session, mapped_ref=mapped_ref, as_of=as_of,
            require_native_census=require_native_census,
        )
    if session is not scope.session or scope.owner != _usage_owner():
        token = _USAGE_READ_SCOPE.set(None)
        try:
            return _read_provider_run_usage_original_in_session(
                session, mapped_ref=mapped_ref, as_of=as_of,
                require_native_census=require_native_census,
            )
        finally:
            _USAGE_READ_SCOPE.reset(token)
    scope.require_unchanged()
    at = aware_utc(as_of)
    key = (
        observation_json(mapped_ref.model_dump(mode="json")), at.isoformat(), require_native_census
    )
    saved = scope.values.get(key)
    if saved is None:
        saved = _read_usage_material(session, mapped_ref, at, require_native_census)
        material = json.loads(saved)
        # Missing authenticated terminal never admits reuse of its authority.
        # Bounds only limit retained pure material; excess takes the full path.
        if material["terminal_authenticated"] and len(scope.values) < 8 and (
            sum(len(value.encode("utf-8")) for value in scope.values.values())
            + len(saved.encode("utf-8")) <= 1_048_576
        ):
            scope.values[key] = saved
    else:
        # Test marker continuity before returning any reused material. Releasing
        # and renewing our readonly marker cannot hide an earlier native TX cut.
        native = cast(sqlite3.Connection, scope.boundary.driver)
        try:
            native.execute(f"RELEASE SAVEPOINT {scope.marker}")
            native.execute(f"SAVEPOINT {scope.marker}")
        except sqlite3.Error as exc:
            raise ValueError("research_usage_read_scope_changed") from exc
        material = json.loads(saved)
        census = ProviderConversationUsageCensusV1.model_validate_json(
            observation_json(material["census"])
        )
        if _fresh_usage_witness(session, census) != material["witness"]:
            raise ValueError("research_usage_frozen_original_changed")
        # This is a fresh authority read: original Task, Method, policy, Source
        # and generation checks are never answered by the pure material memo.
        terminal = read_authenticated_terminal_in_session(session, census.run_id, now=at)
        refs = {ref.output_id: ref for ref in census.source_refs}
        if terminal is None or terminal.receipt_ref not in refs or (
            refs[terminal.receipt_ref].payload_hash != terminal.receipt_hash
        ):
            raise ValueError("research_usage_terminal_original_changed")
    scope.require_unchanged()
    return ProviderConversationUsageCensusV1.model_validate_json(
        observation_json(material["census"])
    )


def _fresh_usage_witness(
    session: Session, census: ProviderConversationUsageCensusV1
) -> dict[str, Any]:
    for ref in census.source_refs:
        row = session.get(RunRoleOutputRecord, ref.output_id, populate_existing=True)
        if row is None or row.run_id != census.run_id or row_reference(row) != ref:
            raise ValueError("research_usage_frozen_original_changed")
    prompts = list(session.scalars(select(DshSessionPromptRecord).where(
        DshSessionPromptRecord.run_id == census.run_id,
    ).order_by(DshSessionPromptRecord.generation).execution_options(populate_existing=True)))
    bindings = list(session.scalars(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == census.run_id,
        (RunRoleOutputRecord.role == ROLE) | RunRoleOutputRecord.role.startswith(CHILD_ROLE),
    ).order_by(RunRoleOutputRecord.output_id).execution_options(populate_existing=True)))
    native = list(session.scalars(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == census.run_id,
        RunRoleOutputRecord.role.startswith("native_conversation_census:"),
    ).order_by(RunRoleOutputRecord.output_id).execution_options(populate_existing=True)))
    return {
        "provider_rows": [row_reference(row).model_dump(mode="json") for row in
                          _rows(session, census.run_id, census.as_of)],
        "binding_rows": [row_reference(row).model_dump(mode="json") for row in bindings],
        "native_census": [row_reference(row).model_dump(mode="json") for row in native],
        "prompts": [[prompt.run_id, prompt.generation, prompt.dsh_session_id,
                     prompt.request_id, prompt.request_hash, sha(prompt.prompt),
                     as_utc(prompt.created_at).isoformat()] for prompt in prompts],
    }


def _read_usage_material(
    session: Session, mapped_ref: ObservedExecutionRef, at: datetime, require_native: bool
) -> str:
    census = _read_provider_run_usage_original_in_session(
        session, mapped_ref=mapped_ref, as_of=at, require_native_census=require_native
    )
    return observation_json({
        "census": census.model_dump(mode="json"),
        "witness": _fresh_usage_witness(session, census),
        "terminal_authenticated": any(
            ref.schema_version == TERMINAL_SCHEMA for ref in census.source_refs
        ),
    })


@borrowed_provider_read
def _read_provider_run_usage_original_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    as_of: datetime,
    require_native_census: bool = True,
) -> ProviderConversationUsageCensusV1:
    """Authenticate every saved dispatch, plus an independent full-session census."""
    at = aware_utc(as_of)
    mapped = read_mapped_observation(session, mapped_ref)
    raw = read_transport_observation(session, mapped.mapping_input_ref)
    context, reply = mapped.context, raw.reply
    if (
        raw.context != context
        or not isinstance(reply, DshSessionResult)
        or mapped.recorded_at > at
        or raw.recorded_at > at
        or reply.run_id != context.run_id
        or reply.dsh_session_id != context.dsh_session_id
        or reply.generation != context.generation
        or sha(reply.final_response + "\0" + reply.events_json) != reply.result_hash
    ):
        raise ValueError("research_usage_transport_original_invalid")
    sources = {ref.output_id: ref for ref in (mapped_ref, mapped.mapping_input_ref)}

    def proof(ref: ObservedExecutionRef) -> None:
        prior = sources.setdefault(ref.output_id, ref)
        if prior != ref or ref.recorded_at > at:
            raise ValueError("research_usage_source_reference_invalid")

    rows = _rows(session, context.run_id, at)
    bindings = {}
    for row in rows:
        if row.role == ROLE or row.role.startswith(CHILD_ROLE):
            binding = ModelDispatchService._read(row)  # pyright: ignore[reportPrivateUsage]
            execution = binding.execution_session_id or binding.dsh_session_id
            key = (binding.generation, binding.dsh_session_id, execution)
            if key in bindings or binding.purpose != "conversation":
                raise ValueError("research_usage_binding_ambiguous")
            bindings[key] = binding
            proof(row_reference(row))
    # Root and every descendant must be independently bound; no aliasing or cycle.
    for key, binding in bindings.items():
        generation, root, execution = key
        visited = {execution}
        parent = binding.parent_session_id
        while execution != root:
            if not parent or parent in visited:
                raise ValueError("research_usage_binding_lineage_invalid")
            ancestor = bindings.get((generation, root, parent))
            if ancestor is None:
                raise ValueError("research_usage_binding_parent_missing")
            if (
                ancestor.request_hash != binding.request_hash
                or ancestor.plugin_build_hash != binding.plugin_build_hash
            ):
                raise ValueError("research_usage_binding_parent_changed")
            visited.add(parent)
            execution, parent = parent, ancestor.parent_session_id
    items: list[ProviderDispatchUsageV1] = []
    inputs: dict[str, ProviderInputOriginal] = {}
    for row in rows:
        if not row.role.startswith("model_input:"):
            continue
        original, input_ref = read_provider_input_in_session(
            session,
            ResearchProviderInputRef(output_id=row.output_id, payload_hash=row.payload_hash),
        )
        value = original.submit
        key = (value.generation, value.root_session_id, value.execution_session_id)
        binding = bindings.get(key)
        if binding is None or any(
            getattr(binding, field) != getattr(value, field)
            for field in (
                "request_hash",
                "plugin_build_hash",
                "provider",
                "model",
                "parent_session_id",
            )
        ):
            raise ValueError("research_usage_input_binding_invalid")
        inputs[input_ref.output_id] = original
        proof(input_ref)
        for ref in (*original.source_refs, original.model_binding_ref):
            proof(ref)
        finished = read_provider_finish_in_session(session, original)
        finish_ref, status, reasons, counters = None, None, [], (None, None, None)
        if finished is None:
            reasons.append("provider_dispatch_finish_missing")
        else:
            finish, finish_ref = finished
            proof(finish_ref)
            status = finish.submit.status
            if status != "completed":
                reasons.append("provider_dispatch_not_completed")
            actual = (finish.input_tokens, finish.output_tokens, finish.total_tokens)
            a, b, c = actual
            if a is None or b is None or c is None:
                reasons.append("provider_dispatch_usage_incomplete")
            elif c < a + b:
                raise ValueError("research_usage_dispatch_counter_invalid")
            if not reasons:
                counters = actual
        items.append(
            ProviderDispatchUsageV1(
                generation=value.generation,
                root_session_id=value.root_session_id,
                execution_session_id=value.execution_session_id,
                parent_session_id=value.parent_session_id,
                dispatch_id=value.dispatch_id,
                provider=value.provider,
                model=value.model,
                input_ref=input_ref,
                finish_ref=finish_ref,
                finish_status=status,
                usage_status="unknown" if reasons else "known",
                input_tokens=counters[0],
                output_tokens=counters[1],
                total_tokens=counters[2],
                missing_codes=tuple(reasons),
            )
        )
    for row in rows:
        if row.role.startswith("model_finish:"):
            value, ref = _read_row(session, row.output_id)
            if (
                not isinstance(value, ProviderFinishOriginal)
                or value.submit.input_ref.output_id not in inputs
            ):
                raise ValueError("research_usage_orphan_finish_invalid")
            proof(ref)
    # A fork, missing predecessor or unrecorded generation cannot become a total.
    groups = Counter(
        (item.generation, item.root_session_id, item.execution_session_id) for item in items
    )
    heads: Counter[tuple[int, str, str]] = Counter()
    successors: Counter[str] = Counter()
    for original in inputs.values():
        value = original.submit
        key = (value.generation, value.root_session_id, value.execution_session_id)
        if value.previous_input_ref is None:
            heads[key] += 1
        else:
            if value.previous_input_ref.output_id not in inputs:
                raise ValueError("research_usage_predecessor_missing")
            successors[value.previous_input_ref.output_id] += 1
    if any(count != 1 for count in heads.values()) or any(
        count > 1 for count in successors.values()
    ):
        raise ValueError("research_usage_dispatch_chain_invalid")
    if set(heads) != set(groups):
        raise ValueError("research_usage_dispatch_chain_incomplete")
    reasons, missing = [], set()
    terminal = read_authenticated_terminal_in_session(session, context.run_id, now=at)
    if terminal is None:
        reasons.append("provider_authenticated_terminal_missing")
    else:
        row = session.get(RunRoleOutputRecord, terminal.receipt_ref, populate_existing=True)
        if row is None or row.payload_hash != terminal.receipt_hash:
            raise ValueError("research_usage_terminal_original_changed")
        proof(row_reference(row))
        completion = terminal.completion
        if (
            completion.run_id != reply.run_id
            or completion.generation != reply.generation
            or completion.dsh_session_id != reply.dsh_session_id
            or completion.result_hash != reply.result_hash
            or completion.last_seq < reply.last_seq
            or (
                reply.finished_at > completion.completed_at
                if completion.terminal_status == "cancelled"
                else completion.completed_at != reply.finished_at
            )
            or reply.started_at > reply.finished_at
        ):
            raise ValueError("research_usage_terminal_result_changed")
        if completion.last_seq != reply.last_seq or completion.completed_at != reply.finished_at:
            # The authenticated terminal cursor includes drain events after the
            # immutable result snapshot. A cancellation can also follow that
            # snapshot's native turn/end. Neither original is rewritten.
            prompt = session.get(
                DshSessionPromptRecord, (context.run_id, context.generation),
                populate_existing=True,
            )
            trace = f"dsh://sessions/{reply.dsh_session_id}?last_seq={reply.last_seq}"
            if (
                prompt is None or prompt.dsh_session_id != reply.dsh_session_id
                or completion.result_ref != f"dsh-host://runs/{reply.run_id}/result"
                or completion.trace_ref != trace or reply.trace_ref != trace
            ):
                raise ValueError("research_usage_terminal_result_changed")
            try:
                validate_terminal_result_native_metadata(reply, prompt.request_id, completion)
            except ValueError as error:
                raise ValueError("research_usage_terminal_result_changed") from error
        if completion.terminal_status != "completed":
            reasons.append("provider_conversation_terminal_not_completed")
    prompts = list(
        session.scalars(
            select(DshSessionPromptRecord.generation).where(
                DshSessionPromptRecord.run_id == context.run_id,
            )
        )
    )
    if prompts != [context.generation] or any(key[0] != context.generation for key in bindings):
        reasons.append("provider_previous_generation_step_census_unavailable")
    steps = _steps(reply)
    # A separate authenticated Host lifecycle original supplies the descendant
    # denominator. Never infer its registered set from input/finish row counts.
    from .native_conversation_census import read_native_census_in_session, validate_census

    native_census = read_native_census_in_session(
        session,
        run_id=context.run_id,
        generation=context.generation,
        as_of=at,
    )
    if native_census is not None:
        original, reference = native_census
        proof(reference)
        for ref in (*original.source_refs, *original.model_binding_refs):
            proof(ref)
        if original.native_context != context:
            raise ValueError("research_usage_native_census_context_changed")
        counts, projections = validate_census(original.census)
        if not _root_projection_matches(
            session, context, reply, projections[context.dsh_session_id]
        ):
            reasons.append("provider_native_root_projection_unavailable")
            missing.add(context.dsh_session_id)
        registered = set(original.census.registered_session_ids)
        bound = {key[2] for key in bindings}
        declared, unresolved = _declared_children(reply)
        if declared - (registered & bound):
            missing.update(declared - (registered & bound))
            reasons.append("provider_native_delegation_census_mismatch")
        if unresolved:
            reasons.append("provider_native_delegation_census_unavailable")
        if registered != bound:
            missing.update(registered ^ bound)
            reasons.append("provider_native_registered_binding_census_mismatch")
        if original.census.registry_status != "complete":
            reasons.append("provider_native_lifecycle_census_incomplete")
            missing.update(
                item.session_id
                for item in original.census.sessions
                if item.capture_status != "complete"
            )
        if set(steps) - registered:
            raise ValueError("research_usage_unbound_native_session")
        steps = counts
    elif require_native_census:
        reasons.append("provider_native_lifecycle_census_missing")
        missing.update(key[2] for key in bindings)
        missing.add(context.dsh_session_id)
    expected_keys = set(bindings) | {
        (context.generation, context.dsh_session_id, context.dsh_session_id)
    }
    for key in expected_keys:
        generation, root, execution = key
        if generation != context.generation or root != context.dsh_session_id:
            continue
        if execution not in steps:
            missing.add(execution)
            reasons.append(
                "provider_child_step_census_unavailable"
                if execution != root
                else "provider_root_step_census_unavailable"
            )
        elif steps[execution] != groups[key]:
            missing.add(execution)
            reasons.append("provider_native_dispatch_census_mismatch")
        if key not in groups:
            missing.add(execution)
            reasons.append("provider_bound_session_input_missing")
    if set(steps) - {key[2] for key in expected_keys}:
        raise ValueError("research_usage_unbound_native_session")
    if any(item.usage_status == "unknown" for item in items):
        reasons.append("provider_dispatch_usage_incomplete")
    if not items:
        reasons.append("provider_conversation_inputs_missing")
    expected = (
        None
        if missing
        or any(
            "generation" in reason or "lifecycle" in reason or "delegation" in reason
            for reason in reasons
        )
        else sum(steps.values())
    )
    complete = not reasons
    reasons.append("provider_pricing_authority_unavailable")
    items.sort(
        key=lambda item: (
            item.generation,
            item.root_session_id,
            item.execution_session_id,
            item.dispatch_id,
        )
    )
    body = dict(
        schema_version="research-provider-conversation-usage.v1",
        run_id=context.run_id,
        as_of=at,
        scope="conversation_only",
        excluded_purposes=("compaction", "title"),
        coverage_status="complete" if complete else "incomplete",
        token_status="known" if complete else "unknown",
        cost_status="unknown",
        expected_dispatches=expected,
        observed_dispatches=len(items),
        root_dispatches=sum(item.execution_session_id == item.root_session_id for item in items),
        child_dispatches=sum(item.execution_session_id != item.root_session_id for item in items),
        covered_dispatches=sum(item.usage_status == "known" for item in items),
        input_tokens=sum(cast(int, item.input_tokens) for item in items) if complete else None,
        output_tokens=sum(cast(int, item.output_tokens) for item in items) if complete else None,
        total_tokens=sum(cast(int, item.total_tokens) for item in items) if complete else None,
        cost_usd=None,
        missing_codes=tuple(dict.fromkeys(reasons)),
        missing_sessions=tuple(sorted(missing)),
        dispatches=tuple(items),
        source_refs=tuple(sources[key] for key in sorted(sources)),
        proof_scope="dsh_llm_generate_options",
        model_understanding_verified=False,
        budget_qualified=False,
        financial_qualified=False,
    )
    # Obtain the exact JSON datetime/ref representation before hashing.
    from pydantic import TypeAdapter

    canonical = TypeAdapter(dict).dump_python(body, mode="json")
    body["census_hash"] = observation_sha(canonical)
    return ProviderConversationUsageCensusV1.model_validate(body)


@borrowed_provider_read
def verify_provider_usage_census_in_session(
    session: Session,
    census: ProviderConversationUsageCensusV1,
    *,
    require_native_census: bool = True,
) -> None:
    """Read the frozen original refs, never backfill an older sidecar from latest."""
    for ref in census.source_refs:
        row = session.get(RunRoleOutputRecord, ref.output_id, populate_existing=True)
        if row is None or row.run_id != census.run_id or row_reference(row) != ref:
            raise ValueError("research_usage_frozen_original_changed")
    provider_refs = {
        ref.output_id: ref
        for ref in census.source_refs
        if ref.role.startswith(("model_input:", "model_finish:", CHILD_ROLE)) or ref.role == ROLE
    }
    actual = {
        row.output_id: row_reference(row) for row in _rows(session, census.run_id, census.as_of)
    }
    if provider_refs != actual:
        raise ValueError("research_usage_frozen_census_changed")
    mapped_refs = [
        ref for ref in census.source_refs if ref.schema_version == "research-mapped-observation.v1"
    ]
    if len(mapped_refs) != 1:
        raise ValueError("research_usage_frozen_mapping_ambiguous")
    actual_census = read_provider_run_usage_in_session(
        session,
        mapped_ref=mapped_refs[0],
        as_of=census.as_of,
        require_native_census=require_native_census,
    )
    if actual_census != census:
        raise ValueError("research_usage_frozen_computation_changed")


_LOADED_USAGE_FUNCTIONS = {
    name: (globals()[name], globals()[name].__code__,
           getattr(globals()[name], "__wrapped__", None),
           getattr(getattr(globals()[name], "__wrapped__", None), "__code__", None))
    for name in (
        "_usage_owner", "provider_usage_read_scope", "read_provider_run_usage_in_session",
        "_fresh_usage_witness", "_read_usage_material",
        "_read_provider_run_usage_original_in_session", "verify_provider_usage_census_in_session",
        "_steps", "_declared_children", "_root_projection_matches", "_rows",
        "validate_terminal_result_native_metadata",
    )
}
