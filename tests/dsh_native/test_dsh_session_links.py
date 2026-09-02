from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database

NOW = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
REQUEST_HASH = "a" * 64
RESULT_HASH = "d" * 64


def identity() -> DshUpstreamIdentity:
    return DshUpstreamIdentity(
        source_commit="c" * 40,
        source_version="0.1.2-alpha.2",
        package_versions={"@deepseek-ai/dsh": "0.1.2-alpha.2"},
        plugin_build_hash="b" * 64,
    )


def database(path: Path | None = None) -> Database:
    url = "sqlite+pysqlite:///:memory:" if path is None else f"sqlite+pysqlite:///{path}"
    db = Database(url)
    db.create_all()
    return db


def submit_for(run_id: str, *, generation: int = 1) -> DshSessionSubmit:
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        run_id, REQUEST_HASH, generation
    )
    return DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash=REQUEST_HASH,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/{generation}",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=NOW + timedelta(minutes=3),
        model_step_timeout_ms=60_000,
        max_tool_calls=12,
        generation=generation,
    )


def reserved(db: Database) -> tuple[DshSessionLinkService, str, DshSessionSubmit]:
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-dsh-native")
    service = DshSessionLinkService(db, clock=lambda: NOW)
    submit = submit_for(run_id)
    service.reserve(submit, identity())
    return service, run_id, submit


def completed(
    run_id: str, session_id: str, *, generation: int = 1, last_seq: int = 12
) -> DshSessionCompletion:
    return DshSessionCompletion(
        schema_version="dsh-session-completion.v1",
        run_id=run_id,
        dsh_session_id=session_id,
        terminal_status="completed",
        generation=generation,
        last_seq=last_seq,
        trace_ref=f"dsh://sessions/{session_id}/trace",
        result_ref=f"hub://runs/{run_id}/result",
        result_hash=RESULT_HASH,
        completed_at=NOW + timedelta(seconds=20),
        error=None,
    )


def test_contract_rejects_unknown_fields() -> None:
    payload = submit_for("run-contract").model_dump(mode="json")
    payload["unexpected"] = True

    with pytest.raises(ValueError):
        DshSessionSubmit.model_validate(payload)


def test_reserve_is_deterministic_idempotent_and_conflict_safe() -> None:
    db = database()
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-dsh-native")
    service = DshSessionLinkService(db, clock=lambda: NOW)
    submit = submit_for(run_id)

    first = service.reserve(submit, identity())
    second = service.reserve(submit, identity())

    assert first == second
    assert first.dsh_session_id.startswith("dsh_")
    assert first.state == "admitted"
    with pytest.raises(ValueError, match="dsh_session_id_not_deterministic"):
        service.reserve(
            submit.model_copy(update={"deterministic_session_id": "dsh_wrong"}), identity()
        )
    with pytest.raises(ValueError, match="dsh_deadline_conflict"):
        service.reserve(
            submit.model_copy(
                update={"deadline_at": submit.deadline_at + timedelta(seconds=1)}
            ),
            identity(),
        )
    with pytest.raises(ValueError, match="dsh_generation_not_ready"):
        service.reserve(submit_for(run_id, generation=2), identity())


def test_terminal_before_accepted_is_reconciled_without_regression() -> None:
    service, run_id, submit = reserved(database())
    completion = completed(run_id, submit.deterministic_session_id)

    terminal = service.complete(completion)
    duplicate = service.complete(completion)
    late_accepted = service.accepted(
        DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=run_id,
            dsh_session_id=submit.deterministic_session_id,
            accepted_at=NOW + timedelta(seconds=2),
            generation=1,
        )
    )

    assert terminal == duplicate
    assert late_accepted.state == "completed"
    assert late_accepted.accepted_at == NOW + timedelta(seconds=2)
    assert late_accepted.last_seq == 12


def test_completed_generation_advances_one_turn_in_the_same_session() -> None:
    service, run_id, first = reserved(database())
    service.store_prompt(
        DshSessionPrompt(
            schema_version="dsh-session-prompt.v1",
            run_id=run_id,
            dsh_session_id=first.deterministic_session_id,
            request_id=first.deterministic_request_id,
            request_hash=first.request_hash,
            generation=1,
            prompt="first evidence round",
        )
    )
    service.complete(completed(run_id, first.deterministic_session_id))

    second = submit_for(run_id, generation=2)
    advanced = service.reserve(second, identity())
    service.store_prompt(
        DshSessionPrompt(
            schema_version="dsh-session-prompt.v1",
            run_id=run_id,
            dsh_session_id=second.deterministic_session_id,
            request_id=second.deterministic_request_id,
            request_hash=second.request_hash,
            generation=2,
            prompt="second evidence round with recomputed gaps",
        )
    )

    assert advanced.generation == 2
    assert advanced.state == "admitted"
    assert first.deterministic_session_id == second.deterministic_session_id
    assert first.deterministic_request_id != second.deterministic_request_id
    assert service.get_prompt(run_id, 1).prompt == "first evidence round"  # type: ignore[union-attr]
    assert service.get_prompt(run_id, 2).prompt.startswith("second evidence")  # type: ignore[union-attr]

    stale = service.complete(completed(run_id, second.deterministic_session_id))
    assert stale.generation == 2
    assert stale.state == "admitted"

    service.complete(
        completed(
            run_id,
            second.deterministic_session_id,
            generation=2,
            last_seq=24,
        )
    )
    with pytest.raises(ValueError, match="dsh_generation_conflict"):
        service.reserve(submit_for(run_id, generation=4), identity())


def test_stale_status_is_ignored_and_terminal_is_immutable() -> None:
    service, run_id, submit = reserved(database())
    running = service.observe(
        DshSessionStatus(
            schema_version="dsh-session-status.v1",
            run_id=run_id,
            dsh_session_id=submit.deterministic_session_id,
            state="running",
            generation=1,
            last_seq=8,
            observed_at=NOW + timedelta(seconds=8),
            error_code=None,
        )
    )
    stale = service.observe(
        DshSessionStatus(
            schema_version="dsh-session-status.v1",
            run_id=run_id,
            dsh_session_id=submit.deterministic_session_id,
            state="idle",
            generation=1,
            last_seq=7,
            observed_at=NOW + timedelta(seconds=9),
            error_code=None,
        )
    )
    service.complete(completed(run_id, submit.deterministic_session_id))

    assert running.state == "running"
    assert stale.state == "running"
    with pytest.raises(ValueError, match="dsh_terminal_conflict"):
        service.complete(
            completed(run_id, submit.deterministic_session_id).model_copy(
                update={"terminal_status": "failed", "error": DshBridgeError(
                    code="provider_failed", message="provider failed", retryable=False
                )}
            )
        )


def test_callback_api_requires_secret_and_preserves_idempotency(tmp_path: Path) -> None:
    db = database(tmp_path / "callback.sqlite3")
    app = create_app(db, source_connectors=[], dsh_callback_secret="bridge-secret")
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-api")
    submit = submit_for(run_id)
    app.state.dsh_sessions.reserve(submit, identity())
    accepted_payload = DshSessionAccepted(
        schema_version="dsh-session-accepted.v1",
        run_id=run_id,
        dsh_session_id=submit.deterministic_session_id,
        accepted_at=NOW + timedelta(seconds=2),
        generation=1,
    ).model_dump(mode="json")

    with TestClient(app) as client:
        unauthorized = client.put(f"/v1/dsh/sessions/{run_id}/accepted", json=accepted_payload)
        accepted = client.put(
            f"/v1/dsh/sessions/{run_id}/accepted",
            json=accepted_payload,
            headers={"X-Decision-Hub-Bridge-Key": "bridge-secret"},
        )
        duplicate = client.put(
            f"/v1/dsh/sessions/{run_id}/accepted",
            json=accepted_payload,
            headers={"X-Decision-Hub-Bridge-Key": "bridge-secret"},
        )
        queried = client.get(f"/v1/dsh/sessions/{run_id}")

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert duplicate.json() == accepted.json()
    assert queried.status_code == 200
    assert queried.json()["dsh_session_id"] == submit.deterministic_session_id


def test_callback_api_fails_closed_when_secret_is_unconfigured(tmp_path: Path) -> None:
    db = database(tmp_path / "callback-unconfigured.sqlite3")
    app = create_app(db, source_connectors=[], dsh_callback_secret="")
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-api-unconfigured")
    submit = submit_for(run_id)
    app.state.dsh_sessions.reserve(submit, identity())

    with TestClient(app) as client:
        response = client.put(
            f"/v1/dsh/sessions/{run_id}/accepted",
            json={
                "schema_version": "dsh-session-accepted.v1",
                "run_id": run_id,
                "dsh_session_id": submit.deterministic_session_id,
                "accepted_at": NOW.isoformat(),
                "generation": 1,
            },
            headers={"X-Decision-Hub-Bridge-Key": "any"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "dsh_callback_not_configured"


def test_callback_api_resolves_link_by_dsh_session_id(tmp_path: Path) -> None:
    db = database(tmp_path / "callback-by-session.sqlite3")
    app = create_app(db, source_connectors=[], dsh_callback_secret="bridge-secret")
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-api-by-session")
    submit = submit_for(run_id)
    app.state.dsh_sessions.reserve(submit, identity())

    with TestClient(app) as client:
        response = client.get(
            f"/v1/dsh/sessions/by-session/{submit.deterministic_session_id}"
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id


def test_status_callback_projects_non_terminal_progress(tmp_path: Path) -> None:
    db = database(tmp_path / "callback-status.sqlite3")
    app = create_app(db, source_connectors=[], dsh_callback_secret="bridge-secret")
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-api-status")
    submit = submit_for(run_id)
    app.state.dsh_sessions.reserve(submit, identity())

    with TestClient(app) as client:
        response = client.put(
            f"/v1/dsh/sessions/{run_id}/status",
            json={
                "schema_version": "dsh-session-status.v1",
                "run_id": run_id,
                "dsh_session_id": submit.deterministic_session_id,
                "state": "running",
                "generation": 1,
                "last_seq": 7,
                "observed_at": (NOW + timedelta(seconds=7)).isoformat(),
                "error_code": None,
            },
            headers={"X-Decision-Hub-Bridge-Key": "bridge-secret"},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "running"
    assert response.json()["last_seq"] == 7
