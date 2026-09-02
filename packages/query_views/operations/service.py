from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityOperationView,
    OperationsOverviewView,
    RuntimeModeView,
    ServiceHeartbeatView,
    SourceManifest,
    SourceOperationView,
)
from packages.kernel.decision_hub_kernel.application.live_observation import (
    EvolutionJobService,
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow

EXPECTED_SERVICES: tuple[tuple[str, str], ...] = (
    ("hub-api", "api"),
    ("hub-realtime-worker", "realtime_worker"),
    ("hub-research-worker", "research_worker"),
    ("hub-evolution-worker", "evolution_worker"),
)


class OperationsQueryService:
    """Assemble a typed operational read model from canonical durable facts."""

    def __init__(
        self,
        database: Database,
        *,
        runtime: RuntimeModeView,
        source_manifests: Sequence[SourceManifest],
        source_execution_enabled: bool = True,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.source_manifests = tuple(source_manifests)
        self.source_execution_enabled = source_execution_enabled
        self.clock = clock
        self.heartbeats = ServiceHeartbeatService(database, clock=clock)
        self.jobs = EvolutionJobService(database, clock=clock)
        self.workbench = WorkbenchAssetService(database)

    def overview(self, *, recent_job_limit: int = 20) -> OperationsOverviewView:
        return OperationsOverviewView(
            schema_version="operations-overview.v1",
            checked_at=self.clock(),
            services=self._services(),
            runtime=self.runtime,
            sources=self._sources(),
            capabilities=self._capabilities(),
            jobs=self.jobs.counts(),
            recent_jobs=self.jobs.list_jobs(limit=max(1, min(recent_job_limit, 100))),
        )

    def _services(self) -> list[ServiceHeartbeatView]:
        durable = {item.service_id: item for item in self.heartbeats.list_views()}
        services: list[ServiceHeartbeatView] = []
        for service_id, role in EXPECTED_SERVICES:
            existing = durable.pop(service_id, None)
            if existing is not None:
                services.append(existing)
                continue
            services.append(
                ServiceHeartbeatView.model_validate(
                    {
                        "service_id": service_id,
                        "role": role,
                        "instance_id": None,
                        "version": None,
                        "mode": None,
                        "status": "offline",
                        "interval_seconds": None,
                        "started_at": None,
                        "heartbeat_at": None,
                        "last_error_code": None,
                    }
                )
            )
        services.extend(durable[key] for key in sorted(durable))
        return services

    def _sources(self) -> list[SourceOperationView]:
        manifests = {item.source_id: item for item in self.source_manifests}
        health = {item.source_id: item for item in self.database.list_source_health()}
        source_ids = sorted(set(manifests) | set(health))
        return [
            SourceOperationView(
                source_id=source_id,
                status=(health[source_id].status if source_id in health else "unknown"),
                enabled=(
                    self.source_execution_enabled
                    and manifests[source_id].enabled
                    if source_id in manifests
                    else False
                ),
                cursor=health[source_id].cursor if source_id in health else None,
                last_success_at=(
                    health[source_id].last_success_at if source_id in health else None
                ),
                next_poll_at=(health[source_id].next_poll_at if source_id in health else None),
                consecutive_failures=(
                    health[source_id].consecutive_failures if source_id in health else 0
                ),
                error_code=health[source_id].error_code if source_id in health else None,
            )
            for source_id in source_ids
        ]

    def _capabilities(self) -> list[CapabilityOperationView]:
        return [
            CapabilityOperationView(
                capability_id=item.capability_id,
                capability_type=item.capability_type,
                status=item.status,
                permissions=item.permissions,
                network_domains=item.network_domains,
                timeout_seconds=item.timeout_seconds,
                max_cost_usd=item.max_cost_usd,
            )
            for item in self.workbench.list_capabilities()
        ]
