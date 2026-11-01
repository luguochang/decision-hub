"""Finite behavior evaluation and future admission on the same claimed job."""

from packages.kernel.decision_hub_kernel.application.research_learning_async import learning_sync

from packages.kernel.decision_hub_kernel.application.research_learning_trial_protocols import (
    LearningTrialProtocolFactory,
)
from packages.kernel.decision_hub_kernel.application.research_method_trial import (
    TrialAdmissionService,
)
from packages.kernel.decision_hub_kernel.application.research_skill_behavior import (
    BehaviorControlRunner,
)
from packages.kernel.decision_hub_kernel.application.research_skill_behavior_reader import (
    BehaviorOriginalReader,
)
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import (
    LearningRepository,
)
from packages.kernel.decision_hub_kernel.persistence.research_skill_behavior import BehaviorStore


class LearningTrialContinuation:
    def __init__(self, handler, materials, sources, runtime, *, call_timeout_seconds):
        self.handler, self.materials, self.sources, self.runtime = (
            handler,
            materials,
            sources,
            runtime,
        )
        self.call_timeout_seconds = call_timeout_seconds

    async def execute_claimed(
        self,
        job,
        worker_id,
        *,
        binding,
        protocol,
        package,
        package_receipt,
        lease_seconds,
        with_lease,
    ):
        intake = self.handler.intake
        old = await learning_sync(self.handler._stage, job.job_id, "trial")
        if old is not None:
            body = await learning_sync(self.handler._read_payload, old)
            if body["package_receipt"] != {
                "ref": package_receipt.id,
                "hash": package_receipt.payload_hash,
            }:
                raise ValueError("learning_trial_stage_package_changed")
            return await learning_sync(
                intake.jobs.advance,
                job.job_id,
                worker_id,
                stage="candidate",
                status="completed",
                result_refs=[package_receipt.id, old.id],
            )

        def fence(session, behavior_protocol_id):
            LearningRepository(session).owned(
                job.job_id, worker_id, job.attempt, now=intake.clock()
            )
            original = store.read_in_session(session, "protocol", behavior_protocol_id)
            if (
                original is None
                or original.job_id != job.job_id
                or original.package_stage.ref != package_receipt.id
            ):
                raise ValueError("learning_behavior_writer_job_mismatch")

        store = BehaviorStore(intake.database, clock=intake.clock, writer_fence=fence)
        runner = BehaviorControlRunner(
            store, store, intake.archive, self.materials, self.runtime, clock=intake.clock
        )
        service = TrialAdmissionService(
            intake.database,
            self.materials,
            BehaviorOriginalReader(store, intake.archive),
            self.sources,
            clock=intake.clock,
            worker_id=worker_id,
            job_attempt=job.attempt,
            behavior_runner=runner,
        )
        factory = LearningTrialProtocolFactory(
            intake.database,
            self.materials,
            self.sources,
            store,
            recipe_hash=self.runtime.recipe_hash,
            call_timeout_seconds=self.call_timeout_seconds,
            clock=intake.clock,
        )
        registered = await with_lease(
            learning_sync(
                lambda: factory.prepare_for_claimed(
                    job_id=job.job_id,
                    package_receipt_id=package_receipt.id,
                    worker_id=worker_id,
                    attempt=job.attempt,
                )
            ),
            job.job_id,
            worker_id,
            lease_seconds,
        )
        try:
            behavior = await with_lease(
                service.evaluate(job.job_id, package_receipt.id, registered.behavior_id),
                job.job_id,
                worker_id,
                lease_seconds,
            )
        except ValueError as error:
            if str(error) != "behavior_provider_completion_unconfirmed":
                raise
            return await learning_sync(
                intake.jobs.advance,
                job.job_id,
                worker_id,
                stage="candidate",
                status="waiting_inputs",
                waiting_reason="behavior_provider_completion_unconfirmed",
                result_refs=[package_receipt.id, registered.behavior_id],
            )
        if behavior.status != "behavior_passed":
            body = {
                "schema_version": "research-learning-trial-stage.v1",
                "package_receipt": {
                    "ref": package_receipt.id,
                    "hash": package_receipt.payload_hash,
                },
                "behavior_receipt": {"ref": behavior.id, "hash": behavior.payload_hash},
                "admission_receipt": None,
                "status": behavior.status,
                "financial_qualified": False,
            }
        else:
            receipt = await with_lease(
                learning_sync(
                    lambda: service.admit(
                        job.job_id, package_receipt.id, behavior.id, registered.trial_id
                    )
                ),
                job.job_id,
                worker_id,
                lease_seconds,
            )
            body = {
                "schema_version": "research-learning-trial-stage.v1",
                "package_receipt": {
                    "ref": package_receipt.id,
                    "hash": package_receipt.payload_hash,
                },
                "behavior_receipt": {"ref": behavior.id, "hash": behavior.payload_hash},
                "admission_receipt": {"ref": receipt.id, "hash": receipt.payload_hash},
                "status": "trial_registered",
                "financial_qualified": False,
            }
        stage = await learning_sync(self.handler._append, job, worker_id, "trial", body)
        return await learning_sync(
            intake.jobs.advance,
            job.job_id,
            worker_id,
            stage="candidate",
            status="completed",
            result_refs=[package_receipt.id, stage.id],
        )
