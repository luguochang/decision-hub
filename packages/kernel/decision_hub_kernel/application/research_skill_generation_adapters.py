"""Generation-aware adapters over the existing sole preparation/skill runtime."""

from __future__ import annotations

from .research_skill_generation import GenerationMaterial, SkillGenerationService, row_ref
from .research_skill_package import decoded
from .research_skill_preparation import SkillPreparation, SkillPreparationRead
from .research_skill_use import SkillUseService


class GenerationSkillUseService(SkillUseService):
    def __init__(self, selections, *, bindings=None, **options):
        super().__init__(selections, **options)
        self.bindings = bindings or SkillGenerationService(selections)

    def _load_directive(self, session, selection, execution):
        if execution.generation == 1:
            return super()._load_directive(session, selection, execution)
        from .research_execution_binding import read_prepared_execution_in_session
        from .research_skill_repair_policy import (
            LATE_MODE,
            MODE,
            prompt_policy_reference,
            read_repair_policy_in_session,
        )

        prepared = read_prepared_execution_in_session(
            session, execution.run_id, execution.generation
        )
        reference = prompt_policy_reference(prepared.prompt.prompt)
        if reference is None:
            raise ValueError("skill_repair_load_policy_required")
        policy = read_repair_policy_in_session(
            session,
            run_id=execution.run_id,
            generation=execution.generation,
            reference=reference,
            request=prepared.execution.request,
        )
        if policy["policy"] == LATE_MODE:
            return {
                "next_tool_call": {
                    "tool_name": "decision_hub_skill",
                    "node_id": "spot_evidence",
                    "arguments": {
                        "operation": "execute_step",
                        "skill_version_id": selection["skill_version_id"],
                        "node_id": "spot_evidence",
                        "input": {"source": "frozen_original"},
                    },
                },
                "instructions": (
                    "Use the named skill step with source frozen_original. Do not acquire "
                    "new Gateway evidence for this frozen-original repair. The Hub "
                    "independently authenticates the retained original; delivery alone "
                    "does not prove loaded, executed, consumed, or financial value."
                ),
            }
        if policy["policy"] != MODE:
            raise ValueError("skill_repair_load_policy_unsupported")
        return super()._load_directive(session, selection, execution)

    def _selection(self, session, run_id, generation, execution_session_id):
        if generation == 1:
            return super()._selection(session, run_id, generation, execution_session_id)
        if (
            type(generation) is not int
            or generation < 2
            or not isinstance(run_id, str)
            or not 1 <= len(run_id) <= 128
            or not isinstance(execution_session_id, str)
            or not 1 <= len(execution_session_id) <= 256
        ):
            raise ValueError("skill_use_identity_invalid")
        # A publication read replaces selections.packages with a raw tracker.
        bindings = self.bindings.with_selections(self.selections)
        body = bindings.read_execution_binding_in_session(session, run_id, generation)
        execution = self.selections.execution_reader(session, run_id, generation)
        identity = decoded(execution.scope_identity_json)
        if identity["dsh_session_id"] != execution_session_id:
            raise ValueError("skill_root_session_required")
        return body, execution, identity


class GenerationAwareSkillPreparation(SkillPreparation):
    """Wrap baseline or trial preparation; generation1 keeps its exact policy."""

    def __init__(self, original, bindings=None, consumption=None):
        self.original = original
        super().__init__(original.selections, original.versions)
        self.bindings = bindings or SkillGenerationService(self.selections)
        from .research_skill_repair_policy import SkillRepairPolicyService

        self.repair_policy = SkillRepairPolicyService(self.bindings, consumption.nodes if consumption else None)
        self.consumption = consumption

    async def before_repair(
        self,
        projector,
        *,
        run_id,
        source_generation,
        request,
        errors,
        evidence_ids,
        lease_owner,
        deadline_at,
        clock,
    ):
        def remaining():
            return max(0.0, min(30.0, (deadline_at - clock()).total_seconds()))

        read = await projector.read(
            lambda _session: self.repair_policy.prepare(
                run_id=run_id,
                source_generation=source_generation,
                request=request,
                errors=errors,
                evidence_ids=evidence_ids,
                lease_owner=lease_owner,
            ),
            timeout_seconds=remaining(),
        )
        if read.state != "ready" or read.value is None:
            raise ValueError(read.limitation or "skill_repair_policy_read_unconfirmed")
        if read.value.value is None:
            return None
        written = await projector.write(
            lambda session: self.repair_policy.freeze_in_session(session, read.value),
            timeout_seconds=remaining(),
        )
        if written.state != "ready" or written.value is None:
            raise ValueError(written.limitation or "skill_repair_policy_commit_unconfirmed")
        return written.value

    async def verify_repair_result(
        self, projector, *, run_id, generation, mapped, deadline_at, clock
    ):
        if self.consumption is None:
            raise ValueError("skill_repair_consumption_reader_required")
        candidate = mapped.action_synthesis
        if candidate is None or not candidate.synthesis.skill_consumption_refs:
            raise ValueError("skill_repair_current_consumption_required")

        def read(session):
            from .research_skill_package import canonical_hash
            from .research_skill_publication import normalize_review

            binding = self.bindings.read_execution_binding_in_session(session, run_id, generation)
            matching = []
            for reference in candidate.synthesis.skill_consumption_refs:
                event = self.consumption.read_consumed_in_session(
                    session,
                    run_id=run_id,
                    event_ref={
                        "event_id": reference.consumption_receipt_ref,
                        "payload_hash": reference.consumption_receipt_hash,
                    },
                )
                if (
                    event["generation"] != generation
                    or event["use_id"] != binding["use_id"]
                    or event["skill_version_id"] != binding["skill_version_id"]
                    or reference.version_id != binding["skill_version_id"]
                    or reference.version_hash != binding["version_hash"]
                    or event["root_session_id"] != binding["root_session_id"]
                    or canonical_hash(event) != reference.consumption_receipt_hash
                ):
                    raise ValueError("skill_repair_consumption_binding_mismatch")
                output = self.consumption.nodes._read_value(
                    event["details"]["consumer_output_ref"],
                    event["details"]["consumer_output_hash"],
                )
                review = normalize_review(output["mechanism_review"])
                if (
                    sum(
                        item.model_dump(mode="json") == review
                        for item in candidate.synthesis.mechanism_reviews
                    )
                    != 1
                ):
                    raise ValueError("skill_repair_mechanism_not_consumed")
                matching.append(event["event_id"])
            if len(set(matching)) != len(matching):
                raise ValueError("skill_repair_duplicate_consumption")
            return True

        result = await projector.read(
            read, timeout_seconds=max(0.0, min(30.0, (deadline_at - clock()).total_seconds()))
        )
        if result.state != "ready" or result.value is not True:
            raise ValueError(result.limitation or "skill_repair_consumption_unconfirmed")

    def prepare(self, run_id, generation):
        if type(generation) is not int or generation < 1:
            raise ValueError("skill_generation_invalid")
        if generation == 1:
            return self.original.prepare(run_id, generation)
        with self.selections.packages.registry.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            if self.selections._row(session, run_id) is None:
                # An unversioned old Run never gains a method on repair.
                return self.original.prepare(run_id, generation)
            from .research_execution_binding import read_prepared_execution_in_session
            from .research_skill_repair_policy import prompt_policy_reference

            saved = read_prepared_execution_in_session(session, run_id, generation)
            if prompt_policy_reference(saved.prompt.prompt) is None:
                return SkillPreparationRead("synthesis_only_repair_no_reexecution")
        return SkillPreparationRead("prepared", self.bindings.prepare(run_id, generation))

    def freeze_in_session(self, session, value):
        if value.status != "prepared" or value.prepared is None:
            raise ValueError("skill_selection_prepared_original_required")
        if isinstance(value.prepared.value, GenerationMaterial):
            return self.bindings.freeze_in_session(session, value.prepared)
        return self.original.freeze_in_session(session, value)


def read_generation_method_in_session(session, *, run_id, generation, service):
    """Plain exact generation originals for MethodViewV2, no writes/qualification."""
    execution = service.use.selections.execution_reader(session, run_id, generation)
    identity = decoded(execution.scope_identity_json)
    selection, _, _ = service.use._selection(
        session, run_id, generation, identity["dsh_session_id"]
    )
    listing = service.workflow.list_in_session(
        session,
        run_id=run_id,
        generation=generation,
        execution_session_id=identity["dsh_session_id"],
    )
    package = service.packages.read_in_session(session, selection["skill_version_id"])
    workflow = next(r for r in package.resources if r.path == "workflow.json")
    binding_ref = None
    if generation > 1:
        row = service.use.bindings.with_selections(service.use.selections)._row(
            session, run_id, generation
        )
        binding_ref = row_ref(row)
    return dict(
        selection=selection,
        execution_identity=identity,
        listing=listing,
        version=package.version,
        workflow=decoded(workflow.body),
        binding_ref=binding_ref,
    )
