"""Event lifecycle GETs and owner-scoped display/ack receipts."""
from pathlib import Path
from typing import Annotated, Literal
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import OperationalError
from packages.kernel.decision_hub_kernel.application.cpi_pre_event_context import _archive
from packages.kernel.decision_hub_kernel.application.event_opportunity_monitor import EventOpportunityMonitorService
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.query_views.event_lifecycle import EventLifecycleQueryService


class EventNoticeCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    command: Literal['display', 'ack']


def event_lifecycle_router(database, *, require_owner, archive=None, feature_root, clock=utcnow):
    archive = archive or _archive()
    monitor = EventOpportunityMonitorService(database, MarketPathStore(database, archive, clock=clock),
        feature_root=Path(feature_root), archive=archive, clock=clock)
    query = EventLifecycleQueryService(database, monitor, archive=archive, clock=clock)
    router = APIRouter(tags=['event-lifecycle'])

    def call(operation):
        try:
            return operation()
        except OperationalError as error:
            raise HTTPException(503, 'event_lifecycle_storage_unavailable') from error
        except (ValueError, OSError) as error:
            raise HTTPException(409, 'event_lifecycle_original_unavailable') from error

    @router.get('/v1/event-lifecycles')
    def source_status():
        return call(lambda: dict(schema_version='event-lifecycle-sources.v1', sources=query.sources()))

    @router.get('/v1/event-lifecycles/{event_key:path}')
    def detail(event_key: str, owner: Annotated[str | None, Header(alias='X-Owner-Id')] = None):
        resolved = require_owner(owner) if owner else None
        result = call(lambda: query.detail(event_key, owner_id=resolved))
        if result is None:
            raise HTTPException(404, 'event_lifecycle_not_found')
        return result

    @router.post('/v1/event-notices/{notice_id}/commands')
    def command(notice_id: str, body: EventNoticeCommand,
                owner: Annotated[str | None, Header(alias='X-Owner-Id')] = None,
                key: Annotated[str | None, Header(alias='Idempotency-Key')] = None):
        resolved = require_owner(owner)
        if key != body.request_id:
            raise HTTPException(400, 'event_notice_request_identity_mismatch')
        result = call(lambda: monitor.record_command(notice_id, owner_id=resolved,
            request_id=body.request_id, command=body.command))
        if result is None:
            raise HTTPException(404, 'event_notice_not_found')
        return result

    return router
