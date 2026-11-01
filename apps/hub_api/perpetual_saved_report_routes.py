"""Explicit V5 saved-report scope; exact JSON only, no V1 frozen export fallback."""

import re

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool


def perpetual_saved_report_router(query, *, owner, result_query=None):
    router = APIRouter()

    async def read(run_id, request, response):
        # Version classification contains no report/owner content. Old reports
        # retain their existing public V1 route without a new pairing requirement.
        if not await run_in_threadpool(query.applicable, run_id):
            raise HTTPException(404, "perpetual_report_not_applicable")
        identity = owner(request, response, False)
        try:
            return await run_in_threadpool(query.read, identity, run_id)
        except PermissionError as exc:
            raise HTTPException(403, "perpetual_report_owner_mismatch") from exc
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise HTTPException(409, "perpetual_report_original_unavailable") from exc

    @router.get("/research/runs/{run_id}/report", response_model=None)
    async def current(run_id: str, request: Request, response: Response):
        value = await read(run_id, request, response)
        response.headers["X-Decision-Hub-Report-Read-Mode"] = value["read_scope"]
        return value

    @router.get("/research/runs/{run_id}/report/source")
    async def download(run_id: str, request: Request, response: Response):
        value = await read(run_id, request, response)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)[:128]
        return Response(
            value["source_json"].encode(),
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "ETag": '"' + value["source_sha256"] + '"',
                "X-Decision-Hub-Report-Read-Mode": value["read_scope"],
                "Content-Disposition": (
                    f'attachment; filename="perpetual-research-{safe_id}-source.json"'
                ),
            },
        )

    if result_query is not None:

        @router.get("/research/results/{run_id}", response_model=None)
        async def forecast_result(run_id: str, request: Request, response: Response):
            try:
                applicable = await run_in_threadpool(result_query.applicable, run_id)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                raise HTTPException(
                    409, "perpetual_forecast_result_original_unavailable"
                ) from exc
            if not applicable:
                raise HTTPException(404, "perpetual_forecast_result_not_applicable")
            identity = owner(request, response, False)
            try:
                return await run_in_threadpool(result_query.read, identity, run_id)
            except PermissionError as exc:
                raise HTTPException(
                    403, "perpetual_forecast_result_owner_mismatch"
                ) from exc
            except (ValueError, TypeError, KeyError, OSError) as exc:
                allowed = {
                    "perpetual_forecast_result_not_applicable",
                    "perpetual_forecast_result_publication_unavailable",
                    "perpetual_forecast_result_experience_duplicate",
                    "perpetual_forecast_result_original_unavailable",
                    "perpetual_forecast_result_binding_mismatch",
                }
                code = str(exc)
                if code == "perpetual_forecast_result_not_applicable":
                    raise HTTPException(404, code) from exc
                raise HTTPException(
                    409,
                    code
                    if code in allowed
                    else "perpetual_forecast_result_original_unavailable",
                ) from exc

    return router
