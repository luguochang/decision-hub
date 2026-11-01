"""Same authenticated route: lifecycle proof does not read report originals."""

import ast
import hmac
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event

from packages.contracts_py.decision_hub_contracts import DshBusinessStatus
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.query_views.research import ResearchQueryService
from tests.evolution.test_research_worker import PACK_ROOT, _admitted_run


def test_activity_lifecycle_is_authenticated_current_and_never_report_projection(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "private-data"))
    db, _event_id, run_id = _admitted_run(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("lifecycle must not authenticate coverage/report originals")

    monkeypatch.setattr(ResearchQueryService, "_coverage_basis_for_run", forbidden)
    # Execute the actual route and its actual auth function, without module-level
    # normal-launch app creation (which requires a production publication registry).
    path = Path(os.environ.get("ACTIVITY_PROJECTION_CANDIDATE_MAIN", "apps/hub_api/main.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )
    names = {"require_dsh_callback_secret", "dsh_business_status"}
    nodes = [
        node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(nodes) == 2
    app = FastAPI()
    namespace = {
        "app": app,
        "Header": Header,
        "HTTPException": HTTPException,
        "Literal": Literal,
        "DshBusinessStatus": DshBusinessStatus,
        "hmac": hmac,
        "effective_dsh_callback_secret": "private-test-bridge",
        "research_query": ResearchQueryService(db, pack_root=PACK_ROOT),
        "frozen_review_bootstrap": None,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    url = f"/v1/dsh/sessions/{run_id}/business-status?projection=lifecycle"
    reads = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement.lower())

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(url).status_code == 401
        for status, expected in [("running", "researching"), ("failed", "failed")]:
            with db.session() as session:
                run = session.get(RunRecord, run_id)
                run.status = status
                run.error_code = "controlled_failure" if status == "failed" else None
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                reply = client.get(
                    url, headers={"X-Decision-Hub-Bridge-Key": "private-test-bridge"}
                )
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)
            assert reply.status_code == 200
            value = reply.json()
            assert value["status"] == expected
            assert value["coverage_status"] is None and value["gate_status"] is None
            assert value["hard_coverage_ratio"] is None and value["failures"] == []
        assert reads and all("from runs" in sql for sql in reads)
        # The default retains full report semantics; it deliberately reaches the trap.
        assert client.get(url.split("?")[0]).status_code == 500
        assert (
            client.get(
                url.replace(run_id, "run-missing"),
                headers={"X-Decision-Hub-Bridge-Key": "private-test-bridge"},
            ).status_code
            == 404
        )
        with db.session() as session:
            session.get(RunRecord, run_id).strategy_version = "baseline.v1"
        assert (
            client.get(
                url, headers={"X-Decision-Hub-Bridge-Key": "private-test-bridge"}
            ).status_code
            == 404
        )
