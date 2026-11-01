"""New typed Evolution handler. Never creates a legacy CandidateVersion."""

from __future__ import annotations
import hashlib
import json
from packages.kernel.decision_hub_kernel.application.research_learning_async import learning_sync
from dataclasses import asdict
from packages.kernel.decision_hub_kernel.ports.research_learning import OriginalRef, ReflectionDelta
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import (
    LearningRepository,
    encoded,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate


class ResearchRecipeLearningHandler:
    def __init__(self, intake, planner, contexts, *, continuation=None):
        self.intake, self.planner, self.contexts = (intake, planner, contexts)
        self.continuation = continuation

    def _read_payload(self, receipt):
        raw = self.intake.archive.read(receipt.payload.ref)
        if hashlib.sha256(raw).hexdigest() != receipt.payload.hash:
            raise ValueError("learning_stage_payload_changed")
        body = json.loads(raw)
        if encoded(body).encode() != raw:
            raise ValueError("learning_stage_payload_not_canonical")
        return body

    def _stage(self, job_id, phase):
        with self.intake.database.session() as session:
            return LearningRepository(session).stage(job_id, phase)

    def _append(self, job, worker_id, phase, body):
        digest, locator = self.intake.archive.put(encoded(body).encode())
        with self.intake.database.session() as session:
            begin_immediate(session)
            return LearningRepository(session).append_stage(
                job_id=job.job_id,
                worker_id=worker_id,
                attempt=job.attempt,
                phase=phase,
                payload=OriginalRef(ref=locator, hash=digest),
                now=self.intake.clock(),
            )

    @staticmethod
    def _validate_reflection(body, context, protocol):
        delta = ReflectionDelta.model_validate(body)
        if delta.parent_manifest != protocol.parent_manifest:
            raise ValueError("reflection_parent_mismatch")
        allowed = {(item["ref"], item["hash"]) for item in context["allowed_refs"]}
        refs = [*delta.source_refs]
        for item in delta.hypotheses:
            refs.extend(
                (
                    *item.supporting_refs,
                    *item.counter_refs,
                    *item.positive_case_refs,
                    *item.negative_case_refs,
                )
            )
        if not delta.source_refs or any(((item.ref, item.hash) not in allowed for item in refs)):
            raise ValueError("reflection_evidence_outside_frozen_context")
        parent_files = context["parent_files"]
        for change in delta.changes:
            if (
                change.path not in protocol.allowed_resource_paths
                or change.path not in parent_files
            ):
                raise ValueError("reflection_resource_not_allowed")
            before = parent_files[change.path]
            if (
                change.before_hash != before["hash"]
                or hashlib.sha256(before["body"].encode()).hexdigest() != before["hash"]
            ):
                raise ValueError("reflection_resource_parent_hash_mismatch")
            if change.after_body == before["body"]:
                raise ValueError("reflection_no_actual_resource_change")
        from packages.kernel.decision_hub_kernel.application.research_learning_suite import (
            validate_reflection_cases,
        )

        validate_reflection_cases(delta, context)
        return delta

    async def execute_claimed(self, job, worker_id, *, lease_seconds, with_lease):
        routed = await learning_sync(self.intake.read_claimed, job, worker_id)
        if routed is None:
            raise ValueError("learning_handler_requires_registered_binding")
        binding, protocol, snapshot = routed
        context_receipt = await learning_sync(self._stage, job.job_id, "context")
        if context_receipt is None:
            context = await learning_sync(self.contexts.build, binding, protocol, snapshot)
            context_receipt = await learning_sync(self._append, job, worker_id, "context", context)
        context = await learning_sync(self._read_payload, context_receipt)
        reflection_receipt = await learning_sync(self._stage, job.job_id, "reflection")
        if reflection_receipt is None:
            prior_dispatch = await learning_sync(self._stage, job.job_id, "dispatch")
            if prior_dispatch is not None:
                await learning_sync(self._read_payload, prior_dispatch)
                return await learning_sync(
                    self.intake.jobs.advance,
                    job.job_id,
                    worker_id,
                    stage="candidate",
                    status="waiting_inputs",
                    waiting_reason="learning_provider_completion_unconfirmed",
                    result_refs=[context_receipt.id, prior_dispatch.id],
                )
            request = await learning_sync(self.planner.request, context, now=self.intake.clock())
            dispatch = await learning_sync(
                self._append,
                job,
                worker_id,
                "dispatch",
                {
                    "schema_version": "research-reflection-dispatch.v1",
                    "context_receipt": {
                        "ref": context_receipt.id,
                        "hash": context_receipt.payload_hash,
                    },
                    "recipe": self.planner.recipe,
                    "recipe_hash": self.planner.recipe_hash,
                    "request": asdict(request) | {"deadline_at": request.deadline_at.isoformat()},
                },
            )
            result = await with_lease(
                self.planner.execute(request), job.job_id, worker_id, lease_seconds
            )
            response = {
                "schema_version": "research-reflection-response.v1",
                "dispatch_receipt": {"ref": dispatch.id, "hash": dispatch.payload_hash},
                "result": asdict(result),
            }
            reflection_receipt = await learning_sync(
                self._append, job, worker_id, "reflection", response
            )
        response = await learning_sync(self._read_payload, reflection_receipt)
        dispatch = await learning_sync(self._stage, job.job_id, "dispatch")
        if dispatch is None or response["dispatch_receipt"] != {
            "ref": dispatch.id,
            "hash": dispatch.payload_hash,
        }:
            raise ValueError("reflection_dispatch_original_mismatch")
        delta = await learning_sync(
            self._validate_reflection, response["result"]["payload"], context, protocol
        )
        if delta.outcome != "candidate":
            return await learning_sync(
                self.intake.jobs.advance,
                job.job_id,
                worker_id,
                stage="plan",
                status="completed",
                result_refs=[context_receipt.id, reflection_receipt.id],
            )
        if self.continuation is None:
            return await learning_sync(
                self.intake.jobs.advance,
                job.job_id,
                worker_id,
                stage="candidate",
                status="waiting_inputs",
                waiting_reason="learning_package_pipeline_unavailable",
                result_refs=[context_receipt.id, reflection_receipt.id],
            )
        return await self.continuation.execute_claimed(
            job,
            worker_id,
            binding=binding,
            protocol=protocol,
            context=context,
            reflection=delta,
            reflection_receipt=reflection_receipt,
            lease_seconds=lease_seconds,
            with_lease=with_lease,
        )
