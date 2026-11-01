from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import (
    DshSessionAccepted,
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database

NOW = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)


def test_owned_run_failure_atomically_closes_nonterminal_dsh_link(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'terminal-convergence.sqlite3'}")
    database.create_all()
    runs = RunService(database, clock=lambda: NOW)
    run_id, _ = runs.create("event-owned-dsh-failure")
    assert runs.claim_for_execution(
        run_id, worker_id="research-worker", lease_seconds=60
    )
    links = DshSessionLinkService(database, clock=lambda: NOW)
    request_hash = "a" * 64
    session_id, request_id = links.deterministic_ids(run_id, request_hash, 1)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash=request_hash,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=NOW + timedelta(minutes=3),
        model_step_timeout_ms=60_000,
        max_tool_calls=12,
        generation=1,
    )
    links.reserve(
        submit,
        DshUpstreamIdentity(
            source_commit="c" * 40,
            source_version="0.1.2-alpha.2",
            package_versions={"@deepseek-ai/dsh": "0.1.2-alpha.2"},
            plugin_build_hash="b" * 64,
        ),
    )
    links.accepted(
        DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=run_id,
            dsh_session_id=session_id,
            accepted_at=NOW,
            generation=1,
        )
    )

    assert runs.fail_if_owned(
        run_id,
        "research-worker",
        error_code="dsh_native_session_lost",
        require_current_lease=True,
    )

    run = database.get_run_record(run_id)
    link = links.get(run_id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "dsh_native_session_lost"
    assert run.finished_at.replace(tzinfo=UTC) == NOW
    assert link is not None and link.state == "failed"
    assert link.error_code == "dsh_native_session_lost"
    assert link.terminal_at is not None and link.terminal_at.replace(tzinfo=UTC) == NOW
    assert link.last_seen_at is not None and link.last_seen_at.replace(tzinfo=UTC) == NOW
