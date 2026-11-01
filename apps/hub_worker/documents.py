from __future__ import annotations

from datetime import UTC, datetime

from apps.hub_worker.heartbeat import keep_heartbeat
from packages.kernel.decision_hub_kernel.application.live_observation import ServiceHeartbeatService
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.persistence.db import Database


class DocumentPipelineWorker:
    """Durable document parser/correction worker using the shared DB queue."""

    def __init__(
        self,
        service: ResearchLibraryService,
        database: Database,
        *,
        service_id: str = "hub-documents",
    ) -> None:
        self.service = service
        self.heartbeats = ServiceHeartbeatService(database)
        self.service_id = service_id
        self.started_at = datetime.now(UTC)
        self.last_error_code: str | None = None

    async def tick(self) -> str | None:
        try:
            async with keep_heartbeat(self._beat):
                result = await self.service.process_next(worker_id=self.service_id)
        except Exception as exc:
            self.last_error_code = f"document_processing_failed:{type(exc).__name__}"
            self._beat()
            raise
        self.last_error_code = None
        self._beat()
        return result

    def _beat(self) -> None:
        self.heartbeats.beat(
            service_id=self.service_id,
            role="documents_worker",
            instance_id=self.service_id,
            version="0.1.0",
            mode="local",
            interval_seconds=10,
            started_at=self.started_at,
            last_error_code=self.last_error_code,
        )
