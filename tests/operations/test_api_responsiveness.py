from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import Database


@pytest.mark.parametrize("path,streaming", [
    ("/v1/research/runs/missing", False),
    ("/v1/research/runs/missing/events", False),
    ("/v1/research/runs/complete/events", True),
])
def test_blocked_report_read_does_not_block_health_or_delivery(
    tmp_path: Path, monkeypatch, path, streaming,
):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'responsive.sqlite3'}")
    app = create_app(database, source_connectors=[], sources_enabled=False)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_get(_run_id, **_kwargs):
        started.set()
        release.wait(2)
        finished.set()
        return [] if streaming else None

    if streaming:
        monkeypatch.setattr(app.state.research_query, "trace", slow_get)
        monkeypatch.setattr(app.state.research_query, "get",
                            lambda _: SimpleNamespace(run=SimpleNamespace(status="completed")))
    else:
        monkeypatch.setattr(app.state.research_query, "get", slow_get)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            blocked = asyncio.create_task(client.get(path))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                health = await client.get("/health/live")
                delivery = await client.get("/v1/product/reports", headers={"X-Owner-Id": "owner"})
                assert health.status_code == delivery.status_code == 200
                assert not finished.is_set(), "slow report query blocked unrelated API requests"
            finally:
                release.set()
                assert (await blocked).status_code == (200 if streaming else 404)

    try:
        asyncio.run(check())
    finally:
        database.engine.dispose()


def test_slow_request_log_uses_route_template_and_does_not_log_secrets(caplog):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    from apps.hub_api.request_timing import SlowRequestLoggingMiddleware

    app = FastAPI()
    app.add_middleware(SlowRequestLoggingMiddleware, threshold_seconds=0)

    @app.get("/items/{item_id}")
    def item(item_id: str):
        return {"ok": True}

    @app.get("/events")
    def events():
        return StreamingResponse(iter(["data: ok\n\n"]), media_type="text/event-stream")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/items/private-id?token=secret-value")).status_code == 200
            assert (await client.get("/events")).status_code == 200

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        asyncio.run(check())
    assert "route=/items/{item_id} status=200" in caplog.text
    assert "private-id" not in caplog.text and "secret-value" not in caplog.text
    assert "route=/events status=200" not in caplog.text
    # At threshold zero, waiting for the SSE headers is still observable.


def test_embedded_migrations_preserve_running_application_loggers(tmp_path):
    import logging

    api_logger = logging.getLogger("uvicorn.error")
    worker_logger = logging.getLogger("decision_hub.test_worker")
    handler = logging.NullHandler()
    api_logger.addHandler(handler)
    api_logger.disabled = worker_logger.disabled = False
    before = tuple(logging.getLogger().handlers)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'logging.sqlite3'}")
    try:
        database.initialize()
        database.initialize()
        assert not api_logger.disabled and not worker_logger.disabled
        assert handler in api_logger.handlers
        assert tuple(logging.getLogger().handlers) == before
    finally:
        database.engine.dispose()
        api_logger.removeHandler(handler)


def test_pending_request_reports_sql_wait_without_logging_statement_or_values(tmp_path, caplog):
    from fastapi import FastAPI
    from sqlalchemy import text

    from apps.hub_api.request_timing import (
        DatabaseRequestDiagnostics,
        SlowRequestLoggingMiddleware,
    )

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'blocked.sqlite3'}")
    with db.engine.begin() as connection:
        connection.execute(text("CREATE TABLE secret_table (value TEXT)"))
    diagnostics = DatabaseRequestDiagnostics(db.engine)
    holder = db.engine.connect()
    holder.exec_driver_sql("BEGIN IMMEDIATE")
    app = FastAPI()
    app.add_middleware(SlowRequestLoggingMiddleware, threshold_seconds=0.03, database=diagnostics)

    @app.post("/items/{item_id}")
    def save(item_id: str):
        with db.engine.begin() as connection:
            connection.execute(text("INSERT INTO secret_table VALUES (:value)"), {"value": item_id})
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(client.post("/items/private-value?token=private-token"))
            try:
                async with asyncio.timeout(2):
                    while "phase=pending" not in caplog.text:
                        await asyncio.sleep(0.01)
                assert not task.done()
                assert (await client.get("/health")).status_code == 200
                assert "db_queries_active=1" in caplog.text
                assert "thread_tokens=1" in caplog.text
                assert "status=None" in caplog.text
            finally:
                holder.rollback()
                assert (await task).status_code == 200
        assert diagnostics.snapshot()["db_queries_active"] == 0

    try:
        with caplog.at_level("WARNING", logger="uvicorn.error"):
            asyncio.run(check())
        assert "phase=finished" in caplog.text and "route=/items/{item_id}" in caplog.text
        for secret in ["private-value", "private-token", "secret_table", "INSERT"]:
            assert secret not in caplog.text
    finally:
        holder.close()
        db.engine.dispose()


def test_failed_sql_does_not_leave_phantom_active_query():
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from apps.hub_api.request_timing import DatabaseRequestDiagnostics

    database = Database("sqlite+pysqlite:///:memory:")
    diagnostics = DatabaseRequestDiagnostics(database.engine)
    try:
        with pytest.raises(OperationalError), database.engine.connect() as conn:
            conn.execute(text("SELECT * FROM table_that_does_not_exist"))
        assert diagnostics.snapshot()["db_queries_active"] == 0
    finally:
        database.engine.dispose()


def test_normal_long_sse_body_does_not_log_pending_request(caplog):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    from apps.hub_api.request_timing import SlowRequestLoggingMiddleware

    app = FastAPI()
    app.add_middleware(SlowRequestLoggingMiddleware, threshold_seconds=0.01)

    @app.get("/events")
    async def events():
        async def body():
            yield "data: begin\n\n"
            await asyncio.sleep(0.06)
            yield "data: end\n\n"
        return StreamingResponse(body(), media_type="text/event-stream")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/events")).status_code == 200
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        asyncio.run(check())
    assert "slow_http_request" not in caplog.text
