"""Serve isolated real Run commands and a real Worker with a failure-only runtime.

No Provider, DSH, source acquisition, formal proxy or AI report is involved.
The first accepted cancel commits normally then delivers explicit HTTP503;
the first accepted retry commits normally then holds its real HTTP200 response.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.ops.serve_document_recovery_probe import (  # noqa: E402
    configure_isolation,
    port_number,
    validate_output,
    write_json,
)

LABEL = "PC 隔离取消重试 · 独立数据库 · 受控执行失败 · 模型调用0 · 不生成AI报告"
OWNER = "owner"
FAILURE_CODE = "pc_command_probe_controlled_failure"
# The graph filters requirements by known capability names before calling the
# runtime port. No Gateway/adapter/client is installed in this failure-only tool.
CONTROL_CAPABILITIES = ("web.search",)
COUNTS = (
    "runs",
    "research_commands",
    "product_commands",
    "artifacts",
    "forecasts",
    "report_revisions",
    "outbox",
    "research_results",
    "run_calls",
    "research_tool_call_reservations",
    "evolution_planning_calls",
    "dsh_session_links",
    "dsh_session_prompts",
    "research_run_scopes",
)
LIBRARY_TABLES = (
    "research_documents",
    "research_document_versions",
    "research_document_blocks",
    "research_document_corrections",
    "analyst_claims",
)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def snapshot(database: Path, source_run_id: str) -> dict:
    """Explicit local SQLite read only; never initialize/migrate an application."""
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:

        def rows(table, where="", parameters=()):
            return [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} {where} ORDER BY rowid",
                    parameters,
                )
            ]

        all_runs = rows("runs")
        source = next(row for row in all_runs if row["run_id"] == source_run_id)
        historical = {
            table: rows(table)
            for table in LIBRARY_TABLES
            + (
                "events",
                "observations",
                "studies",
            )
        }
        historical["source_scope"] = rows("research_run_scopes", "WHERE run_id=?", (source_run_id,))
        historical["source_trigger"] = rows(
            "snapshots", "WHERE snapshot_id=?", (source["snapshot_id"],)
        )
        historical["source_task_pack"] = rows(
            "run_role_outputs",
            "WHERE run_id=? AND role IN ('research_task_request','research_request_pack')",
            (source_run_id,),
        )
        historical["non_research_product_commands"] = rows(
            "product_commands",
            "WHERE kind<>'research_command'",
        )
        return {
            "counts": {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in COUNTS
            },
            "runs": all_runs,
            "commands": rows("research_commands"),
            "receipts": rows("product_commands", "WHERE kind='research_command'"),
            "scopes": rows("research_run_scopes"),
            "historical": historical,
            "historical_hashes": {name: digest(value) for name, value in historical.items()},
        }
    finally:
        connection.close()


class ControlledFailureRuntime:
    """The real graph reaches this protocol port; it never receives a successful result."""

    runtime_id = "pc-controlled-failure"
    runtime_version = "pc-controlled-failure.v1"
    profile_ref = "crypto_macro.manager.v1"

    def __init__(self, root: Path, source_run_id: str, timeout_seconds: int):
        self.root, self.source_run_id, self.timeout_seconds = root, source_run_id, timeout_seconds
        self.events = []
        self.stopping = False
        write_json(root / "runtime-events.json", self.events)

    async def execute(self, request, trace_sink=None):
        from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

        release = (
            self.root
            / "controls"
            / (
                "release-source-failure"
                if request.run_id == self.source_run_id
                else "release-child-failure"
            )
        )
        event = {
            "sequence": len(self.events) + 1,
            "kind": "runtime_entered",
            "run_id": request.run_id,
            "at": datetime.now(UTC).isoformat(),
            "request_hash": digest(request.model_dump(mode="json")),
            "input_evidence_refs": [item.evidence_id for item in request.input_evidence],
            "library_input_refs": [
                item.evidence_id
                for item in request.input_evidence
                if item.evidence_id.startswith("library:")
            ],
            "excluded_inputs": [
                item.model_dump(mode="json") for item in request.context_manifest.excluded_inputs
            ],
            "model_calls": 0,
            "successful_result_returned": False,
        }
        self.events.append(event)
        write_json(self.root / "runtime-events.json", self.events)
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while not release.is_file() and not self.stopping:
                if time.monotonic() > deadline:
                    event["gate_timed_out"] = True
                    break
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            event.update(
                {
                    "kind": "cooperative_cancel",
                    "cancelled_at": datetime.now(UTC).isoformat(),
                    "cancelled_error_propagated": True,
                }
            )
            write_json(self.root / "runtime-events.json", self.events)
            raise
        event.update(
            {
                "released_at": datetime.now(UTC).isoformat(),
                "error_code": FAILURE_CODE,
                "kind": "runtime_failed",
                "shutdown_release": self.stopping,
            }
        )
        write_json(self.root / "runtime-events.json", self.events)
        raise AgentExecutionError(
            FAILURE_CODE,
            "PC隔离命令验收：执行端受控失败，没有运行模型或生成AI报告。",
            retryable=True,
            origin="orchestration",
            cause_code="pc_controlled_failure",
        )

    async def close(self):
        self.stopping = True


async def seed(app, root: Path, port: int) -> dict:
    from apps.hub_worker.research import ResearchRequestFactory
    from packages.contracts_py.decision_hub_contracts import (
        DocumentUpload,
        ObservationCreate,
        ResearchRequest,
    )
    from packages.contracts_py.decision_hub_contracts.models import RunStatus
    from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
    from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.provider_adapters.documents import (
        DocumentParserRouter,
        ImagePlaceholderDocumentParser,
        PypdfTextDocumentParser,
    )

    database, library = app.state.database, app.state.research_library
    library.parser = DocumentParserRouter(
        (
            PypdfTextDocumentParser(library.store),
            ImagePlaceholderDocumentParser(library.store),
        )
    )
    version = library.upload(
        DocumentUpload(
            schema_version="document-upload.v1",
            title="PC取消重试隔离资料（非市场研究）",
            source_type="private_file",
            market_scopes=["crypto_macro"],
            instrument_scopes=["BTC-USDT"],
            privacy_mode="local_only",
            visibility="private",
            notes="合成验收资料；仅验证本次授权不会被重试继承。",
        ),
        filename="pc-command-control.txt",
        owner=OWNER,
        data=(
            "这是一份 PC 取消与重试的隔离验收资料，不提供投资建议。\n"
            "该资料只验证本次研究的选择与授权记录，后续重试必须重新选择资料。\n"
            "执行端只返回明确受控失败，不调用模型、获取行情或生成研究报告。\n"
        ).encode(),
    )
    await library.process_next(worker_id="pc-command-seed-documents")
    library.request_correction(version.version_id)
    await library.process_next(worker_id="pc-command-seed-documents")
    await library.accept_correction(version.version_id)
    event_id, _, _ = AdmissionService(database).admit(
        ObservationCreate(
            text="PC隔离取消重试验收：BTC-USDT控制任务；非市场信息，不生成AI成功报告。",
            source_id="pc-research-command-probe",
            source_type="manual",
            language="zh",
        )
    )
    runs = RunService(database)
    source_run_id, _ = runs.create(event_id, "pc-command-source", strategy_version="research.v1")
    reference_run_id, _ = runs.create(
        event_id, "pc-command-reference", strategy_version="research.v1"
    )
    runs.set_status(reference_run_id, RunStatus.failed, error_code="pc_reference_seed_only")
    library.select_context(
        ResearchRequest(
            schema_version="research-request.v1",
            research_family="crypto_macro",
            instrument_scope=["BTC-USDT"],
            focus="PC隔离取消重试；仅合成资料，不做市场研究。",
            library_context_mode="selected",
            selected_version_ids=[version.version_id],
            allow_remote_library_content=True,
        ),
        run_id=source_run_id,
    )
    request = ResearchRequestFactory(
        database, pack_root=REPO / "packs/crypto_macro", allowed_capabilities=CONTROL_CAPABILITIES
    ).build(event_id, source_run_id)
    ResearchTaskService(database).freeze(request)
    result = {
        "isolation_label": LABEL,
        "production_data": False,
        "proxy": False,
        "paid_provider": False,
        "ai_success": False,
        "worker_role": "actual_durable_research_failure_only",
        "owner": OWNER,
        "database": str(database.sqlite_path),
        "base_url": f"http://127.0.0.1:{port}",
        "source_run_id": source_run_id,
        "reference_run_id": reference_run_id,
        "source_url": f"http://127.0.0.1:{port}/#/report?run={source_run_id}",
        "reference_url": f"http://127.0.0.1:{port}/#/report?run={reference_run_id}",
        "document_version_id": version.version_id,
        "controls": {
            name: str(root / "controls" / marker)
            for name, marker in (
                ("worker_start", "release-worker-start"),
                ("source_failure", "release-source-failure"),
                ("child_failure", "release-child-failure"),
                ("retry_response", "release-retry-response"),
            )
        },
        "fault_semantics": "actual cancel commit200 delivered HTTP503; not TCP disconnect",
        "late_commit_verified": False,
        "baseline": snapshot(database.sqlite_path, source_run_id),
    }
    if not any(item.evidence_id.startswith("library:") for item in request.input_evidence):
        raise RuntimeError("pc_command_source_library_input_not_selected")
    write_json(root / "seed.json", result)
    return result


async def build_app(root: Path, frontend: Path, port: int, *, gate_timeout_seconds: int = 900):
    """Build a fresh isolated app for CLI/TestClient; no listener starts here."""
    root = validate_output(root)
    if not 1 <= gate_timeout_seconds <= 1800:
        raise ValueError("gate timeout must be 1..1800 seconds")
    root.mkdir(parents=True, exist_ok=False)
    (root / "controls").mkdir()
    configure_isolation(root)
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    from apps.hub_api.main import create_app
    from apps.hub_worker.research import DurableResearchWorker

    app = create_app(source_connectors=[], sources_enabled=False, frontend_dir=frontend)
    seeded = await seed(app, root, port)
    runtime = ControlledFailureRuntime(root, seeded["source_run_id"], gate_timeout_seconds)
    worker = DurableResearchWorker(
        app.state.database,
        runtime,
        pack_root=REPO / "packs/crypto_macro",
        checkpoint_path=root / "runtime/checkpoints/research.sqlite3",
        worker_id="pc-command-research-worker",
        allowed_capabilities=CONTROL_CAPABILITIES,
        min_priority=100,
    )
    app.state.pc_command_runtime = runtime
    journal, worker_events = [], []
    withheld, held_response = False, False
    write_json(root / "requests.json", journal)
    write_json(root / "worker-events.json", worker_events)
    write_json(root / "worker-status.json", {"alive": False, "worker_start_held": True})
    command_path = re.compile(r"^/v1/research/runs/([^/]+)/commands$")
    receipt_path = re.compile(r"^/v1/research/runs/([^/]+)/commands/([^/]+)$")
    banner = (
        '<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;'
        'background:#ffecb3;color:#332500;padding:8px;text-align:center">'
        f"{LABEL}</div>"
    )

    async def worker_loop():
        started_at = datetime.now(UTC)
        while not runtime.stopping:
            held = not (root / "controls/release-worker-start").is_file()
            write_json(
                root / "worker-status.json",
                {
                    "alive": True,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "worker_start_held": held,
                },
            )
            app.state.heartbeats.beat(
                service_id="hub-research-worker",
                role="research_worker",
                instance_id="pc-command-research-worker",
                version="pc-control.v1",
                mode="local",
                interval_seconds=10,
                started_at=started_at,
            )
            if not held:
                report = await worker.tick()
                if report:
                    worker_events.append(
                        {
                            "at": datetime.now(UTC).isoformat(),
                            "report": report.model_dump(mode="json"),
                        }
                    )
                    write_json(root / "worker-events.json", worker_events)
            await asyncio.sleep(0.1)

    async def heartbeat_loop(worker_task):
        # The Worker remains executing in its real tick while the runtime is held.
        while not runtime.stopping:
            alive = not worker_task.done()
            write_json(root / "worker-status.json", {
                "alive": alive, "checked_at": datetime.now(UTC).isoformat(),
                "worker_start_held": not (root / "controls/release-worker-start").is_file(),
            })
            if not alive:
                return
            app.state.heartbeats.beat(
                service_id="hub-research-worker",
                role="research_worker",
                instance_id="pc-command-research-worker",
                version="pc-control.v1",
                mode="local",
                interval_seconds=10,
                started_at=datetime.now(UTC),
            )
            write_json(root / "heartbeat.json", {"checked_at": datetime.now(UTC).isoformat()})
            await asyncio.sleep(1)

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(current_app):
        async with original_lifespan(current_app):
            worker_task = asyncio.create_task(worker_loop())
            tasks = [worker_task, asyncio.create_task(heartbeat_loop(worker_task))]
            try:
                yield
            finally:
                await runtime.close()
                # Allow failure-only runtime to unwind the real tick and its lease task.
                try:
                    await asyncio.wait_for(tasks[0], timeout=3)
                except TimeoutError:
                    tasks[0].cancel()
                tasks[1].cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                write_json(
                    root / "worker-status.json",
                    {
                        "alive": False,
                        "stopped_at": datetime.now(UTC).isoformat(),
                    },
                )

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def isolate(request, call_next):
        nonlocal withheld, held_response
        path = request.url.path
        if request.method == "GET" and path in {"/", "/index.html"}:
            return HTMLResponse(
                (frontend / "index.html")
                .read_text(encoding="utf-8")
                .replace("</body>", banner + "</body>")
            )
        post_match = command_path.fullmatch(path) if request.method == "POST" else None
        get_match = receipt_path.fullmatch(path) if request.method == "GET" else None
        write = request.method not in {"GET", "HEAD"}
        payload = None
        if post_match:
            try:
                payload = await request.json()
            except (ValueError, UnicodeError):
                payload = None
        entry = {
            "sequence": len(journal) + 1,
            "method": request.method,
            "path": path,
            "received_at": datetime.now(UTC).isoformat(),
            "source_run_id": (post_match or get_match)[1] if post_match or get_match else None,
            "request_id": payload.get("request_id")
            if isinstance(payload, dict)
            else get_match[2]
            if get_match
            else None,
            "payload": payload,
            "payload_sha256": digest(payload) if payload else None,
            "owner_header": request.headers.get("X-Owner-Id"),
            "idempotency_header": request.headers.get("Idempotency-Key"),
            "evidence_only": request.headers.get("X-PC-Probe-Evidence") == "readonly-verifier",
            "application_status": None,
            "delivered_status": None,
            "committed_response_withheld": False,
            "response_held": False,
        }
        journal.append(entry)
        write_json(root / "requests.json", journal)
        if write:
            current = snapshot(app.state.database.sqlite_path, seeded["source_run_id"])
            allowed_ids = {seeded["source_run_id"]} | {
                row["run_id"]
                for row in current["runs"]
                if row["parent_run_id"] == seeded["source_run_id"]
            }
            allowed = post_match and post_match[1] in allowed_ids and isinstance(payload, dict)
            allowed = allowed and payload.get("command") in {"cancel", "retry"}
            allowed = allowed and request.headers.get("X-Owner-Id") in {None, OWNER}
            if not allowed:
                entry.update({"delivered_status": 405, "guard_rejection": True})
                write_json(root / "requests.json", journal)
                return JSONResponse(
                    {"detail": "pc_command_probe_write_not_allowed"}, status_code=405
                )
        response = await call_next(request)
        if not post_match and not get_match:
            entry.update(
                {
                    "application_status": response.status_code,
                    "delivered_status": response.status_code,
                }
            )
            write_json(root / "requests.json", journal)
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        receipt = json.loads(body)
        entry.update(
            {
                "application_status": response.status_code,
                "application_returned_at": datetime.now(UTC).isoformat(),
                "receipt": receipt,
                "receipt_sha256": digest(receipt),
            }
        )
        accepted = (
            post_match and response.status_code == 200 and receipt.get("status") == "accepted"
        )
        if accepted and payload["command"] == "cancel" and not withheld:
            withheld = True
            entry.update(
                {
                    "committed_response_withheld": True,
                    "expected_fault": True,
                    "delivered_status": 503,
                }
            )
            write_json(root / "requests.json", journal)
            return JSONResponse(
                {"detail": "pc_command_probe_response_withheld_after_commit"}, status_code=503
            )
        if accepted and payload["command"] == "retry" and not held_response:
            held_response = True
            entry["response_held"] = True
            write_json(root / "requests.json", journal)
            deadline = time.monotonic() + gate_timeout_seconds
            while not (root / "controls/release-retry-response").is_file() and not runtime.stopping:
                if time.monotonic() > deadline:
                    entry.update(
                        {"gate_timed_out": True, "expected_fault": True, "delivered_status": 503}
                    )
                    write_json(root / "requests.json", journal)
                    return JSONResponse(
                        {"detail": "pc_command_probe_response_gate_timeout"}, status_code=503
                    )
                await asyncio.sleep(0.05)
        entry.update(
            {
                "delivered_status": response.status_code,
                "delivered_at": datetime.now(UTC).isoformat(),
            }
        )
        write_json(root / "requests.json", journal)
        return Response(body, status_code=response.status_code, headers=dict(response.headers))

    return app, seeded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, default=REPO / "apps/decision-desk/dist")
    parser.add_argument("--port", type=port_number, default=8075)
    parser.add_argument("--gate-timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        root = validate_output(args.output)
        frontend = args.frontend.resolve()
        if not (frontend / "index.html").is_file() or not (frontend / "assets").is_dir():
            raise ValueError("frontend must contain a built index.html and assets directory")
        if not 1 <= args.gate_timeout_seconds <= 1800:
            raise ValueError("gate timeout must be 1..1800 seconds")
    except ValueError as error:
        parser.error(str(error))
    app, seeded = asyncio.run(
        build_app(root, frontend, args.port, gate_timeout_seconds=args.gate_timeout_seconds)
    )
    print(
        json.dumps(
            {
                "isolation_label": LABEL,
                "seed": str(root / "seed.json"),
                "source_url": seeded["source_url"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
