"""Serve real feedback/rejection APIs in a fresh local PC fault probe.

Each write kind commits normally once, then its successful HTTP receipt is
withheld and replaced by an explicit 503. Same-key retries and receipt GETs
use the actual application. No Worker, Provider, production proxy or deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.ops.serve_document_recovery_probe import (  # noqa: E402
    configure_isolation,
    port_number,
    validate_output,
)

LABEL = "PC 隔离写回执验收 · 独立数据库 · 首次已提交回执改为503 · 非AI研究"
DOMAIN = "crypto_macro.v1"
OWNER = "owner"
IMMUTABLE_TABLES = (
    "runs", "artifacts", "report_revisions", "studies", "forecasts", "outbox",
)
COUNT_TABLES = IMMUTABLE_TABLES + (
    "workbench_feedback", "promotion_decisions", "active_pointers", "candidate_versions",
    "run_calls",
    "research_tool_call_reservations", "evolution_planning_calls",
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def snapshot(database: Path) -> dict:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        hashes = {}
        for table in IMMUTABLE_TABLES:
            rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
            canonical = sorted(json.dumps(row, sort_keys=True) for row in rows)
            hashes[table] = sha(json.dumps(canonical).encode())
        return {
            "immutable_hashes": hashes,
            "counts": {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in COUNT_TABLES},
            "feedback": [dict(row) for row in connection.execute(
                "SELECT * FROM workbench_feedback",
            )],
            "decisions": [dict(row) for row in connection.execute(
                "SELECT * FROM promotion_decisions",
            )],
            "candidates": [dict(row) for row in connection.execute(
                "SELECT * FROM candidate_versions",
            )],
        }
    finally:
        connection.close()


def seed(app, root: Path, port: int) -> dict:
    from packages.contracts_py.decision_hub_contracts import CandidateVersion, ObservationCreate
    from packages.contracts_py.decision_hub_contracts.models import GateStatus
    from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
    from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.kernel.decision_hub_kernel.decision.gate import GateResult
    from packages.kernel.decision_hub_kernel.persistence.db import ReportRevisionRecord

    database = app.state.database
    event_id, _, _ = AdmissionService(database).admit(ObservationCreate(
        text="PC 隔离回执验收报告：仅测试版本反馈，不含市场判断，不是真实AI研究。",
        source_id="pc-write-receipts-probe", source_type="manual", language="zh",
    ))
    # The real commit service creates an explicitly synthetic control revision;
    # no executor/model was used and this Run remains confined to the probe.
    # Delivery lists only research.v1 revisions. This is an explicitly labelled
    # control commit on that contract, not a successful Provider execution.
    run_id, _ = RunService(database).create(
        event_id, "pc-write-receipts-control", strategy_version="research.v1",
    )
    artifact_id = CommitDecisionService(database).commit(
        run_id, event_id,
        {"headline": "PC 隔离回执验收 · 非AI研究", "summary": "只验证反馈回执，不提供市场结论。"},
        GateResult(GateStatus.research_only, []),
    )
    with database.session() as session:
        revision = session.query(ReportRevisionRecord).filter_by(source_run_id=run_id).one()
        revision_id = revision.revision_id
    candidate = CandidateVersion(
        execution_scope="directional_synthesis", candidate_id="candidate_pc_receipt_reject",
        candidate_type="strategy", content_hash=sha(b"PC receipt-only non-executable candidate"),
        version="pc-receipt-reject.v1", parent_version=None, status="candidate",
        created_at=datetime.now(UTC), content_ref=None, source="pc-receipt-probe",
    )
    app.state.evolution.register_candidate(candidate)
    result = {
        "isolation_label": LABEL, "production_data": False, "proxy": False,
        "paid_provider": False, "worker_started": False,
        "database": str(database.sqlite_path), "base_url": f"http://127.0.0.1:{port}",
        "owner": OWNER, "domain_pack_ref": DOMAIN,
        "report": {"run_id": run_id, "artifact_id": artifact_id, "revision_id": revision_id,
                   "synthetic_control_only": True,
                   "url": f"http://127.0.0.1:{port}/#/delivery?run={run_id}"},
        "candidate": candidate.model_dump(mode="json"),
        "evolution_url": f"http://127.0.0.1:{port}/#/Evolution",
        "baseline": snapshot(database.sqlite_path),
    }
    (root / "seed.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return result


def build_app(root: Path, frontend: Path, port: int):
    """Called only with a new isolated root; does not bind/listen on any port."""
    root = validate_output(root)
    root.mkdir(parents=True, exist_ok=False)
    configure_isolation(root)

    from fastapi.responses import HTMLResponse, JSONResponse, Response

    from apps.hub_api.main import create_app

    app = create_app(source_connectors=[], sources_enabled=False, frontend_dir=frontend)
    seeded = seed(app, root, port)
    journal: list[dict] = []
    withheld = set()
    receipt_get = re.compile(r"^/v1/evolution/crypto_macro\.v1/decisions/[^/]+$")
    feedback_path = "/v1/workbench/feedback"
    decision_path = f"/v1/evolution/{DOMAIN}/decisions"
    banner = (
        '<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;'
        'background:#ffecb3;color:#332500;padding:8px;text-align:center">'
        f"{LABEL}</div>"
    )

    def save_journal() -> None:
        (root / "requests.json").write_text(json.dumps(journal, ensure_ascii=False, indent=2),
                                          encoding="utf-8")

    save_journal()

    def rejected_request(request, detail: str, status: int):
        journal.append({
            "sequence": len(journal) + 1, "method": request.method,
            "path": request.url.path, "kind": "guard_rejection",
            "received_at": datetime.now(UTC).isoformat(), "application_status": None,
            "delivered_status": status, "detail": detail,
            "committed_response_withheld": False,
        })
        save_journal()
        return JSONResponse({"detail": detail}, status_code=status)

    @app.middleware("http")
    async def isolate(request, call_next):
        path = request.url.path
        if request.method == "GET" and path in {"/", "/index.html"}:
            html = (frontend / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(html.replace("</body>", banner + "</body>"))
        is_write = request.method == "POST" and path in {feedback_path, decision_path}
        is_receipt_get = request.method == "GET" and bool(receipt_get.fullmatch(path))
        if request.method not in {"GET", "HEAD"} and not is_write:
            return rejected_request(request, "pc_receipt_probe_write_not_allowed", 405)
        if not is_write and not is_receipt_get:
            return await call_next(request)
        try:
            payload = await request.json() if is_write else None
        except (ValueError, UnicodeError):
            return rejected_request(request, "pc_receipt_probe_json_required", 400)
        kind = "feedback" if path == feedback_path else "decision"
        if is_write:
            allowed = isinstance(payload, dict) and (
                payload.get("target_type") == "report_revision"
                and payload.get("target_id") == seeded["report"]["revision_id"]
                if kind == "feedback"
                else payload.get("candidate_id") == seeded["candidate"]["candidate_id"]
                and payload.get("decision") == "reject"
                and payload.get("evaluation_refs") == []
                and payload.get("expected_generation") == 0
            )
            if not allowed:
                return rejected_request(request, "pc_receipt_probe_target_not_allowed", 405)
        entry = {
            "sequence": len(journal) + 1, "method": request.method, "path": path, "kind": kind,
            "received_at": datetime.now(UTC).isoformat(),
            "request_id": payload.get("request_id") if is_write else path.rsplit("/", 1)[1],
            "payload_sha256": (
                sha(json.dumps(payload, sort_keys=True).encode()) if payload else None
            ),
            "idempotency_header": request.headers.get("Idempotency-Key"),
            "owner_header": request.headers.get("X-Owner-Id"),
        }
        journal.append(entry)
        save_journal()
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        entry.update({"application_status": response.status_code,
                      "application_returned_at": datetime.now(UTC).isoformat(),
                      "receipt": json.loads(body), "receipt_sha256": sha(body)})
        should_withhold = is_write and response.status_code == 200 and kind not in withheld
        if should_withhold:
            withheld.add(kind)
        entry["committed_response_withheld"] = should_withhold
        entry["expected_fault"] = should_withhold
        entry["delivered_status"] = 503 if should_withhold else response.status_code
        save_journal()
        if should_withhold:
            return JSONResponse({"detail": "pc_probe_response_withheld_after_commit"},
                                status_code=503)
        return Response(body, status_code=response.status_code, headers=dict(response.headers))

    return app, seeded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, default=REPO / "apps/decision-desk/dist")
    parser.add_argument("--port", type=port_number, default=8072)
    args = parser.parse_args()
    try:
        root = validate_output(args.output)
    except ValueError as error:
        parser.error(str(error))
    frontend = args.frontend.resolve()
    if not (frontend / "index.html").is_file() or not (frontend / "assets").is_dir():
        parser.error("frontend must contain a built index.html and assets directory")
    app, seeded = build_app(root, frontend, args.port)
    print(json.dumps({"isolation_label": LABEL, "seed": str(root / "seed.json"),
                      "feedback_url": seeded["report"]["url"],
                      "evolution_url": seeded["evolution_url"]}, ensure_ascii=False), flush=True)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
