"""Bounded request diagnostics without SQL, bodies, URLs or business identifiers."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from uuid import uuid4

from anyio.to_thread import current_default_thread_limiter
from sqlalchemy import event
from sqlalchemy.engine import Engine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("uvicorn.error")


class DatabaseRequestDiagnostics:
    """Track timings only; statements and parameters never leave SQLAlchemy."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self._lock = Lock()
        self._active: dict[int, float] = {}
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)
        event.listen(engine, "handle_error", self._error)

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        with self._lock:
            self._active[id(context)] = perf_counter()

    def _after(self, conn, cursor, statement, parameters, context, executemany):
        self._finish(context)

    def _error(self, error_context):
        self._finish(error_context.execution_context)

    def _finish(self, context):
        with self._lock:
            self._active.pop(id(context), None)

    def snapshot(self) -> dict[str, int | float | None]:
        with self._lock:
            starts = tuple(self._active.values())
        checkedout = getattr(self.engine.pool, "checkedout", None)
        return {
            "db_checked_out": checkedout() if checkedout else None,
            "db_queries_active": len(starts),
            "db_oldest_query_ms": round((perf_counter() - min(starts)) * 1000, 1)
            if starts else 0,
        }


class SlowRequestLoggingMiddleware:
    def __init__(
        self, app: ASGIApp, threshold_seconds: float = 1.0,
        database: DatabaseRequestDiagnostics | None = None,
    ):
        self.app = app
        self.threshold_seconds = threshold_seconds
        self.database = database

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = perf_counter()
        request_id = uuid4().hex
        # Only server-generated context can join model phases to this request.
        # Header/query/body values never select or overwrite this diagnostic ID.
        scope["decision_hub_request_id"] = request_id
        status, streaming, first_byte_ms = None, False, None
        loop_delay_ms = None

        def record(phase):
            # Only matched route templates are safe: never fall back to raw paths.
            route = getattr(scope.get("route"), "path", "<unmatched>")
            limiter = current_default_thread_limiter().statistics()
            metrics = self.database.snapshot() if self.database else {}
            logger.warning(
                "slow_http_request method=%s route=%s status=%s duration_ms=%.1f "
                "phase=%s request_id=%s observed_at=%s first_byte_ms=%s "
                "thread_tokens=%s thread_limit=%s thread_waiting=%s "
                "db_checked_out=%s db_queries_active=%s db_oldest_query_ms=%s "
                "watchdog_delay_ms=%s",
                scope["method"], route, status, (perf_counter() - started) * 1000,
                phase, request_id, datetime.now(UTC).isoformat(), first_byte_ms,
                limiter.borrowed_tokens, limiter.total_tokens, limiter.tasks_waiting,
                metrics.get("db_checked_out"), metrics.get("db_queries_active"),
                metrics.get("db_oldest_query_ms"), loop_delay_ms,
            )

        async def pending():
            nonlocal loop_delay_ms
            await asyncio.sleep(self.threshold_seconds)
            loop_delay_ms = round(
                max(0, perf_counter() - started - self.threshold_seconds) * 1000, 1
            )
            if not streaming:
                record("pending")

        def binding_observation(
            phase: str, state: str = "finished", error: BaseException | None = None,
        ):
            observer = scope.get("decision_hub_model_binding_response_observer")
            if not callable(observer):
                return
            try:
                observer(phase, state, error)
            except Exception:
                # A diagnostic callback cannot mask the actual ASGI send outcome.
                pass

        def provider_observation(
            phase: str, state: str = "finished", error: BaseException | None = None,
        ):
            observer = scope.get("decision_hub_provider_callback_response_observer")
            if callable(observer):
                try:
                    observer(phase, state, error, status_code=status)
                except Exception:
                    # Observability never changes the real ASGI send or its error.
                    pass

        def risk_observation(phase: str, state: str = "finished", error: BaseException | None = None):
            observer = scope.get("decision_hub_risk_summary_response_observer")
            if callable(observer):
                try:
                    observer(phase, state, observed_at_utc=datetime.now(UTC).isoformat(),
                             request_elapsed_ms=round((perf_counter() - started) * 1000, 3),
                             status_code=status, error_type=type(error).__name__ if error else None)
                except Exception:
                    # Diagnostics cannot change actual ASGI send or exception behavior.
                    pass

        async def timed_send(message: Message):
            nonlocal status, streaming, first_byte_ms
            if message["type"] == "http.response.start":
                status = message["status"]
                if callable(scope.get("decision_hub_risk_summary_response_observer")):
                    headers = [(key, value) for key, value in message.get("headers", [])
                               if key.lower() != b"x-decision-hub-request-id"]
                    message = {**message, "headers": headers + [
                        (b"x-decision-hub-request-id", request_id.encode("ascii"))]}
                risk_observation("risk_response_start")
                first_byte_ms = round((perf_counter() - started) * 1000, 1)
                streaming = any(
                    key.lower() == b"content-type" and b"text/event-stream" in value.lower()
                    for key, value in message.get("headers", [])
                )
                binding_observation("response_ready")
                provider_observation("response_ready")
            try:
                await send(message)
            except BaseException as error:
                binding_observation("response_send", "error", error)
                provider_observation("response_send", "error", error)
                risk_observation("risk_send_error", "error", error)
                raise
            if message["type"] == "http.response.start":
                binding_observation("response_headers_sent")
                provider_observation("response_headers_sent")
                risk_observation("risk_headers_sent")
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                binding_observation("response_body_sent")
                provider_observation("response_body_sent")
                risk_observation("risk_final_body_sent")

        watchdog = asyncio.create_task(pending())
        app_error = None
        try:
            await self.app(scope, receive, timed_send)
        except BaseException as error:
            app_error = error
            raise
        finally:
            try:
                watchdog.cancel()
                await asyncio.gather(watchdog, return_exceptions=True)
                if perf_counter() - started >= self.threshold_seconds and not streaming:
                    record("finished")
            except BaseException as cleanup_error:
                risk_observation("risk_cleanup_error", "error", cleanup_error)
                if app_error is None:
                    app_error = cleanup_error
                raise
            finally:
                risk_observation("risk_asgi_finally", "error" if app_error else "finished", app_error)
