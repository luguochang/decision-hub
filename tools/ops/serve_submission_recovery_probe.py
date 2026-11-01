"""PC browser fault probe: real API admission in a fresh isolated SQLite database.

The first accepted submission's response is held until a manual retry (or 90 s).
No worker is started, no model executes, and no production endpoint is proxied.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8057)
    parser.add_argument(
        "--submission-kind", choices=["research", "observation"], default="research"
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    root = args.output.resolve()
    allowed = (repo / "data/product-local/remediation").resolve()
    if not root.is_relative_to(allowed) or root == allowed:
        parser.error("output must be a new directory beneath data/product-local/remediation")
    frontend = args.frontend.resolve()
    if not (frontend / "index.html").is_file():
        parser.error("frontend must contain a built index.html")
    # Refuse reuse: the probe must never change an existing database.
    root.mkdir(parents=True, exist_ok=False)
    os.environ["DECISION_HUB_DATA_DIR"] = str(root)
    os.environ["DECISION_HUB_CHECKPOINT_PATH"] = str(root / "checkpoints.sqlite3")
    os.environ["DECISION_HUB_SOURCE_ACQUISITION_ROOT"] = str(root / "acquisitions")
    os.environ["DECISION_HUB_SOURCES_ENABLED"] = "0"
    os.environ["DECISION_HUB_CALENDAR_DISCOVERY_ENABLED"] = "0"

    import uvicorn
    from fastapi.responses import HTMLResponse, JSONResponse

    from apps.hub_api.main import create_app
    from packages.kernel.decision_hub_kernel.persistence.db import RunStatus

    app = create_app(source_connectors=[], sources_enabled=False, frontend_dir=frontend)
    if args.submission_kind == "observation":
        # Legacy admission normally executes in an API background task. Explicitly
        # retain a failed diagnostic Run instead of running any analysis/model.
        class NoExecutionProbe:
            async def execute(self, request):
                app.state.analyzer.runs.set_status(
                    request.run_id, RunStatus.failed, error_code="pc_probe_execution_disabled"
                )

        app.state.analyzer.executor = NoExecutionProbe()
    release = asyncio.Event()
    submissions: list[dict] = []
    banner = (
        '<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;'
        'background:#ffecb3;color:#332500;padding:8px;text-align:center">'
        "隔离故障验收 · 独立数据库 · 首次提交响应延迟 · 不执行模型研究</div>"
    )

    @app.middleware("http")
    async def probe(request, call_next):
        path = request.url.path
        if request.method == "GET" and path == "/":
            html = (frontend / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(html.replace("</body>", banner + "</body>"))
        submission_path = (
            "/v1/observations"
            if args.submission_kind == "observation"
            else "/v1/research/observations"
        )
        is_submission = request.method == "POST" and path == submission_path
        if request.method not in {"GET", "HEAD"} and not is_submission:
            return JSONResponse({"detail": "probe_write_not_allowed"}, status_code=405)
        if not is_submission:
            return await call_next(request)
        row = {
            "key": request.headers.get("Idempotency-Key"),
            "received_at": datetime.now(UTC).isoformat(),
            "kind": args.submission_kind,
        }
        submissions.append(row)
        if len(submissions) > 1:
            release.set()
        response = await call_next(request)
        row["status"] = response.status_code
        row["accepted_at"] = datetime.now(UTC).isoformat()
        (root / "submissions.json").write_text(
            json.dumps(submissions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if len(submissions) == 1 and response.status_code == 202:
            try:
                await asyncio.wait_for(release.wait(), timeout=90)
            except TimeoutError:
                pass
        return response

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
