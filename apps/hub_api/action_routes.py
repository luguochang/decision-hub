"""Action UI reads and receipt-backed owner commands; no research on GET."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Response
from packages.kernel.decision_hub_kernel.application.action_plan import ActionPlanService
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, TimeoutError

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v1 import (
    DecisionUiCommandReceiptV1,
    DecisionUiCommandV1,
    DecisionUiCurrentViewV1,
    DecisionUiCurrentViewV2,
    DecisionUiPlanViewV1,
    DecisionUiTrackingViewV1,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    DecisionUiReviewViewV1,
)
from packages.query_views.decision_product import DecisionProductQuery

OwnerHeader = Annotated[str | None, Header(alias="X-Owner-Id", max_length=128)]
Identity = Annotated[str, Query(min_length=1, max_length=128)]


def action_router(
    database: Database,
    require_owner: Callable[[str | None], str],
    *,
    enabled: bool,
    clock: Callable[[], datetime] = utcnow,
) -> APIRouter:
    router = APIRouter(tags=["decision-actions"])
    query = DecisionProductQuery(database, enabled=enabled, clock=clock)
    commands = ActionPlanService(database, clock=clock)

    def call[T, **P](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "action_object_not_found") from exc
        except ValidationError as exc:
            raise HTTPException(409, "action_original_shape_invalid") from exc
        except ValueError as exc:
            code = str(exc)
            if re.fullmatch(r"[a-z0-9_]{1,128}", code) is None:
                code = "action_original_invalid"
            raise HTTPException(404 if code == "action_plan_not_found" else 409, code) from exc
        except (OperationalError, TimeoutError) as exc:
            raise HTTPException(503, "action_storage_unavailable") from exc

    def owner(value: str | None) -> str:
        resolved = require_owner(value)
        if not resolved.strip() or resolved != resolved.strip() or resolved.startswith("system:"):
            raise HTTPException(403, "action_owner_identity_invalid")
        return resolved

    @router.get("/v1/actions/current", response_model=DecisionUiCurrentViewV1)
    def current(
        response: Response,
        scope_id: Annotated[str, Query(min_length=1, max_length=192)],
        horizon: Literal["30m", "24h", "72h"] = "24h",
        owner_id: OwnerHeader = None,
    ):
        response.headers["Cache-Control"] = "no-store"
        return call(
            query.current,
            scope_id,
            horizon=horizon,
            owner_id=owner(owner_id) if owner_id is not None else None,
        )

    @router.get("/v2/actions/current", response_model=DecisionUiCurrentViewV2)
    def current_v2(
        response: Response,
        scope_id: Annotated[str, Query(min_length=1, max_length=192)],
        horizon: Literal["30m", "24h", "72h"] = "24h",
        owner_id: OwnerHeader = None,
    ):
        response.headers["Cache-Control"] = "no-store"
        return call(
            query.current_v2,
            scope_id,
            horizon=horizon,
            owner_id=owner(owner_id) if owner_id is not None else None,
        )

    @router.get("/v1/actions/reviews", response_model=DecisionUiReviewViewV1)
    def reviews(response: Response, opportunity_id: Identity):
        response.headers["Cache-Control"] = "no-store"
        return call(query.reviews, opportunity_id)

    @router.get("/v1/actions/tracking", response_model=DecisionUiTrackingViewV1)
    def tracking(
        response: Response,
        scope_id: Annotated[str, Query(min_length=1, max_length=192)],
        horizon: Literal["30m", "24h", "72h"] = "24h",
        cursor: Identity | None = None,
    ):
        response.headers["Cache-Control"] = "no-store"
        return call(query.tracking, scope_id, horizon=horizon, cursor=cursor)

    @router.get("/v1/actions/plans/{plan_id}", response_model=DecisionUiPlanViewV1)
    def plan(
        response: Response,
        plan_id: str,
        revision_id: Identity | None = None,
        cursor: Identity | None = None,
        owner_id: OwnerHeader = None,
    ):
        response.headers["Cache-Control"] = "no-store"
        return call(
            query.plan,
            plan_id,
            revision_id=revision_id,
            cursor=cursor,
            owner_id=owner(owner_id) if owner_id is not None else None,
        )

    @router.post("/v1/actions/plans/{plan_id}/commands", response_model=DecisionUiCommandReceiptV1)
    def command(
        response: Response,
        plan_id: str,
        body: DecisionUiCommandV1,
        owner_id: OwnerHeader = None,
    ):
        response.headers["Cache-Control"] = "no-store"
        verified_owner = owner(owner_id)
        # A disabled producer never prevents recovery of an already accepted
        # request. The service still rejects a changed body using the same key.
        if (
            not enabled
            and call(commands.get_command, owner_id=verified_owner, request_id=body.request_id)
            is None
        ):
            raise HTTPException(503, "action_product_disabled")
        result = call(
            commands.record_command,
            owner_id=verified_owner,
            plan_id=plan_id,
            request_id=body.request_id,
            expected_revision_id=body.expected_revision_id,
            expected_state_version=body.expected_state_version,
            operation=body.operation,
            payload=body.payload,
        )
        return DecisionUiCommandReceiptV1.model_validate(result.wire())

    @router.get("/v1/actions/commands/{request_id}", response_model=DecisionUiCommandReceiptV1)
    def receipt(response: Response, request_id: str, owner_id: OwnerHeader = None):
        response.headers["Cache-Control"] = "no-store"
        result = call(commands.get_command, owner_id=owner(owner_id), request_id=request_id)
        if result is None:
            raise HTTPException(404, "action_command_not_found")
        return DecisionUiCommandReceiptV1.model_validate(result.wire())

    return router
