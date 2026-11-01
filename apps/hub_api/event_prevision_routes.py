"""Published, event-owned previsions. Every endpoint is read only."""
import logging
from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import OperationalError
from packages.query_views.event_prevision import (
    EventPrevisionDetail, EventPrevisionList, EventPrevisionQueryService,
)

logger = logging.getLogger(__name__)


def event_prevision_router(database, *, archive=None, clock=None):
    service = EventPrevisionQueryService(database, archive=archive, **({'clock': clock} if clock else {}))
    router = APIRouter(tags=['event-prevision'])

    def read(operation):
        try:
            return operation()
        except OperationalError as error:
            logger.warning('event_prevision_database_unavailable', exc_info=error)
            raise HTTPException(503, 'event_prevision_database_unavailable') from error
        except (ValueError, FileNotFoundError) as error:
            logger.warning('event_prevision_original_verification_failed', exc_info=error)
            raise HTTPException(502, 'event_prevision_original_verification_failed') from error

    @router.get('/v1/event-previsions', response_model=EventPrevisionList)
    def listing():
        return read(service.listing)

    @router.get('/v1/event-previsions/{event_key:path}', response_model=EventPrevisionDetail)
    def detail(event_key: str):
        result = read(lambda: service.detail(event_key))
        if result is None:
            raise HTTPException(404, 'event_prevision_not_found')
        return result

    return router
