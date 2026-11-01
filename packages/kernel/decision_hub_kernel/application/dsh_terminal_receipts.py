"""Original authenticated Host callbacks; transport observations grant no proof."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionCompletion,
    DshSessionResult,
    DshUpstreamIdentity,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
    read_execution_request,
    read_prompt_integrity,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    has_prepared_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context import build_task_policy
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)

SCHEMA = "dsh-host-terminal-receipt.v1"
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class AuthenticatedTerminalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["dsh-host-terminal-receipt.v1"]
    authentication_source: Literal["dsh_callback_secret"]
    run_id: str
    event_id: str
    request_id: str | None
    dsh_session_id: str
    generation: Annotated[int, Field(ge=1)]
    request_hash: Sha
    prompt_hash: Sha | None
    upstream_identity_hash: Sha
    completion: DshSessionCompletion
    recorded_at: AwareDatetime


@dataclass(frozen=True)
class VerifiedHostTerminal:
    receipt_ref: str
    receipt_hash: str
    prompt_hash: str
    task_request_hash: str
    task_policy_ref: str
    completion: DshSessionCompletion


@dataclass(frozen=True)
class AuthenticatedTerminalMetadata:
    """Original Host terminal identity for display, never an execution grant."""
    receipt_ref: str
    receipt_hash: str
    receipt: AuthenticatedTerminalReceipt


def read_authenticated_terminal_metadata_in_session(
    session: Session, run_id: str, *, now: datetime,
) -> AuthenticatedTerminalMetadata | None:
    """Reuse original producer/identity guards without Task/Graph execution proof.

    The actual authenticated writer alone creates the reserved terminal role.
    Missing/legacy rows remain unknown; this consumer never rewrites them.
    The full permission/recovery reader below remains unchanged.
    """
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if link is None:
        return None
    row = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id,
        RunRoleOutputRecord.role == terminal_receipt_role(link.generation),
    ).execution_options(populate_existing=True))
    if row is None:
        return None
    receipt = AuthenticatedTerminalReceipt.model_validate_json(row.payload_json)
    if receipt.request_id is None or receipt.prompt_hash is None:
        raise ValueError("dsh_terminal_original_prompt_missing")
    same_pointers = (
        link.trace_ref == receipt.completion.trace_ref
        and link.result_ref == receipt.completion.result_ref
        and link.result_hash == receipt.completion.result_hash
    )
    result_enrichment = (
        receipt.completion.terminal_status in {"failed", "cancelled"}
        and receipt.completion.result_ref is None and receipt.completion.result_hash is None
        and link.result_ref is not None and link.result_hash is not None
    )
    if not same_pointers and not result_enrichment:
        raise ValueError("dsh_terminal_original_result_drift")
    # Mirror the actual immutable-writer enrichment branch, never a historical
    # Link. The original receipt remains the only displayed cause identity.
    current_callback = DshSessionCompletion.model_validate_json(receipt.completion.model_copy(update={
        "trace_ref": link.trace_ref, "result_ref": link.result_ref, "result_hash": link.result_hash,
    }).model_dump_json())
    expected = _body(session, link, current_callback, receipt.recorded_at)
    expected = expected.model_copy(update={"completion": receipt.completion})
    encoded = canonical_recipe(expected.model_dump(mode="json"))
    if (
        row.output_id != _receipt_id(run_id, link.generation) or row.schema_version != SCHEMA
        or row.payload_json != encoded
        or row.payload_hash != hashlib.sha256(encoded.encode()).hexdigest()
        or _time(row.created_at) != receipt.recorded_at or receipt.recorded_at > _time(now)
    ):
        raise ValueError("dsh_authenticated_terminal_invalid")
    return AuthenticatedTerminalMetadata(row.output_id, row.payload_hash, receipt)


def terminal_receipt_role(generation: int) -> str:
    return f"dsh_authenticated_terminal:{generation}"


def _receipt_id(run_id: str, generation: int) -> str:
    return "host_terminal_" + hashlib.sha256(f"{run_id}\0{generation}".encode()).hexdigest()


def _time(value: datetime | None) -> datetime:
    current = as_utc(value)
    if current is None:
        raise ValueError("dsh_terminal_receipt_time_missing")
    return current


def _body(
    session: Session, link: DshSessionLinkRecord,
    callback: DshSessionCompletion, recorded_at: datetime,
) -> AuthenticatedTerminalReceipt:
    run = session.get(RunRecord, link.run_id, populate_existing=True)
    prompt = session.get(
        DshSessionPromptRecord, (link.run_id, callback.generation), populate_existing=True,
    )
    if run is None:
        raise ValueError("dsh_terminal_original_run_missing")
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        link.run_id, link.request_hash, callback.generation,
    )
    upstream = DshUpstreamIdentity.model_validate_json(link.upstream_identity_json)
    if (
        callback.run_id != link.run_id or callback.generation != link.generation
        or callback.dsh_session_id != session_id or link.dsh_session_id != session_id
        or (prompt is not None and (
            prompt.dsh_session_id != session_id or prompt.request_id != request_id
            or prompt.request_hash != link.request_hash
        ))
        or link.plugin_build_hash != upstream.plugin_build_hash
        or link.state != callback.terminal_status or link.last_seq != callback.last_seq
        or link.result_ref != callback.result_ref or link.result_hash != callback.result_hash
        or link.trace_ref != callback.trace_ref
        or link.error_code != (callback.error.code if callback.error else None)
        or _time(link.terminal_at) != callback.completed_at
        or (prompt is not None and not (
            _time(link.created_at) <= _time(prompt.created_at) <= callback.completed_at
        ))
        or callback.completed_at > _time(recorded_at)
    ):
        raise ValueError("dsh_terminal_original_identity_invalid")
    return AuthenticatedTerminalReceipt(
        schema_version=SCHEMA, authentication_source="dsh_callback_secret",
        run_id=run.run_id, event_id=run.event_id,
        request_id=request_id if prompt is not None else None,
        dsh_session_id=session_id, generation=callback.generation,
        request_hash=link.request_hash,
        prompt_hash=hashlib.sha256(prompt.prompt.encode("utf-8")).hexdigest() if prompt else None,
        upstream_identity_hash=hashlib.sha256(link.upstream_identity_json.encode()).hexdigest(),
        completion=callback, recorded_at=_time(recorded_at),
    )


def freeze_authenticated_terminal_in_session(
    session: Session, link: DshSessionLinkRecord,
    callback: DshSessionCompletion, *, recorded_at: datetime,
) -> None:
    """Only the authenticated callback writer calls this in Link.complete's transaction."""
    role = terminal_receipt_role(callback.generation)
    existing = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == link.run_id, RunRoleOutputRecord.role == role,
    ).execution_options(populate_existing=True))
    output_id = _receipt_id(link.run_id, callback.generation)
    if existing is not None:
        original = AuthenticatedTerminalReceipt.model_validate_json(existing.payload_json)
        encoded = canonical_recipe(original.model_dump(mode="json"))
        if (
            existing.output_id != output_id or existing.schema_version != SCHEMA
            or existing.payload_json != encoded
            or existing.payload_hash != hashlib.sha256(encoded.encode()).hexdigest()
            or _time(existing.created_at) != original.recorded_at
        ):
            raise ValueError("dsh_authenticated_terminal_conflict")
        same = original.completion == callback
        enrichment = (
            original.completion.terminal_status in {"failed", "cancelled"}
            and original.completion.result_ref is None and original.completion.result_hash is None
            and callback.result_ref is not None and callback.result_hash is not None
            and callback.model_copy(update={
                "trace_ref": original.completion.trace_ref,
                "result_ref": None, "result_hash": None,
            }) == original.completion
        )
        if not same and not enrichment:
            raise ValueError("dsh_authenticated_terminal_conflict")
        current_callback = original.completion.model_copy(update={
            "trace_ref": link.trace_ref, "result_ref": link.result_ref,
            "result_hash": link.result_hash,
        })
        expected = _body(session, link, current_callback, original.recorded_at)
        expected = expected.model_copy(update={"completion": original.completion})
        if expected != original or original.recorded_at > _time(recorded_at):
            raise ValueError("dsh_authenticated_terminal_conflict")
        # A partial result discovered later must not rewrite the original proof.
        # Recovery separately rejects Link result pointers and requires this
        # original receipt to still agree with the current terminal state.
        return
    body = _body(session, link, callback, _time(recorded_at))
    encoded = canonical_recipe(body.model_dump(mode="json"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    session.add(RunRoleOutputRecord(
        output_id=output_id, run_id=link.run_id, role=role, schema_version=SCHEMA,
        payload_json=encoded, payload_hash=digest, created_at=body.recorded_at,
    ))


def read_authenticated_terminal_in_session(
    session: Session, run_id: str, *, now: datetime,
) -> VerifiedHostTerminal | None:
    """Revalidate original receipt, Task, every generation and execution policy.

    Missing legacy receipts remain unknown. This reader never certifies a Link
    from transport strings and never writes, commits or calls the Host.
    """
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if link is None:
        return None
    row = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id,
        RunRoleOutputRecord.role == terminal_receipt_role(link.generation),
    ).execution_options(populate_existing=True))
    if row is None:
        return None
    receipt = AuthenticatedTerminalReceipt.model_validate_json(row.payload_json)
    if receipt.request_id is None or receipt.prompt_hash is None:
        raise ValueError("dsh_terminal_original_prompt_missing")
    expected = _body(session, link, receipt.completion, receipt.recorded_at)
    encoded = canonical_recipe(expected.model_dump(mode="json"))
    if (
        row.output_id != _receipt_id(run_id, link.generation) or row.schema_version != SCHEMA
        or row.payload_json != encoded
        or row.payload_hash != hashlib.sha256(encoded.encode()).hexdigest()
        or _time(row.created_at) != receipt.recorded_at or receipt.recorded_at > _time(now)
    ):
        raise ValueError("dsh_authenticated_terminal_invalid")
    run = session.get(RunRecord, run_id, populate_existing=True)
    assert run is not None
    saved = read_task_request(session, run, assessed_at=receipt.completion.completed_at)
    if saved is None:
        raise ValueError("dsh_terminal_original_task_missing")
    prompts = session.scalars(select(DshSessionPromptRecord).where(
        DshSessionPromptRecord.run_id == run_id,
    ).order_by(DshSessionPromptRecord.generation).execution_options(populate_existing=True)).all()
    if [prompt.generation for prompt in prompts] != list(range(1, link.generation + 1)):
        raise ValueError("dsh_terminal_generation_incomplete")
    execution: ResearchSessionRequest | None = None
    if has_prepared_bundle_in_session(session, run_id):
        prepared = read_prepared_execution_in_session(session, run_id, link.generation)
        execution = ResearchSessionRequest.model_validate(
            prepared.execution.request.model_dump(mode="json"),
        )
        if prepared.integrity.prompt_hash != receipt.prompt_hash:
            raise ValueError("dsh_terminal_execution_prompt_invalid")
    else:
        for prompt in prompts:
            if _time(prompt.created_at) > receipt.completion.completed_at:
                raise ValueError("dsh_terminal_prompt_time_invalid")
            session_id, request_id = DshSessionLinkService.deterministic_ids(
                run_id, link.request_hash, prompt.generation,
            )
            if (prompt.dsh_session_id, prompt.request_id, prompt.request_hash) != (
                session_id, request_id, link.request_hash,
            ):
                raise ValueError("dsh_terminal_prompt_identity_invalid")
            read_prompt_integrity(session, prompt)
            execution = read_execution_request(session, prompt, saved[1])
            if execution.event_id != run.event_id or (
                prompt.generation == 1 and research_request_hash(execution) != link.request_hash
            ):
                raise ValueError("dsh_terminal_execution_identity_invalid")
    if execution is None or execution.event_id != run.event_id:
        raise ValueError("dsh_terminal_execution_missing")
    policy = build_task_policy(
        research_mode=execution.research_mode or "current_decision",
        requirements=execution.evidence_requirements,
        created_at=_time(prompts[-1].created_at),
        max_evidence_rounds=execution.execution_budget.max_evidence_rounds,
        context_inputs=execution.input_evidence,
    )
    policy_ref = f"{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}"
    if execution.task_policy_ref != policy_ref:
        raise ValueError("dsh_terminal_task_policy_invalid")
    return VerifiedHostTerminal(
        row.output_id, row.payload_hash, receipt.prompt_hash, saved[1], policy_ref,
        receipt.completion,
    )


def validate_terminal_result_native_metadata(
    result: DshSessionResult, request_id: str, original: DshSessionCompletion,
) -> None:
    """Meta fields are not in result_hash; derive them from its native events."""
    def invalid() -> ValueError:
        return ValueError("dsh_terminal_native_metadata_mismatch")

    def time(value: object) -> datetime:
        if type(value) not in {int, float} or not math.isfinite(cast(float, value)):
            raise invalid()
        try:
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=cast(float, value))
        except (OverflowError, ValueError) as error:
            raise invalid() from error

    try:
        notifications = json.loads(result.events_json)
    except (ValueError, TypeError) as error:
        raise invalid() from error
    if not isinstance(notifications, list):
        raise invalid()
    events: list[dict[str, object]] = []
    sequence = -1
    for row in notifications:
        if not isinstance(row, dict) or row.get("method") != "session.event":
            raise invalid()
        payload = row.get("payload")
        event = payload.get("event") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("sessionId") != result.dsh_session_id
            or not isinstance(event, dict) or type(event.get("seq")) is not int
            or not sequence < event["seq"] <= result.last_seq
            or not isinstance(event.get("data"), dict)
        ):
            raise invalid()
        sequence = event["seq"]
        events.append(event)
    prompt: dict[str, object] | None = None
    ends: list[dict[str, object]] = []
    for event in events:
        data = cast(dict[str, object], event["data"])
        source = data.get("source")
        if (
            event.get("type") == "user/message"
            and isinstance(source, dict) and source.get("kind") == "user"
        ):
            if source.get("rpcId") == request_id and prompt is None:
                prompt = event
            elif prompt is not None and source.get("rpcId") != request_id:
                break
        elif prompt is not None and event.get("type") == "turn/end":
            ends.append(event)
    if prompt is None or not ends:
        raise invalid()
    end = ends[-1]
    reason = cast(dict[str, object], end["data"]).get("reason")
    kind = reason.get("kind", "unknown") if isinstance(reason, dict) else "unknown"
    if not isinstance(kind, str):
        raise invalid()
    expected = (
        "error" if original.error is not None and original.error.code == "dsh_model_step_timeout"
        else "completed" if original.terminal_status == "completed" and kind in {"aborted", "error"}
        else kind
    )
    if (
        result.started_at != time(prompt.get("time"))
        or result.finished_at != time(end.get("time"))
        or result.finish_reason != expected
    ):
        raise invalid()
