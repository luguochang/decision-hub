"""Persistent package stage, then explicit trial admission boundary."""

from packages.kernel.decision_hub_kernel.application.research_learning_async import learning_sync


class LearningPackageContinuation:
    def __init__(self, handler, builder, *, trials=None):
        self.handler, self.builder, self.trials = (handler, builder, trials)

    async def execute_claimed(
        self,
        job,
        worker_id,
        *,
        binding,
        protocol,
        context,
        reflection,
        reflection_receipt,
        lease_seconds,
        with_lease,
    ):
        stage = await learning_sync(self.handler._stage, job.job_id, "package")
        if stage is None:
            body = await learning_sync(
                self.builder.build,
                binding=binding,
                protocol=protocol,
                reflection=reflection,
                reflection_receipt=reflection_receipt,
                context=context,
            )
            stage = await learning_sync(self.handler._append, job, worker_id, "package", body)
        package = await learning_sync(self.handler._read_payload, stage)
        if self.trials is None:
            return await learning_sync(
                self.handler.intake.jobs.advance,
                job.job_id,
                worker_id,
                stage="candidate",
                status="waiting_inputs",
                waiting_reason="learning_method_trial_not_admitted",
                result_refs=[reflection_receipt.id, stage.id],
            )
        return await self.trials.execute_claimed(
            job,
            worker_id,
            binding=binding,
            protocol=protocol,
            package=package,
            package_receipt=stage,
            lease_seconds=lease_seconds,
            with_lease=with_lease,
        )
