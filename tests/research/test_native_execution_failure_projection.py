"""Keep real current-Link failures separate from immutable saved research."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ResearchRunDetailView
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    ResearchResultRecord,
    RunRecord,
)
from packages.query_views.research import ResearchQueryService
from packages.query_views.research.service import (
    _native_execution_failure,  # pyright: ignore[reportPrivateUsage]
)
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
    _result,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.query_basis_helpers import save_control_request


def test_current_native_failure_is_read_only_and_clears_after_link_repair(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    save_control_request(database, request, NOW)
    result = _result(request).model_copy(update={
        "status": "degraded", "causal_case": None, "horizons": [],
        "stop_reason": _result(request).stop_reason.model_copy(update={
            "code": "synthesis_incomplete", "detail": "No causal case was produced.",
        }),
    })
    terminal_at = NOW + timedelta(seconds=3)
    with database.session() as session:
        ResearchObservabilityService(database, clock=lambda: NOW).save_result_in_session(
            session, run_id, result
        )
        run = session.get(RunRecord, run_id)
        link = session.get(DshSessionLinkRecord, run_id)
        assert run is not None and link is not None
        run.status = "degraded"
        link.state = "failed"
        link.error_code = "dsh_model_binding_unavailable"
        link.terminal_at = terminal_at
        expected_session = link.dsh_session_id
    with database.session() as session:
        saved = session.get(ResearchResultRecord, run_id)
        assert saved is not None
        original = (saved.payload_json, saved.payload_hash)

    def forbid_writes(
        _conn: object, _cursor: object, statement: str, _parameters: object,
        _context: object, _executemany: bool,
    ) -> None:
        assert not statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))

    event.listen(database.engine, "before_cursor_execute", forbid_writes)
    try:
        detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
        assert detail is not None
        failure = detail.native_execution_failure
        assert failure is not None
        assert failure.model_dump(mode="json") == {
            "schema_version": "research-native-execution-failure.v1",
            "run_id": run_id, "dsh_session_id": expected_session, "generation": 1,
            "error_code": "dsh_model_binding_unavailable",
            "terminal_at": terminal_at.isoformat().replace("+00:00", "Z"),
        }
        assert detail.run.stop_reason is not None
        assert detail.run.stop_reason.code == "synthesis_incomplete"
        assert detail.causal_case is None and detail.horizons == []
        assert detail.rounds[0].plan == result.rounds[0].plan
        assert not {"prompt", "request_hash", "payload", "retryable", "cause_code"} & (
            failure.model_dump().keys()
        )
        legacy = detail.model_dump(mode="json")
        legacy.pop("native_execution_failure")
        assert ResearchRunDetailView.model_validate(legacy).native_execution_failure is None
    finally:
        event.remove(database.engine, "before_cursor_execute", forbid_writes)

    with database.session() as session:
        saved = session.get(ResearchResultRecord, run_id)
        link = session.get(DshSessionLinkRecord, run_id)
        assert saved is not None and link is not None
        assert (saved.payload_json, saved.payload_hash) == original
        # A fresh current generation no longer fails. Never keep an old banner.
        link.generation = 2
        link.state = "running"
        link.error_code = None
        link.terminal_at = None
    repaired = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    assert repaired is not None and repaired.native_execution_failure is None
    assert repaired.run.stop_reason == detail.run.stop_reason
    with database.session() as session:
        saved = session.get(ResearchResultRecord, run_id)
        assert saved is not None and (saved.payload_json, saved.payload_hash) == original


@pytest.mark.parametrize("state,error_code,has_terminal,same_run", [
    ("running", "dsh_model_binding_unavailable", True, True),
    ("completed", "dsh_model_binding_unavailable", True, True),
    ("cancelled", "dsh_model_binding_unavailable", True, True),
    ("failed", None, True, True),
    ("failed", "dsh_model_binding_unavailable", False, True),
    ("failed", "dsh_model_binding_unavailable", True, False),
])
def test_native_failure_requires_current_same_run_terminal_failure(
    tmp_path: Path, state: str, error_code: str | None, has_terminal: bool, same_run: bool
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    save_control_request(database, request, NOW)
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        assert link is not None
        link.state, link.error_code = state, error_code
        link.terminal_at = NOW if has_terminal else None
        assert _native_execution_failure(run_id if same_run else "foreign-run", link) is None
    if same_run:
        detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
        assert detail is not None and detail.native_execution_failure is None


def test_old_completed_result_and_absent_link_do_not_claim_native_failure(tmp_path: Path):
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    result = _result(request)
    with database.session() as session:
        ResearchObservabilityService(database, clock=lambda: NOW).save_result_in_session(
            session, run_id, result
        )
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "completed"
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    assert detail is not None and detail.native_execution_failure is None
    assert detail.causal_case == result.causal_case and detail.horizons == result.horizons
