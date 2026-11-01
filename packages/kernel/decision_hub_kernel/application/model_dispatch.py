"""Bind root and descendant conversation controls before DSH stream dispatch."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    canonical_recipe,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    CallbackSourceLease,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.operation_phases import (
    OperationPhases,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    ExecutionBoundary,
    begin_immediate,
    current_execution_boundary,
    prepare_originals,
)

ROLE = "research_model_dispatch"
SCHEMA = "dsh-model-dispatch-binding.v1"
CHILD_ROLE = "research_child_model:"


@dataclass(frozen=True)
class _ModelOriginals:
    execution: ExecutionBoundary | None
    tool_calls_started: int
    max_tool_calls: int | None

    def require_current_counter(self, link: DshSessionLinkRecord) -> None:
        # Parallel, genuinely reserved tools can advance this counter. That
        # observation cannot change its original ceiling or move backwards.
        count = link.tool_calls_started
        if (type(count) is not int or count < self.tool_calls_started
                or link.max_tool_calls != self.max_tool_calls
                or self.max_tool_calls is not None and count > self.max_tool_calls
                or self.max_tool_calls is None and count != self.tool_calls_started):
            raise ValueError("dsh_model_dispatch_tool_budget_changed")


def binding_role(execution_session_id: str | None) -> str:
    return (
        ROLE
        if execution_session_id is None
        else CHILD_ROLE + hashlib.sha256(execution_session_id.encode()).hexdigest()[:40]
    )


class ModelDispatchService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database, self.clock = database, clock

    @staticmethod
    def _read(row: RunRoleOutputRecord) -> DshModelDispatchBinding:
        if (
            row.schema_version != SCHEMA
            or row.payload_hash != hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
        ):
            raise ValueError("dsh_model_binding_corrupt")
        payload = DshModelDispatchBinding.model_validate(json.loads(row.payload_json))
        if payload.run_id != row.run_id or row.role != binding_role(payload.execution_session_id):
            raise ValueError("dsh_model_binding_corrupt")
        return payload

    def get(
        self,
        run_id: str,
        execution_session_id: str | None = None,
    ) -> DshModelDispatchBinding | None:
        with self.database.session() as session:
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=run_id,
                    role=binding_role(execution_session_id),
                )
                .one_or_none()
            )
            return self._read(row) if row else None

    def children(self, run_id: str) -> list[DshModelDispatchBinding]:
        with self.database.session() as session:
            rows = (
                session.query(RunRoleOutputRecord)
                .filter(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role.startswith(CHILD_ROLE),
                )
                .order_by(RunRoleOutputRecord.role)
                .all()
            )
            return [self._read(row) for row in rows]

    def _validate_in_session(
        self, session: Session, payload: DshModelDispatchBinding,
        harness_witness_hash: str | None = None,
    ) -> ExecutionBoundary | None:
        run = session.get(
            RunRecord,
            payload.run_id,
            with_for_update=True,
            populate_existing=True,
        )
        link = session.get(DshSessionLinkRecord, payload.run_id, populate_existing=True)
        if run is None or link is None:
            raise KeyError(payload.run_id)
        if run.status not in {"admitted", "running"}:
            raise ValueError("dsh_model_dispatch_run_terminal")
        if (
            payload.purpose != "conversation"
            or link.dsh_session_id != payload.dsh_session_id
            or link.request_hash != payload.request_hash
            or link.generation != payload.generation
            or link.plugin_build_hash != payload.plugin_build_hash
        ):
            raise ValueError("dsh_model_dispatch_identity_mismatch")
        method_row = (
            session.query(RunRoleOutputRecord)
            .filter_by(
                run_id=payload.run_id,
                role=METHOD_ROLE,
            )
            .populate_existing()
            .one_or_none()
        )
        from .research_observability import requires_tool_admission_proof

        native = requires_tool_admission_proof(session, payload.run_id)
        if native:
            self._require_active_link(link)
            # Public callback identity remains the frozen first linked hash.
            # Authenticate the complete latest execution in this read snapshot;
            # a partial/mixed prepared footprint must never fall back to V1.
            prepared = read_prepared_execution_in_session(session, payload.run_id)
            method_hash = prepared.method.request_hash
            plugin_hash = prepared.method.definition.upstream_identity.plugin_build_hash
            boundary = current_execution_boundary(session, payload.run_id, prepared=prepared)
            from .research_harness_execution import require_harness_root_witness_in_session
            require_harness_root_witness_in_session(
                session, payload.run_id, harness_witness_hash, prepared=prepared,
            )
        else:
            boundary = None
            if method_row is None:
                raise ValueError("dsh_model_dispatch_method_missing")
            # Preserve the original V1 row recipe only in the legacy branch.
            method = ResearchMethodService._read(method_row)  # pyright: ignore[reportPrivateUsage]
            method_hash = method["request_hash"]
            plugin_hash = method["definition"]["upstream_identity"]["plugin_build_hash"]
        if method_hash != payload.request_hash or plugin_hash != payload.plugin_build_hash:
            raise ValueError("dsh_model_dispatch_method_mismatch")
        if payload.execution_session_id is None:
            if payload.parent_session_id is not None:
                raise ValueError("dsh_model_dispatch_lineage_invalid")
        else:
            if (
                payload.execution_session_id == payload.dsh_session_id
                or payload.parent_session_id in {None, payload.execution_session_id}
            ):
                raise ValueError("dsh_model_dispatch_lineage_invalid")
            parent_role = binding_role(
                None
                if payload.parent_session_id == payload.dsh_session_id
                else payload.parent_session_id
            )
            parent_row = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=payload.run_id,
                    role=parent_role,
                )
                .one_or_none()
            )
            if parent_row is None:
                raise ValueError("dsh_model_dispatch_lineage_invalid")
            parent = self._read(parent_row)
            if (
                parent.dsh_session_id != payload.dsh_session_id
                or parent.request_hash != payload.request_hash
                or parent.plugin_build_hash != payload.plugin_build_hash
            ):
                raise ValueError("dsh_model_dispatch_lineage_invalid")
        return boundary

    def bind(
        self, payload: DshModelDispatchBinding, *,
        request_check: Callable[[], None] | None = None,
        diagnostics: OperationPhases | None = None,
        harness_witness_hash: str | None = None,
        writer_start: Callable[[], None] | None = None,
        source_lease: CallbackSourceLease | None = None,
    ) -> DshModelDispatchBinding:
        # This token only narrows this callback's lifetime. It never grants
        # original proof, Run permission or any replacement writer authority.
        phases = diagnostics or OperationPhases()
        check = request_check or (lambda: None)
        check()
        payload = DshModelDispatchBinding.model_validate(payload.model_dump(mode="json"))

        def original(session: Session) -> _ModelOriginals:
            boundary = self._validate_in_session(session, payload, harness_witness_hash)
            link = session.get(DshSessionLinkRecord, payload.run_id, populate_existing=True)
            if link is None:
                raise KeyError(payload.run_id)
            if type(link.tool_calls_started) is not int or link.tool_calls_started < 0:
                raise ValueError("dsh_model_dispatch_tool_budget_changed")
            saved = _ModelOriginals(boundary, link.tool_calls_started, link.max_tool_calls)
            saved.require_current_counter(link)
            if source_lease is not None and boundary is not None:
                # The selected retained capsule is known only after the complete
                # native original has authenticated it in this same RO snapshot.
                source_lease.extend_authenticated_selection(session, payload.run_id)
                source_lease.seal_authenticated_selection(payload.run_id)
            return saved

        prepared = prepare_originals(
            self.database, original,
            link_activity_observations=True, link_tool_counter_observations=True,
            diagnostics=phases, checkpoint=check, source_lease=source_lease,
        )
        check()

        def before_commit(_session: Session) -> None:
            check()

        with self.database.session() as session:
            event.listen(session, "before_commit", before_commit)
            check()
            with phases.phase("writer_wait"):
                begin_immediate(session)
            phases.writer_acquired(session)
            if writer_start is not None:
                # Writer wait has its own typed busy result. Start the short
                # writer-hold slice only after BEGIN IMMEDIATE is acquired.
                writer_start()
            check()
            run = session.get(RunRecord, payload.run_id, populate_existing=True)
            link = session.get(DshSessionLinkRecord, payload.run_id, populate_existing=True)
            if run is None or link is None:
                raise KeyError(payload.run_id)
            if run.status not in {"admitted", "running"}:
                raise ValueError("dsh_model_dispatch_run_terminal")
            run_status, owner = run.status, run.lease_owner
            if (link.dsh_session_id != payload.dsh_session_id
                    or link.request_hash != payload.request_hash
                    or link.generation != payload.generation
                    or link.plugin_build_hash != payload.plugin_build_hash):
                raise ValueError("dsh_model_dispatch_identity_mismatch")
            prepared.value.require_current_counter(link)
            if prepared.value.execution is not None:
                self._require_active_link(link)
                prepared.value.execution.require_current_link(session, payload.run_id)
            if prepared.value.execution is not None and run.status == "running":
                from .research_graph_binding import (
                    _permission,  # pyright: ignore[reportPrivateUsage]
                )
                _permission(session, payload.run_id, run.lease_owner)
            prepared.verify(session, diagnostics=phases, checkpoint=check)
            role = binding_role(payload.execution_session_id)
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=payload.run_id,
                    role=role,
                )
                .one_or_none()
            )
            if row:
                recorded = self._read(row)
                # Later evidence rounds keep the same model controls; only the
                # authenticated link can advance the generation independently.
                if recorded.model_dump(exclude={"generation"}) != payload.model_dump(
                    exclude={"generation"}
                ):
                    raise ValueError("dsh_model_configuration_changed_during_run")
                with phases.phase("writer_fence"):
                    self._final_fence(session, payload.run_id, prepared.value, run_status, owner)
                check()
                return payload
            encoded = canonical_recipe(payload.model_dump(mode="json"))
            session.add(
                RunRoleOutputRecord(
                    output_id="model_"
                    + hashlib.sha256(
                        (payload.run_id if role == ROLE else payload.run_id + ":" + role).encode()
                    ).hexdigest()[:32],
                    run_id=payload.run_id,
                    role=role,
                    payload_json=encoded,
                    payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                    schema_version=SCHEMA,
                    created_at=self.clock(),
                )
            )
            session.flush()
            prepared.verify_after_writes(session, diagnostics=phases, checkpoint=check)
            with phases.phase("writer_fence"):
                self._final_fence(session, payload.run_id, prepared.value, run_status, owner)
            check()
            return payload

    @staticmethod
    def _require_active_link(link: DshSessionLinkRecord) -> None:
        if link.state not in {"admitted", "running", "idle"} or link.terminal_at is not None:
            raise ValueError("dsh_model_dispatch_link_terminal_or_unknown")

    @staticmethod
    def _final_fence(
        session: Session, run_id: str, original: _ModelOriginals,
        status: str, owner: str | None,
    ) -> None:
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        if link is None:
            raise ValueError("dsh_model_dispatch_identity_mismatch")
        original.require_current_counter(link)
        boundary = original.execution
        if boundary is None:
            return
        boundary.require_current_link(session, run_id)
        ModelDispatchService._require_active_link(link)
        run = session.get(RunRecord, run_id, populate_existing=True)
        if status == "running":
            from .research_graph_binding import _fence  # pyright: ignore[reportPrivateUsage]

            _fence(session, run_id, owner)
        elif run is None or run.status != status:
            raise PermissionError("research_graph_writer_permission_lost")
