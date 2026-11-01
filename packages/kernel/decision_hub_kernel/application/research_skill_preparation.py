"""Select an installed package once, after preparation and before first dispatch."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from ..persistence.db import RunRecord, DshSessionLinkRecord, as_utc

from ..ports.research_synthesis_protocol import DECISION_OUTPUT_SCHEMA_REF
from .research_binding import FrozenResearchBundle, read_research_bundle_in_session
from .research_execution_binding import read_prepared_execution_in_session
from .research_run_scope import read_run_spot_admission_in_session


@dataclass(frozen=True)
class SkillPreparationObservationScope:
    run_id: str
    generation: int


class SkillPreparationObservationPending(ValueError):
    """A call-local observation handle, never a Task/selection execution grant."""

    def __init__(self, *, run_id, generation, phase, observation, scope):
        super().__init__("cancellation_projection_wait_elapsed")
        self.run_id, self.generation, self.phase = run_id, generation, phase
        self.observation, self.scope = observation, scope


def _require_observed_preparation(read, *, run_id, generation, phase, scope):
    if (read.state == "pending" and read.limitation == "cancellation_projection_wait_elapsed"
            and read.pending is not None):
        raise SkillPreparationObservationPending(
            run_id=run_id, generation=generation, phase=phase, observation=read, scope=scope
        )


@dataclass(frozen=True)
class SkillPreparationRead:
    status: str
    prepared: object | None = None


class SkillPreparation:
    def __init__(self, selections, versions):
        self.selections = selections
        self.versions = dict(versions)

    def prepare(self, run_id, generation):
        # No writer spans immutable package authentication or workflow compile.
        with self.selections.packages.registry.database.session() as session:
            execution = read_prepared_execution_in_session(session, run_id, generation)
            request = execution.execution.request
            if (
                request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
                or request.research_mode != "current_decision"
                or request.execution_mode != "live"
            ):
                return SkillPreparationRead("legacy_not_selected")
            existing = self.selections._row(session, run_id)
            if existing is not None:
                self.selections.read_in_session(session, run_id)
                return SkillPreparationRead("original_selection_retained")
            if generation != 1 or execution.execution.turn_kind != "research_round":
                return SkillPreparationRead("repair_unselected_legacy")
            binding = read_run_spot_admission_in_session(session, run_id)
            bundle = read_research_bundle_in_session(session, run_id)
            if binding is None or not isinstance(bundle, FrozenResearchBundle):
                return SkillPreparationRead("unsupported_task")
            version = self.versions.get(binding.scope.scope_id)
            if version is None:
                return SkillPreparationRead("scope_not_installed")
            version_id, first_known = version
            cutoff = (
                bundle.request.context_manifest.created_at
                if bundle.request.context_manifest
                else bundle.recorded_at
            )
            if cutoff < first_known:
                # Old frozen requests do not gain a newly installed method.
                return SkillPreparationRead("task_predates_method")
        return SkillPreparationRead("prepared", self.selections.prepare(run_id, version_id))

    def freeze_in_session(self, session, value):
        if value.status != "prepared" or value.prepared is None:
            raise ValueError("skill_selection_prepared_original_required")
        return self.selections.freeze_in_session(session, value.prepared)

    async def after_prepared(
        self, projector, *, run_id, generation, deadline_at, clock, expected_lease_owner=None
    ):
        def remaining():
            return max(0.0, min(30.0, (deadline_at - clock()).total_seconds()))
        scope = SkillPreparationObservationScope(run_id, generation)
        read = await projector.read(
            lambda _session: self.prepare(run_id, generation), timeout_seconds=remaining(),
            observation_scope=scope,
        )
        if (read.state == "pending" and read.limitation == "cancellation_projection_wait_elapsed"
                and read.pending is not None):
            try:
                logging.getLogger(__name__).info(
                    "skill_preparation_read_inflight operation_id=%s generation=%s",
                    read.pending.operation_id, generation,
                )
            except Exception:
                pass  # Diagnostic failure cannot change the native operation.

            # Continue this exact call-local native RO Future. A legal slow read
            # must reach its one freeze, never restart prepare every observation.
            read = await projector.await_pending(
                read.pending, timeout_seconds=max(0.0, (deadline_at - clock()).total_seconds()),
                read_only=True, observation_scope=scope,
            )
        if clock() >= deadline_at:
            raise ValueError("skill_preparation_deadline_elapsed")

        if read.state != "ready" or read.value is None:
            raise ValueError(read.limitation or "skill_preparation_unconfirmed")
        if read.value.status != "prepared":
            return read.value.status
        # Normal freeze verifies the actual PreparedOriginals and current
        # Task/run/source/permission; completion of this Future grants none.
        def freeze(session):
            def require_current():
                now = clock()
                if now >= deadline_at:
                    raise ValueError("skill_preparation_deadline_elapsed")
                run = session.get(RunRecord, run_id, populate_existing=True)
                link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
                if (not expected_lease_owner or run is None or run.status != "running"
                        or run.lease_owner != expected_lease_owner
                        or as_utc(run.lease_expires_at) is None or as_utc(run.lease_expires_at) <= now):
                    raise ValueError("skill_preparation_current_owner_lost")
                if (link is None or link.generation != generation
                        or as_utc(link.deadline_at) != deadline_at
                        or link.terminal_at is not None
                        or link.state not in {"admitted", "running", "idle"}):
                    raise ValueError("skill_preparation_execution_changed")
            require_current()
            result = self.freeze_in_session(session, read.value)
            require_current()
            return result

        written = await projector.write(
            freeze, timeout_seconds=remaining(), observation_scope=scope,
        )
        _require_observed_preparation(written, run_id=run_id, generation=generation, phase="write", scope=scope)
        if written.state != "ready":
            # Pending is not a rollback or permission to dispatch. Next runtime
            # attempt reads the exact saved selection if the writer finished.
            raise ValueError(written.limitation or "skill_selection_commit_unconfirmed")
        return "selected"
