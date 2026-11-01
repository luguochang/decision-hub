"""Read only advice under the existing signed settings owner authority."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool


def perpetual_advice_router(query, *, owner):
    router = APIRouter()

    @router.get("/advice/current", response_model=None)
    async def current(
        request: Request,
        response: Response,
        account_kind: Literal["paper", "user_declared"],
        account_id: str,
        instrument_id: str,
        profile_ref: str,
        expected_settings_ref: str,
        expected_settings_hash: str,
        expected_settings_state_version: int = Query(ge=1),
        horizon: Literal["4h", "24h", "72h"] = "4h",
    ):
        identity = owner(request, response, False)
        try:
            return await run_in_threadpool(
                query.current,
                identity,
                account_kind=account_kind,
                account_id=account_id,
                instrument_id=instrument_id,
                profile_ref=profile_ref,
                expected_settings_ref=expected_settings_ref,
                expected_settings_hash=expected_settings_hash,
                expected_settings_state_version=expected_settings_state_version,
                horizon=horizon,
            )
        except ValidationError as exc:
            raise HTTPException(422, "perpetual_advice_query_invalid") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, "perpetual_advice_original_unavailable") from exc

    return router
