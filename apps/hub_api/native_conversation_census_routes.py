"""Host-authenticated immutable lifecycle census, separate from root history."""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request

from packages.kernel.decision_hub_kernel.application.native_conversation_census import (
    NativeConversationCensusService,
    read_native_census_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.native_conversation_census import (
    NativeConversationCensusRecorded,
    NativeConversationCensusV1,
)

from .provider_input_routes import _bounded_callback  # pyright: ignore[reportPrivateUsage]


def native_conversation_census_router(
    database: Database,
    authenticate: Callable[[str | None], None],
) -> APIRouter:
    router = APIRouter(tags=["native-conversation-census-originals"])
    service = NativeConversationCensusService(database)

    @router.post(
        "/v1/dsh/sessions/{run_id}/native-conversation-censuses/{generation}",
        response_model=NativeConversationCensusRecorded,
    )
    async def record(
        run_id: str,
        generation: int,
        payload: NativeConversationCensusV1,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        authenticate(bridge_key)
        if (payload.run_id, payload.generation) != (run_id, generation):
            raise HTTPException(409, "research_provider_callback_path_mismatch")
        # Reuse the existing finite cancellation/watcher boundary. Its generic
        # diagnostic dispatch key is the collector epoch, not an input row.
        return await _bounded_callback(request, service.record, payload)

    @router.get(
        "/v1/dsh/sessions/{run_id}/native-conversation-censuses/{generation}",
        response_model=NativeConversationCensusRecorded,
    )
    def read(
        run_id: str,
        generation: int,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        authenticate(bridge_key)
        try:
            with database.session() as session:
                session.connection().exec_driver_sql("BEGIN")
                value = read_native_census_in_session(
                    session,
                    run_id=run_id,
                    generation=generation,
                )
                if value is None:
                    raise HTTPException(404, "research_provider_census_not_found")
                original, reference = value
                return NativeConversationCensusRecorded(
                    run_id=run_id,
                    generation=generation,
                    census_sha256=original.census.census_sha256,
                    reference=reference,
                )
        except (ValueError, PermissionError, TypeError) as exc:
            raise HTTPException(409, "research_provider_census_original_invalid") from exc

    return router
