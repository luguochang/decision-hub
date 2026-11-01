"""Harness-only MethodV4 sidecar and root model-input consumption originals.

This does not upgrade MethodV1-3, attest child enforcement, or qualify deployment.
Native callback authentication belongs to the API; user approval is separate.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle, read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_harness_consumption import (
    CONSUMPTION_ROLE, read_harness_consumption_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database, DshSessionLinkRecord, RunRecord, RunRoleOutputRecord, as_utc, utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    begin_immediate, prepare_originals,
)

if TYPE_CHECKING:
    from .research_execution_binding import FrozenExecutionPreparation


METHOD_ROLE = "research_harness_execution_method"
METHOD_SCHEMA = "research-harness-execution-method.v4"
WITNESS_SCHEMA = "research-harness-root-consumption.v1"
Hash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Id = Annotated[StrictStr, Field(min_length=1, max_length=256)]
MAX_INPUT_BYTES = 1_000_000


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class HarnessMethodV4(Closed):
    schema_version: Literal["research-harness-execution-method.v4"]
    run_id: Id
    event_id: Id
    method_v3_payload_hash: Hash
    consumption_payload_hash: Hash
    artifact_bytes_sha256: Hash
    pack_definition_hash: Hash
    full_task_hash: Hash
    compiled_policy_hash: Hash
    plugin_build_hash: Hash
    proof_scope: Literal["root_model_request_consumption"]
    native_execution_proof: Literal["unverified"]
    eligible_for_deployment: Literal[False]
    recorded_at: AwareDatetime


class HostScope(Closed):
    host_instance_id: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")]
    process_id: Annotated[StrictInt, Field(ge=1)]
    started_at: AwareDatetime  # Bridge creation time; never OS process start ticks.
    hub_base_url: Annotated[StrictStr, Field(min_length=1, max_length=2048)]
    workspace_id: Id
    workspace_cwd_hash: Hash

    @field_validator("started_at", mode="before")
    @classmethod
    def _iso_started_at(cls, value: object) -> object:
        # FastAPI validates decoded JSON in Python mode. Convert only this
        # wire timestamp; the remaining fields keep the closed strict boundary.
        if isinstance(value, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value) is None:
                raise ValueError("harness_host_time_iso_required")
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class RootConsumption(Closed):
    schema_version: Literal["research-harness-root-consumption.v1"]
    run_id: Id
    dsh_session_id: Id
    request_id: Id
    request_hash: Hash
    generation: Annotated[StrictInt, Field(ge=1)]
    plugin_build_hash: Hash
    method_v4_payload_hash: Hash
    prompt_hash: Hash
    host_scope: HostScope
    execution_session_id: Id
    model_input_json: Annotated[StrictStr, Field(min_length=1, max_length=MAX_INPUT_BYTES)]
    model_input_sha256: Hash
    proof_scope: Literal["root_model_request_consumption"]
    eligible_for_deployment: Literal[False]


class RecordedRootConsumption(Closed):
    payload: RootConsumption
    recorded_at: AwareDatetime


class WitnessSummary(Closed):
    payload_hash: Hash
    host_scope: HostScope


class HarnessExecutionView(Closed):
    schema_version: Literal["research-harness-execution-view.v1"]
    method: HarnessMethodV4
    method_payload_hash: Hash
    run_id: Id
    dsh_session_id: Id
    request_id: Id
    request_hash: Hash
    generation: Annotated[StrictInt, Field(ge=1)]
    plugin_build_hash: Hash
    prompt: StrictStr
    prompt_hash: Hash
    witness: WitnessSummary | None


def _row(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    row = session.scalar(select(RunRoleOutputRecord).filter_by(run_id=run_id, role=role)
                         .execution_options(populate_existing=True))
    if row is not None and row.payload_hash != sha(row.payload_json):
        raise ValueError("harness_execution_original_corrupt")
    return row


def _method_body(session: Session, bundle: FrozenResearchBundle, recorded_at: datetime) -> HarnessMethodV4 | None:
    from .research_harness_frozen_run import read_any_consumption
    consumed = read_any_consumption(session, bundle)
    if consumed is None:
        return None
    method = _row(session, bundle.pack.run_id, "research_execution_method")
    consumption = _row(session, bundle.pack.run_id, getattr(consumed, "consumption_role", CONSUMPTION_ROLE))
    if method is None or consumption is None or method.schema_version != "research-execution-method.v3":
        raise ValueError("harness_method_v3_original_required")
    return encode_harness_method(bundle, consumed, method.payload_json, method.payload_hash,
                                 consumption.payload_hash, recorded_at)


def encode_harness_method(
    bundle: FrozenResearchBundle, consumed, method_json: str, method_hash: str,
    consumption_hash: str, recorded_at: datetime,
) -> HarnessMethodV4:
    """Pure V4 encoder over already authenticated consumption and canonical MethodV3."""
    if sha(method_json) != method_hash:
        raise ValueError("harness_method_v3_original_required")
    body = json.loads(method_json)
    installed = consumed.selection.candidate.installed_runtime_binding
    plugin = body["definition"]["upstream_identity"]["plugin_build_hash"]
    if installed.plugin_build_hash != plugin or installed.runtime_id != "dsh":
        raise ValueError("harness_installed_method_identity_mismatch")
    return HarnessMethodV4(
        schema_version=METHOD_SCHEMA, run_id=bundle.pack.run_id, event_id=bundle.compiled.event_id,
        method_v3_payload_hash=method_hash, consumption_payload_hash=consumption_hash,
        artifact_bytes_sha256=consumed.selection.candidate.artifact_index.artifact_bytes_sha256,
        pack_definition_hash=consumed.pack_definition_hash, full_task_hash=consumed.full_task_hash,
        compiled_policy_hash=consumed.compiled_policy_hash, plugin_build_hash=plugin,
        proof_scope="root_model_request_consumption", native_execution_proof="unverified",
        eligible_for_deployment=False, recorded_at=recorded_at,
    )



def prepare_harness_method_in_session(
    session: Session, bundle: FrozenResearchBundle, method: RunRoleOutputRecord,
    *, first_method: bool, recorded_at: datetime,
) -> RunRoleOutputRecord | None:
    """RO originals/absence, including a first MethodV3 not yet published."""
    if not first_method:
        verify_harness_method_in_session(session, bundle)
        return None
    from .research_harness_frozen_run import read_any_consumption

    consumed = read_any_consumption(session, bundle)
    existing = _row(session, bundle.pack.run_id, METHOD_ROLE)
    if existing is not None:
        raise ValueError("harness_method_v4_backfill_forbidden")
    if consumed is None:
        return None
    consumption = _row(session, bundle.pack.run_id, getattr(consumed, "consumption_role", CONSUMPTION_ROLE))
    if method.schema_version != "research-execution-method.v3" or consumption is None:
        raise ValueError("harness_method_v3_original_required")
    receipt = encode_harness_method(bundle, consumed, method.payload_json, method.payload_hash,
                                    consumption.payload_hash, recorded_at)
    raw = canonical_recipe(receipt.model_dump(mode="json"))
    return RunRoleOutputRecord(output_id="hmethod4_" + sha(receipt.run_id), run_id=receipt.run_id,
        role=METHOD_ROLE, schema_version=METHOD_SCHEMA, payload_json=raw, payload_hash=sha(raw),
        created_at=recorded_at)


def verify_harness_method_in_session(session: Session, bundle: FrozenResearchBundle) -> HarnessMethodV4 | None:
    row = _row(session, bundle.pack.run_id, METHOD_ROLE)
    if row is None:
        from .research_harness_frozen_run import read_any_consumption
        if read_any_consumption(session, bundle) is not None:
            raise ValueError("harness_method_v4_missing")
        return None
    if row.schema_version != METHOD_SCHEMA:
        raise ValueError("harness_method_v4_corrupt")
    receipt = HarnessMethodV4.model_validate_json(row.payload_json)
    if (receipt != _method_body(session, bundle, receipt.recorded_at)
            or as_utc(row.created_at) != as_utc(receipt.recorded_at)):
        raise ValueError("harness_method_v4_originals_changed")
    return receipt


def freeze_harness_method_in_session(session: Session, bundle: FrozenResearchBundle, *, first_method: bool, recorded_at: datetime) -> None:
    if not first_method:
        verify_harness_method_in_session(session, bundle)
        return
    receipt = _method_body(session, bundle, recorded_at)
    if receipt is None:
        if _row(session, bundle.pack.run_id, METHOD_ROLE) is not None:
            raise ValueError("harness_method_without_consumption")
        return
    if _row(session, receipt.run_id, METHOD_ROLE) is not None:
        raise ValueError("harness_method_v4_backfill_forbidden")
    # The existing preparation service owns this physical writer and rollback.
    from .research_execution_binding import _require_transaction  # pyright: ignore[reportPrivateUsage]
    _require_transaction(session)
    raw = canonical_recipe(receipt.model_dump(mode="json"))
    session.add(RunRoleOutputRecord(output_id="hmethod4_" + sha(receipt.run_id),
        run_id=receipt.run_id, role=METHOD_ROLE, schema_version=METHOD_SCHEMA,
        payload_json=raw, payload_hash=sha(raw), created_at=recorded_at))
    session.flush()
    verify_harness_method_in_session(session, bundle)


def harness_required_in_session(session: Session, run_id: str) -> bool:
    # A hint only, never execution authorization. Presence forces fail-closed validation.
    return session.scalar(select(RunRoleOutputRecord.output_id).filter_by(
        run_id=run_id).where(RunRoleOutputRecord.role.in_((CONSUMPTION_ROLE, "research_harness_frozen_run_consumption"))).limit(1)) is not None


def _scope_key(scope: HostScope) -> str:
    return sha(canonical_recipe(scope.model_dump(mode="json")))[:24]


def _witness_role(generation: int, scope: HostScope) -> str:
    return f"harness_root:{generation}:{_scope_key(scope)}"


def _fence(session: Session, run_id: str, now: datetime) -> DshSessionLinkRecord:
    run = session.get(RunRecord, run_id, populate_existing=True)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if run is None or link is None:
        raise KeyError(run_id)
    lease_expires_at = as_utc(run.lease_expires_at)
    deadline_at = as_utc(link.deadline_at)
    if (run.status != "running" or link.state not in {"admitted", "running", "idle"}
            or link.terminal_at is not None or not run.lease_owner
            or lease_expires_at is None or lease_expires_at <= as_utc(now)
            or deadline_at is None or deadline_at <= as_utc(now)):
        raise ValueError("harness_execution_permission_lost")
    from .research_graph_binding import _permission  # pyright: ignore[reportPrivateUsage]
    _permission(session, run_id, run.lease_owner)
    return link


def _current_view(session: Session, run_id: str, generation: int, scope: HostScope | None, now: datetime, *, prepared: FrozenExecutionPreparation | None = None) -> HarnessExecutionView:
    from .research_execution_binding import read_prepared_execution_in_session
    link = _fence(session, run_id, now)
    if link.generation != generation:
        raise ValueError("harness_execution_generation_changed")
    # Existing native full proof remains authoritative, including current Selection/C/RPC.
    prepared = prepared or read_prepared_execution_in_session(session, run_id, generation)
    if prepared.execution.generation != generation or prepared.method.run_id != run_id:
        raise ValueError("harness_execution_generation_changed")
    bundle = read_research_bundle_in_session(session, run_id)
    if not isinstance(bundle, FrozenResearchBundle):
        raise ValueError("harness_bundle_required")
    method = verify_harness_method_in_session(session, bundle)
    method_row = _row(session, run_id, METHOD_ROLE)
    if method is None or method_row is None:
        raise ValueError("harness_method_v4_required")
    original = None if scope is None else _row(session, run_id, _witness_role(generation, scope))
    witness = None
    if original is not None:
        recorded = RecordedRootConsumption.model_validate_json(original.payload_json)
        if original.schema_version != WITNESS_SCHEMA or as_utc(original.created_at) != as_utc(recorded.recorded_at):
            raise ValueError("harness_root_witness_corrupt")
        witness = WitnessSummary(payload_hash=original.payload_hash, host_scope=recorded.payload.host_scope)
    return HarnessExecutionView(schema_version="research-harness-execution-view.v1", method=method,
        method_payload_hash=method_row.payload_hash, run_id=run_id, dsh_session_id=link.dsh_session_id,
        request_id=prepared.prompt.request_id, request_hash=link.request_hash, generation=generation,
        plugin_build_hash=link.plugin_build_hash, prompt=prepared.prompt.prompt,
        prompt_hash=sha(prepared.prompt.prompt), witness=witness)


def validate_root_input(payload: RootConsumption, view: HarnessExecutionView) -> None:
    if (payload.run_id != view.run_id or payload.dsh_session_id != view.dsh_session_id
            or payload.execution_session_id != view.dsh_session_id or payload.request_id != view.request_id
            or payload.request_hash != view.request_hash or payload.generation != view.generation
            or payload.plugin_build_hash != view.plugin_build_hash
            or payload.method_v4_payload_hash != view.method_payload_hash or payload.prompt_hash != view.prompt_hash
            or len(payload.model_input_json.encode("utf-8")) > MAX_INPUT_BYTES
            or sha(payload.model_input_json) != payload.model_input_sha256):
        raise ValueError("harness_root_witness_identity_mismatch")
    inputs = json.loads(payload.model_input_json)
    if not isinstance(inputs, dict) or set(inputs) != {"messages", "system", "tools"}:
        raise ValueError("harness_actual_model_input_invalid")
    messages = inputs["messages"]
    if (not isinstance(messages, list) or not isinstance(inputs["tools"], list)
            or inputs["system"] is not None and not isinstance(inputs["system"], str)):
        raise ValueError("harness_actual_model_input_invalid")
    # Require the whole exact original prompt as a real user text block, never prose/queued flags.
    matched = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"
        and isinstance(m.get("source"), dict) and m["source"].get("kind") == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "text" and b.get("text") == view.prompt
                for b in m["content"])]
    if len(matched) != 1:
        raise ValueError("harness_full_prompt_not_consumed")


def require_harness_root_witness_in_session(session: Session, run_id: str, witness_hash: str | None, *, prepared: FrozenExecutionPreparation | None = None) -> None:
    if not harness_required_in_session(session, run_id):
        if witness_hash is not None:
            raise ValueError("harness_witness_without_consumption")
        return
    if witness_hash is None or len(witness_hash) != 64:
        raise ValueError("harness_root_witness_required")
    row = session.scalar(select(RunRoleOutputRecord).filter_by(run_id=run_id, payload_hash=witness_hash)
        .where(RunRoleOutputRecord.role.startswith("harness_root:")))
    if row is None or row.schema_version != WITNESS_SCHEMA or sha(row.payload_json) != witness_hash:
        raise ValueError("harness_root_witness_required")
    saved = RecordedRootConsumption.model_validate_json(row.payload_json)
    if row.role != _witness_role(saved.payload.generation, saved.payload.host_scope):
        raise ValueError("harness_root_witness_corrupt")
    view = _current_view(session, run_id, saved.payload.generation, saved.payload.host_scope, utcnow(), prepared=prepared)
    validate_root_input(saved.payload, view)


class HarnessExecutionService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def view(self, run_id: str, generation: int, scope: HostScope) -> HarnessExecutionView:
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN")
            return _current_view(session, run_id, generation, scope, utcnow())

    def record(self, payload: RootConsumption) -> WitnessSummary:
        payload = RootConsumption.model_validate_json(payload.model_dump_json())
        def original(session: Session) -> tuple[HarnessExecutionView, str]:
            view = _current_view(session, payload.run_id, payload.generation, payload.host_scope, utcnow())
            validate_root_input(payload, view)
            run = session.get(RunRecord, payload.run_id)
            assert run is not None and run.lease_owner is not None
            return view, run.lease_owner
        prepared = prepare_originals(self.database, original, link_activity_observations=True,
                                     link_tool_counter_observations=True)
        with self.database.session() as session:
            begin_immediate(session)
            run_link = _fence(session, payload.run_id, utcnow())
            from .research_graph_binding import _fence as fence_writer_permission  # pyright: ignore[reportPrivateUsage]
            fence_writer_permission(session, payload.run_id, prepared.value[1])
            prepared.verify(session)
            role = _witness_role(payload.generation, payload.host_scope)
            old = _row(session, payload.run_id, role)
            if old is not None:
                saved = RecordedRootConsumption.model_validate_json(old.payload_json)
                if saved.payload != payload:
                    raise ValueError("harness_root_witness_conflict")
                prepared.verify_after_writes(session)
                fence_writer_permission(session, payload.run_id, prepared.value[1])
                if run_link.generation != payload.generation:
                    raise ValueError("harness_execution_generation_changed")
                return WitnessSummary(payload_hash=old.payload_hash, host_scope=payload.host_scope)
            now = utcnow()
            receipt = RecordedRootConsumption(payload=payload, recorded_at=now)
            raw = canonical_recipe(receipt.model_dump(mode="json"))
            session.add(RunRoleOutputRecord(output_id="hroot_" + sha(payload.run_id + "\0" + role),
                run_id=payload.run_id, role=role, schema_version=WITNESS_SCHEMA,
                payload_json=raw, payload_hash=sha(raw), created_at=now))
            session.flush()
            prepared.verify_after_writes(session)
            _fence(session, payload.run_id, utcnow())
            fence_writer_permission(session, payload.run_id, prepared.value[1])
            return WitnessSummary(payload_hash=sha(raw), host_scope=payload.host_scope)
