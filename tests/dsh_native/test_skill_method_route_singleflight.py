from __future__ import annotations

import threading
import time

import pytest

from apps.hub_api.skill_method_routes import (
    MethodProjectionPending,
    MethodReadCoordinator,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.skill_method_view import SkillMethodQuery
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ResearchSkillUseEventRecord,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    utcnow,
)


class _Service:
    def __init__(self) -> None:
        self.calls = 0
        self.release = threading.Event()

    def read(self, run_id: str, revision_id: str | None = None):
        self.calls += 1
        assert self.release.wait(2)
        return (run_id, revision_id, self.calls)


class _FailsOnceService(_Service):
    def read(self, run_id: str, revision_id: str | None = None):
        self.calls += 1
        if self.calls == 1:
            raise OSError("controlled transient method read failure")
        return (run_id, revision_id, self.calls)


def _wait_done(
    coordinator: MethodReadCoordinator,
    service: _Service,
    frontier: str,
):
    for _ in range(200):
        try:
            return coordinator.read(service, "run-one", None, frontier)  # type: ignore[arg-type]
        except MethodProjectionPending:
            time.sleep(0.005)
    raise AssertionError("shared method projection did not settle")


def test_same_frontier_uses_one_physical_method_read() -> None:
    coordinator = MethodReadCoordinator()
    service = _Service()

    with pytest.raises(MethodProjectionPending):
        coordinator.read(service, "run-one", None, "frontier-a")  # type: ignore[arg-type]
    with pytest.raises(MethodProjectionPending):
        coordinator.read(service, "run-one", None, "frontier-a")  # type: ignore[arg-type]
    assert service.calls == 1

    service.release.set()
    assert _wait_done(coordinator, service, "frontier-a") == (
        "run-one",
        None,
        1,
    )
    assert service.calls == 1


def test_changed_frontier_starts_one_new_read_after_original_finishes() -> None:
    coordinator = MethodReadCoordinator()
    service = _Service()
    service.release.set()

    with pytest.raises(MethodProjectionPending):
        coordinator.read(service, "run-one", None, "frontier-a")  # type: ignore[arg-type]
    assert _wait_done(coordinator, service, "frontier-a")[-1] == 1

    with pytest.raises(MethodProjectionPending):
        coordinator.read(service, "run-one", None, "frontier-b")  # type: ignore[arg-type]
    assert _wait_done(coordinator, service, "frontier-b")[-1] == 2
    assert service.calls == 2


def test_failed_future_is_observed_once_then_same_frontier_can_retry() -> None:
    coordinator = MethodReadCoordinator()
    service = _FailsOnceService()

    with pytest.raises(MethodProjectionPending):
        coordinator.read(service, "run-one", None, "frontier-a")  # type: ignore[arg-type]
    for _ in range(200):
        try:
            coordinator.read(service, "run-one", None, "frontier-a")  # type: ignore[arg-type]
        except MethodProjectionPending:
            time.sleep(0.005)
            continue
        except OSError as error:
            assert str(error) == "controlled transient method read failure"
            break
    else:
        raise AssertionError("failed method projection was never observed")

    with pytest.raises(MethodProjectionPending):
        coordinator.read(service, "run-one", None, "frontier-a")  # type: ignore[arg-type]
    assert _wait_done(coordinator, service, "frontier-a") == (
        "run-one",
        None,
        2,
    )
    assert service.calls == 2


def test_frontier_is_absent_before_selection_and_changes_with_stage(tmp_path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'frontier.sqlite3'}")
    database.create_all()
    run_id, _ = RunService(database).create("method-frontier")
    query = SkillMethodQuery(database, object())  # type: ignore[arg-type]

    assert query.frontier(run_id) is None
    now = utcnow()
    with database.session() as session:
        session.add(
            RunRoleOutputRecord(
                output_id="selection-output",
                run_id=run_id,
                role="research_skill_selection",
                payload_json="{}",
                payload_hash="1" * 64,
                schema_version="research-skill-selection.v1",
                created_at=now,
            )
        )
    selected = query.frontier(run_id)
    assert selected is not None

    with database.session() as session:
        session.add(
            ResearchSkillUseEventRecord(
                event_id="stage-selected",
                use_id="use-one",
                run_id=run_id,
                generation=1,
                root_session_id="dsh-one",
                execution_session_id="dsh-one",
                skill_version_id="skill-one",
                package_hash="2" * 64,
                workflow_hash="3" * 64,
                method_binding_hash="4" * 64,
                stage="selected",
                node_id="",
                stage_request_id="request-one",
                parent_event_ref=None,
                original_ref="original://selected",
                original_hash="5" * 64,
                first_known_at=now,
                payload_json="{}",
                payload_hash="6" * 64,
            )
        )
    assert query.frontier(run_id) != selected
