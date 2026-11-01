"""Normal local-owner read route; no native callback credentials or mutations."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from packages.kernel.decision_hub_kernel.application.skill_method_view import (
    MethodView,
    ResearchMethodViewV2,
    SkillMethodQuery,
)

type MethodProjection = MethodView | ResearchMethodViewV2


class MethodProjectionPending(RuntimeError):
    pass


@dataclass(frozen=True)
class _MethodRead:
    frontier: str
    future: Future[MethodProjection]


class MethodReadCoordinator:
    """One low-priority retained-original verification per Run/revision."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="method-projection"
        )
        self._reads: dict[tuple[str, str | None], _MethodRead] = {}

    def read(
        self,
        service: SkillMethodQuery,
        run_id: str,
        revision_id: str | None,
        frontier: str,
    ) -> MethodProjection:
        slot = (run_id, revision_id)
        with self._lock:
            current = self._reads.get(slot)
            if current is not None and not current.future.done():
                raise MethodProjectionPending("method_projection_pending")
            if current is None or current.frontier != frontier:
                current = _MethodRead(
                    frontier,
                    self._executor.submit(service.read, run_id, revision_id),
                )
                self._reads[slot] = current
                raise MethodProjectionPending("method_projection_pending")
            future = current.future
        try:
            return future.result()
        except Exception:
            # A transient retained-original/storage failure must remain visible
            # to this request, but it must not poison this frontier forever.
            # Remove only the exact completed Future; a newer frontier/read may
            # already own the slot by the time the exception is observed.
            with self._lock:
                saved = self._reads.get(slot)
                if saved is not None and saved.future is future:
                    self._reads.pop(slot, None)
            raise


def skill_method_router(
    service: SkillMethodQuery | None, require_owner: Callable[[str | None], str]
):
    router = APIRouter(tags=["research-methods"])
    coordinator = MethodReadCoordinator()

    @router.get(
        "/v1/research/runs/{run_id}/methods", response_model=MethodView | ResearchMethodViewV2
    )
    def methods(
        run_id: str,
        revision_id: str | None = Query(None, min_length=1, max_length=128),
        x_owner_id: str | None = Header(None),
    ):
        require_owner(x_owner_id)
        if service is None:
            raise HTTPException(503, "method_service_unavailable")
        try:
            frontier = service.frontier(run_id, revision_id)
            if frontier is None:
                return service.read(run_id, revision_id)
            return coordinator.read(service, run_id, revision_id, frontier)
        except MethodProjectionPending as error:
            raise HTTPException(
                503,
                "method_projection_pending",
                headers={"Retry-After": "2"},
            ) from error
        except (ValueError, KeyError, TypeError, PermissionError, OSError) as error:
            code = str(error)
            if not code.startswith(("skill_", "method_", "action_review_")) or len(code) > 160:
                code = "method_original_verification_failed"
            raise HTTPException(404 if code == "method_run_missing" else 409, code) from error
        except SQLAlchemyError as error:
            raise HTTPException(503, "method_storage_unavailable") from error

    return router
