"""Authenticated native-only Harness transport; no user approval authority."""
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import DshRunSessionLinkView
from packages.kernel.decision_hub_kernel.application.research_harness_execution import (
    HarnessExecutionService, HarnessExecutionView, HostScope, RootConsumption,
    WitnessSummary, harness_required_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database


class HarnessLinkView(DshRunSessionLinkView):
    harness_required: Literal[True] | None = None


def link_hint(database: Database, view: DshRunSessionLinkView) -> HarnessLinkView:
    body = view.model_dump(mode="json")
    with database.session() as session:
        if harness_required_in_session(session, view.run_id):
            body["harness_required"] = True
    # Every old field, including nulls, remains explicitly set. Only absent hint omitted.
    return HarnessLinkView.model_validate(body)


def harness_execution_router(database: Database, authenticate: Callable[[str | None], None]) -> APIRouter:
    router = APIRouter()
    service = HarnessExecutionService(database)

    @router.post("/v1/dsh/sessions/{run_id}/harness-execution", response_model=HarnessExecutionView)
    def originals(run_id: str, payload: HostScope, generation: int = Query(ge=1),
                  bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key")) -> HarnessExecutionView:
        authenticate(bridge_key)
        try:
            return service.view(run_id, generation, payload)
        except (ValueError, PermissionError, KeyError) as exc:
            raise HTTPException(409, "harness_execution_unavailable") from exc

    @router.put("/v1/dsh/sessions/{run_id}/harness-root-consumption", response_model=WitnessSummary)
    def record(run_id: str, payload: RootConsumption,
               bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key")) -> WitnessSummary:
        authenticate(bridge_key)
        if payload.run_id != run_id:
            raise HTTPException(400, "harness_execution_identity_mismatch")
        try:
            return service.record(payload)
        except (ValueError, PermissionError, KeyError) as exc:
            raise HTTPException(409, "harness_root_consumption_rejected") from exc
    return router
