"""Serve PC document recovery acceptance in a fresh, explicitly isolated directory.

Uses the actual API, text parser, deterministic correction and document Worker.
Seeds one parse fault, one correction fault and a ready document. No model or
source execution is enabled; requests are never proxied to another API.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ISOLATION_LABEL = "PC 隔离文档恢复验收 · 独立数据库 · 非市场建议 · 不执行模型研究"
UPLOAD_LABEL = "PC 隔离上传恢复验收 · 独立数据库 · 预期提交后503 · 不执行模型研究"


@dataclass(frozen=True)
class UploadProbeOptions:
    withhold_sha256: str | None = None
    hold_parse_start: bool = False
    hold_parse_finish: bool = False
    hold_response_sha256: str | None = None
    gate_timeout_seconds: float = 240


def write_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def database_snapshot(path: Path) -> dict:
    """No application session or migration; the verifier reuses this read-only view."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        tables = (
            "research_documents", "research_document_versions", "research_document_blocks",
            "research_document_corrections", "analyst_claims", "product_commands", "runs",
            "artifacts", "run_calls", "research_tool_call_reservations", "evolution_planning_calls",
        )
        return {
            "counts": {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in tables},
            "documents": [dict(row) for row in connection.execute(
                "SELECT * FROM research_documents",
            )],
            "versions": [dict(row) for row in connection.execute(
                "SELECT * FROM research_document_versions",
            )],
            "commands": [dict(row) for row in connection.execute("SELECT * FROM product_commands")],
            "blocks": [{key: dict(row)[key] for key in (
                "block_id", "version_id", "page_no", "content_hash", "locator",
            )} for row in connection.execute("SELECT * FROM research_document_blocks")],
        }
    finally:
        connection.close()


class ControlledParser:
    """An explicit local gate; underlying bytes still go to the actual parser."""

    def __init__(self, parser, controls: Path, stop: threading.Event, options: UploadProbeOptions):
        self.parser, self.controls, self.stop, self.options = parser, controls, stop, options
        self.gates = []
        write_json(controls.parent / "parser-gates.json", self.gates)

    def parse(self, document):
        if self.options.hold_parse_finish:
            started = time.monotonic()
            event = {"version_id": document.version_id,
                     "started_at": datetime.now(UTC).isoformat(), "gate": "parse_finish",
                     "underlying_parser_called": False}
            self.gates.append(event)
            write_json(self.controls.parent / "parser-gates.json", self.gates)
            deadline = time.monotonic() + self.options.gate_timeout_seconds
            while not (self.controls / "release-parse-finish").is_file():
                if self.stop.wait(0.05):
                    raise RuntimeError("pc_upload_probe_stopped")
                if time.monotonic() > deadline:
                    raise TimeoutError("pc_upload_probe_parse_gate_timeout")
            event.update({"released_at": datetime.now(UTC).isoformat(),
                          "harness_hold_seconds": time.monotonic() - started,
                          "underlying_parser_called": True})
            write_json(self.controls.parent / "parser-gates.json", self.gates)
        return self.parser.parse(document)


async def document_ticks(worker, root: Path, options: UploadProbeOptions | None = None,
                         stop: threading.Event | None = None) -> None:
    """Audit one bad parser tick, then continue; CancelledError always propagates."""
    events = []
    write_json(root / "worker-events.json", events)
    while True:
        held = bool(options and options.hold_parse_start
                    and not (root / "controls/release-parse-start").is_file())
        state = {"alive": True, "checked_at": datetime.now(UTC).isoformat(),
                 "parse_start_held": held}
        write_json(root / "worker-status.json", state)
        if held:
            worker._beat()
        else:
            try:
                version_id = await worker.tick()
            except Exception as exc:
                event = {"sequence": len(events) + 1, "kind": "tick_failed",
                         "at": datetime.now(UTC).isoformat(), "error_type": type(exc).__name__,
                         "versions": database_snapshot(
                             worker.service.database.sqlite_path,
                         )["versions"]}
                events.append(event)
                write_json(root / "worker-events.json", events)
                state["last_error_type"] = type(exc).__name__
            else:
                if version_id:
                    events.append({"sequence": len(events) + 1, "kind": "tick_processed",
                                   "at": datetime.now(UTC).isoformat(), "version_id": version_id})
                    write_json(root / "worker-events.json", events)
                    state["last_processed_version_id"] = version_id
        write_json(root / "worker-status.json", state)
        await asyncio.sleep(0.25)


def validate_output(output: Path) -> Path:
    """Pure guard: inspect paths before making directories or importing the API."""
    root = output.resolve()
    allowed = (REPO / "data/product-local/remediation").resolve()
    if root == allowed or not root.is_relative_to(allowed):
        raise ValueError("output must be a new directory beneath data/product-local/remediation")
    if os.path.lexists(root):
        raise ValueError("output already exists; use a new directory, never reuse a database")
    return root


def port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def configure_isolation(root: Path) -> None:
    runtime = root / "runtime"
    os.environ.update({
        "DECISION_HUB_DATA_DIR": str(runtime),
        "DECISION_HUB_CHECKPOINT_PATH": str(runtime / "checkpoints" / "probe.sqlite3"),
        "DECISION_HUB_DOCUMENT_BUNDLE_ROOT": str(runtime / "document-bundles"),
        "DECISION_HUB_SOURCE_ACQUISITION_ROOT": str(runtime / "acquisitions"),
        "DECISION_HUB_LLM_ENABLED": "0",
        "DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED": "0",
        "DECISION_HUB_SOURCES_ENABLED": "0",
        "DECISION_HUB_CALENDAR_DISCOVERY_ENABLED": "0",
        "DECISION_HUB_MARKET_ENABLED": "0",
        "DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED": "0",
        "DECISION_HUB_OPERATIONS_RESEARCH_RUNTIME": "",
    })


class InjectedParserFault:
    def parse(self, _document):
        raise RuntimeError("pc_acceptance_injected_parse_fault")


class InjectedCorrectionFault:
    async def correct(self, *_args, **_kwargs):
        raise RuntimeError("pc_acceptance_injected_correction_fault")


async def seed(library, root: Path, port: int) -> dict:
    from packages.contracts_py.decision_hub_contracts import DocumentUpload

    versions = {}
    for phase in ("parse", "correction", "ready"):
        payload = DocumentUpload(
            schema_version="document-upload.v1", title=f"PC 隔离验收资料：{phase}",
            source_type="private_file", market_scopes=["crypto_macro"],
            instrument_scopes=["BTC-USDT"], privacy_mode="local_only", visibility="private",
            notes="仅用于 PC 隔离故障验收，不是真实市场建议。",
        )
        version = library.upload(
            payload, filename=f"acceptance-{phase}.txt",
            data=(f"PC 隔离验收文档（{phase}），非市场建议。\n"
                  "BTC 看多，目标上涨10%。\nBTC 看空，可能下跌。\n").encode(),
        )
        parser, correction = library.parser, library.correction
        injected = False
        try:
            if phase == "parse":
                library.parser = InjectedParserFault()
            await library.process_next(worker_id="pc-document-probe-seed")
            if phase != "parse":
                library.request_correction(version.version_id)
                if phase == "correction":
                    library.correction = InjectedCorrectionFault()
                await library.process_next(worker_id="pc-document-probe-seed")
                await library.accept_correction(version.version_id)
        except RuntimeError as exc:
            expected = f"pc_acceptance_injected_{phase}_fault"
            if phase == "ready" or str(exc) != expected:
                raise
            injected = True
        finally:
            # Both adapters are restored before the actual Worker starts. New
            # recoveries execute the real parser and correction, never the fault.
            library.parser, library.correction = parser, correction
        current = library.get_version(version.version_id)
        expected_status = "ready" if phase == "ready" else "failed"
        if current.status != expected_status or injected != (phase != "ready"):
            raise RuntimeError("pc_document_probe_seed_not_confirmed")
        scene = "document-ready" if phase == "ready" else "document-failed"
        versions[phase] = {
            "injected_fault_once": injected,
            "version": current.model_dump(mode="json"),
            "url": f"http://127.0.0.1:{port}/#/{scene}?version={version.version_id}",
        }
    result = {
        "isolation_label": ISOLATION_LABEL,
        "purpose": "PC isolated acceptance; one-time parse/correction faults",
        "paid_provider": False, "production_data": False, "proxy": False,
        "database": str(library.database.sqlite_path), "versions": versions,
    }
    (root / "seed.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


async def build_app(root: Path, frontend: Path, port: int,
                    options: UploadProbeOptions | None = None):
    # No project import, database construction or environment mutation occurs
    # before the CLI path guard has accepted a fresh output directory.
    root = validate_output(root)
    root.mkdir(parents=True, exist_ok=False)
    configure_isolation(root)
    sys.path.insert(0, str(REPO))

    from fastapi.responses import HTMLResponse, JSONResponse, Response

    from apps.hub_api.main import create_app
    from apps.hub_worker.documents import DocumentPipelineWorker
    from packages.provider_adapters.documents import (
        DocumentParserRouter,
        ImagePlaceholderDocumentParser,
        PypdfTextDocumentParser,
    )

    app = create_app(source_connectors=[], sources_enabled=False, frontend_dir=frontend)
    library = app.state.research_library
    # Use the real local text/PDF parser explicitly; inherited MinerU command
    # configuration must not introduce an external OCR service into this probe.
    library.parser = DocumentParserRouter((
        PypdfTextDocumentParser(library.store), ImagePlaceholderDocumentParser(library.store),
    ))
    controls = root / "controls"
    controls.mkdir()
    stop = threading.Event()
    if options:
        library.parser = ControlledParser(library.parser, controls, stop, options)
        result = {
            "isolation_label": UPLOAD_LABEL, "mode": "upload", "paid_provider": False,
            "production_data": False, "proxy": False, "worker_role": "documents_only",
            "database": str(app.state.database.sqlite_path), "base_url": f"http://127.0.0.1:{port}",
            "owner": "owner", "versions": {},
            "baseline": database_snapshot(library.database.sqlite_path),
            "fault_semantics": "actual upload commit202 replaced by HTTP503; not TCP disconnect",
            "options": {"withhold_sha256": options.withhold_sha256,
                        "hold_parse_start": options.hold_parse_start,
                        "hold_parse_finish": options.hold_parse_finish,
                        "hold_response_sha256": options.hold_response_sha256,
                        "gate_timeout_seconds": options.gate_timeout_seconds},
            "controls": {"parse_start": str(controls / "release-parse-start"),
                         "parse_finish": str(controls / "release-parse-finish"),
                         "response": str(controls / "release-upload-response")},
        }
        write_json(root / "seed.json", result)
    else:
        result = await seed(library, root, port)
    worker = DocumentPipelineWorker(
        library, app.state.database, service_id="pc-document-recovery-probe",
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(current_app):
        async with original_lifespan(current_app):
            task = asyncio.create_task(document_ticks(worker, root, options, stop))
            try:
                yield
            finally:
                stop.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                write_json(root / "worker-status.json", {
                    "alive": False, "stopped_at": datetime.now(UTC).isoformat(),
                })

    app.router.lifespan_context = lifespan
    banner = (
        '<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;'
        'background:#ffecb3;color:#332500;padding:8px;text-align:center">'
        f"{UPLOAD_LABEL if options else ISOLATION_LABEL}</div>"
    )
    document_write = re.compile(
        r"^/v1/research-library/(documents|uploads|web-import|"
        r"versions/[^/]+/(recover|correction|correction/accept)|claims/[^/]+/review)$"
    )
    journal = []
    withheld = False
    response_held = False
    receipt_get = re.compile(r"^/v1/research-library/uploads/[^/]+$")
    if options:
        write_json(root / "requests.json", journal)

    @app.middleware("http")
    async def isolate(request, call_next):
        nonlocal withheld, response_held
        path = request.url.path
        if request.method == "GET" and path in {"/", "/index.html"}:
            html = (frontend / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(html.replace("</body>", banner + "</body>"))
        if request.method not in {"GET", "HEAD"} and (
            request.method != "POST" or not document_write.fullmatch(path)
        ):
            if options:
                journal.append({"sequence": len(journal) + 1, "method": request.method,
                                "path": path, "kind": "guard_rejection",
                                "received_at": datetime.now(UTC).isoformat(),
                                "application_status": None, "delivered_status": 405,
                                "expected_fault": False})
                write_json(root / "requests.json", journal)
            return JSONResponse({"detail": "pc_document_probe_write_not_allowed"}, status_code=405)
        audited = options and (
            request.method not in {"GET", "HEAD"} or receipt_get.fullmatch(path)
        )
        if not audited:
            return await call_next(request)
        entry = {
            "sequence": len(journal) + 1, "method": request.method, "path": path,
            "received_at": datetime.now(UTC).isoformat(),
            "request_id": request.headers.get("Idempotency-Key")
            if request.method == "POST" else path.rsplit("/", 1)[1],
            "owner_header": request.headers.get("X-Owner-Id"),
            "evidence_only": request.method == "GET" and request.headers.get(
                "X-PC-Probe-Evidence",
            ) == "readonly-verifier",
        }
        journal.append(entry)
        write_json(root / "requests.json", journal)
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        receipt = json.loads(body)
        entry.update({"application_status": response.status_code, "receipt": receipt,
                      "application_returned_at": datetime.now(UTC).isoformat(),
                      "receipt_sha256": hashlib.sha256(body).hexdigest()})
        intent = receipt.get("intent", {}) if isinstance(receipt, dict) else {}
        upload_committed = request.method == "POST" and path == "/v1/research-library/uploads" \
            and response.status_code == 202
        should_withhold = bool(upload_committed and options.withhold_sha256
                               == intent.get("file_sha256") and not withheld)
        if should_withhold:
            withheld = True
        entry.update({"expected_fault": should_withhold,
                      "committed_response_withheld": should_withhold,
                      "planned_delivered_status": 503 if should_withhold else response.status_code,
                      "delivered_status": None})
        if upload_committed and options.hold_response_sha256 == intent.get("file_sha256") \
                and not response_held:
            response_held = True
            entry["response_gate_started_at"] = datetime.now(UTC).isoformat()
            write_json(root / "requests.json", journal)
            deadline = time.monotonic() + options.gate_timeout_seconds
            while not (controls / "release-upload-response").is_file():
                if time.monotonic() > deadline:
                    entry["response_gate_timed_out"] = True
                    break
                await asyncio.sleep(0.05)
            entry["response_gate_released_at"] = datetime.now(UTC).isoformat()
        entry["delivered_at"] = datetime.now(UTC).isoformat()
        entry["delivered_status"] = entry["planned_delivered_status"]
        write_json(root / "requests.json", journal)
        if should_withhold:
            return JSONResponse({"detail": "pc_upload_probe_response_withheld_after_commit"},
                                status_code=503)
        return Response(body, status_code=response.status_code, headers=dict(response.headers))

    return app, result


async def serve(root: Path, frontend: Path, port: int,
                options: UploadProbeOptions | None = None) -> None:
    import uvicorn

    app, result = await build_app(root, frontend, port, options)

    print(json.dumps({
        "isolation_label": result["isolation_label"], "ready": f"http://127.0.0.1:{port}",
        "seed": str(root / "seed.json"),
        "versions": {key: value["url"] for key, value in result["versions"].items()},
    }, ensure_ascii=False), flush=True)
    await uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False),
    ).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=port_number, default=8071)
    parser.add_argument("--frontend", type=Path, default=REPO / "apps/decision-desk/dist")
    parser.add_argument("--upload-mode", action="store_true")
    parser.add_argument("--withhold-upload-sha256")
    parser.add_argument("--hold-parse-start", action="store_true")
    parser.add_argument("--hold-parse-finish", action="store_true")
    parser.add_argument("--hold-upload-response-sha256")
    parser.add_argument("--gate-timeout-seconds", type=float, default=240)
    args = parser.parse_args()
    try:
        root = validate_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    frontend = args.frontend.resolve()
    if not (frontend / "index.html").is_file() or not (frontend / "assets").is_dir():
        parser.error("frontend must contain a built index.html and assets directory")
    if not 0 < args.gate_timeout_seconds <= 240:
        parser.error("gate timeout must be between 0 and 240 seconds")
    for digest in (args.withhold_upload_sha256, args.hold_upload_response_sha256):
        if digest and not re.fullmatch(r"[a-f0-9]{64}", digest):
            parser.error("fault selectors must be lowercase SHA256 hex")
    if args.withhold_upload_sha256 and args.withhold_upload_sha256 \
            == args.hold_upload_response_sha256:
        parser.error("withheld and delayed response branches must select different file SHA256")
    if not args.upload_mode and any((args.withhold_upload_sha256, args.hold_parse_start,
                                     args.hold_parse_finish, args.hold_upload_response_sha256)):
        parser.error("upload controls require --upload-mode")
    options = UploadProbeOptions(
        args.withhold_upload_sha256, args.hold_parse_start, args.hold_parse_finish,
        args.hold_upload_response_sha256, args.gate_timeout_seconds,
    ) if args.upload_mode else None
    asyncio.run(serve(root, frontend, args.port, options))


if __name__ == "__main__":
    main()
