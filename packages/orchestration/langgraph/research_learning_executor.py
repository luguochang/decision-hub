"""Registered learning work on the existing queue and one renewable job lease."""

import asyncio

from packages.kernel.decision_hub_kernel.application.research_learning_async import (
    learning_sync,
    settle_learning_task,
)
from packages.orchestration.langgraph.evolution_executor import EvolutionJobExecutor


class LearningEvolutionExecutor(EvolutionJobExecutor):
    def __init__(self, *, learning_handler, **kwargs):
        super().__init__(**kwargs)
        if learning_handler.intake.jobs is not self.jobs:
            raise ValueError("learning_executor_queue_mismatch")
        self.learning_handler = learning_handler

    async def _execute(self, job, worker_id, lease_seconds):
        originals = await self._with_lease(
            learning_sync(self.learning_handler.intake.read_claimed, job, worker_id),
            job.job_id,
            worker_id,
            lease_seconds,
        )
        if originals is None:
            return await super()._execute(job, worker_id, lease_seconds)

        # All preparation, local writes and model awaits share this one renewer.
        # Existing phase callbacks must not create competing nested renewers.
        async def already_leased(operation, phase_job_id, phase_worker_id, phase_seconds):
            if (phase_job_id, phase_worker_id, phase_seconds) != (
                job.job_id,
                worker_id,
                lease_seconds,
            ):
                if hasattr(operation, "close"):
                    operation.close()
                raise ValueError("learning_phase_lease_identity_mismatch")
            return await operation

        return await self._with_lease(
            self.learning_handler.execute_claimed(
                job, worker_id, lease_seconds=lease_seconds, with_lease=already_leased
            ),
            job.job_id,
            worker_id,
            lease_seconds,
        )

    async def _call_sync(self, function, *args, **kwargs):
        return await learning_sync(function, *args, **kwargs)

    async def _with_lease(self, operation, job_id, worker_id, lease_seconds):
        async def renew():
            while True:
                await asyncio.sleep(max(0.1, lease_seconds / 3))
                await learning_sync(self.jobs.renew, job_id, worker_id, lease_seconds=lease_seconds)

        operation_task = asyncio.ensure_future(operation)
        renew_task = asyncio.create_task(renew())
        try:
            done, _ = await asyncio.wait(
                {operation_task, renew_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if renew_task in done:
                await renew_task
                raise RuntimeError("evolution_lease_renewer_stopped")
            return await operation_task
        finally:
            # A canceled to_thread await does not stop the writer. Join it first
            # while heartbeat remains live; ownership fences still apply.
            if not operation_task.done():
                operation_task.cancel()
            interrupted = await settle_learning_task(operation_task)
            renew_task.cancel()
            interrupted |= await settle_learning_task(renew_task)
            if interrupted:
                raise asyncio.CancelledError
