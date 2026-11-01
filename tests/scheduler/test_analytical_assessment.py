"""Durable assessment scheduling with controlled typed builder outputs, offline.

These fixtures certify scheduling, claims and serialization only. They supply
no authenticated research or market label. The real original-input/Graph and
archive authenticity gates remain in research assessment/basis tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from packages.evals.analytical_assessment import AnalyticalAssessment
from packages.evals.assessment_basis import LOADED_VERIFIER_VERSION, SCHEMA
from packages.kernel.decision_hub_kernel.application import analytical_assessment as service_module
from packages.kernel.decision_hub_kernel.application.analytical_assessment import (
    AnalyticalAssessmentService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalAssessmentJobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.db import AnalyticalOutlookRecord as Outlook
from packages.kernel.decision_hub_kernel.persistence.db import Database, as_utc
from packages.provider_adapters.research.raw_capture import RawBodyArchive

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
MANIFEST = "d" * 64
NEW_MANIFEST = "e" * 64
type _LabelStatus = Literal["verified", "not_due", "pending", "retry", "unverifiable"]


@pytest.fixture(autouse=True)
def controlled_loaded_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheduler transport seam only; never a loaded-source certificate."""
    monkeypatch.setattr(service_module, "loaded_assessment_manifest_hash", lambda: MANIFEST)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def database(tmp_path: Path) -> Generator[Database]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assessment-jobs.sqlite3'}")
    database.create_all()
    try:
        yield database
    finally:
        database.engine.dispose()


def _add_outlook(database: Database, identity: str, **updates: object) -> None:
    values: dict[str, Any] = {
        "outlook_id": identity, "run_id": f"run-{identity}", "artifact_id": f"art-{identity}",
        "study_id": "study-one", "market": "crypto", "instrument": "BTC-USDT-SWAP",
        "method_family": "ai_synthesis", "method_version": "controlled-scheduler.v1",
        "horizon": "30m", "action": "neutral", "subjective_probability": None,
        "probability_status": "uncalibrated", "data_as_of": NOW - timedelta(seconds=1),
        "window_start_at": NOW, "window_end_at": NOW + timedelta(minutes=30),
        "recorded_at": NOW, "result_hash": "0" * 64,
        "evidence_refs_json": "[]", "fact_version_refs_json": "[]",
        "raw_payload_json": json.dumps({"controlled_scheduler_original": identity}),
        "trigger": "controlled trigger", "invalidation": "controlled invalidation",
        "cohort": "prospective", "eligibility": "excluded",
        "exclusion_reasons_json": '["accepted_versioned_inputs_missing"]',
        "research_quality": "insufficient",
    }
    values.update(updates)
    with database.session() as session:
        session.add(Outlook(**values))


def _rows(database: Database, model: type[Job] | type[Outlook]) -> dict[str, dict[str, Any]]:
    with database.session() as session:
        return {
            row.outlook_id: {
                column.name: as_utc(value) if isinstance(value, datetime) else value
                for column in row.__table__.columns
                for value in (getattr(row, column.name),)
            }
            for row in session.query(model).all()
        }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _projection(
    identity: str, now: datetime, *, label: _LabelStatus = "verified", manifest: str = MANIFEST,
) -> tuple[AnalyticalAssessment, dict[str, Any]]:
    assessment = AnalyticalAssessment(
        assessed_at=now, outlook_id=identity, run_id=f"run-{identity}", study_id="study-one",
        source_status="unverifiable", method_status="unverifiable", stable_recipe_hash=None,
        label_status=label,
    )
    # Only the scheduler's used transport fields are controlled here. The
    # canonical hash really covers this typed assessment, not an invented hash.
    basis = {
        "schema_version": SCHEMA, "outlook_id": identity,
        "loaded_verifier": {"schema_version": LOADED_VERIFIER_VERSION, "manifest_hash": manifest},
        "manifest_hash": manifest, "assessment_hash": _digest(assessment.model_dump(mode="json")),
    }
    return assessment, basis


def _builder(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock,
    *, callback: Callable[[str], _LabelStatus] | None = None,
) -> list[str]:
    calls: list[str] = []

    def build(db: Database, identity: str, *, assessed_at: datetime,
        archive: RawBodyArchive | None = None,
    ):
        assert assessed_at == clock.now
        calls.append(identity)
        label = callback(identity) if callback is not None else "verified"
        return _projection(identity, assessed_at, label=label)

    monkeypatch.setattr(service_module, "loaded_assessment_manifest_hash", lambda: MANIFEST)
    monkeypatch.setattr(service_module, "build_assessment_basis", build)
    return calls


def test_discovery_is_bounded_idempotent_and_excludes_retrospective_and_future(database: Database):
    for identity, changes in (
        ("ai-a", {"recorded_at": NOW - timedelta(seconds=3)}),
        ("ai-b", {"recorded_at": NOW - timedelta(seconds=2)}),
        ("ai-c", {"recorded_at": NOW - timedelta(seconds=1)}),
        ("daily", {"recorded_at": NOW - timedelta(days=10), "method_family": "daily_rule"}),
        ("historical", {"cohort": "retrospective", "recorded_at": NOW - timedelta(days=30)}),
        ("future", {"recorded_at": NOW + timedelta(seconds=1)}),
    ):
        _add_outlook(database, identity, **changes)
    originals = _rows(database, Outlook)
    service = AnalyticalAssessmentService(database, clock=lambda: NOW)
    assert service.discover(NOW, MANIFEST, limit=2) == 2
    assert set(_rows(database, Job)) == {"ai-a", "ai-b"}
    restarted = AnalyticalAssessmentService(database, clock=lambda: NOW)
    assert restarted.discover(NOW, MANIFEST, limit=2) == 2
    assert set(_rows(database, Job)) == {"ai-a", "ai-b", "ai-c", "daily"}
    assert restarted.discover(NOW, MANIFEST, limit=2) == 0
    assert _rows(database, Outlook) == originals


async def test_claim_and_completed_projection_survive_service_restart(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "one")
    clock = _Clock()
    calls = _builder(monkeypatch, clock)
    original = _rows(database, Outlook)
    service = AnalyticalAssessmentService(database, clock=clock)
    assert service.discover(clock.now, MANIFEST) == 1
    claim = service.claim(clock.now, MANIFEST)
    assert claim is not None and claim[0] == "one" and claim[2] == 1
    restarted = AnalyticalAssessmentService(database, clock=clock)
    assert restarted.claim(clock.now, MANIFEST) is None
    assessment, basis = _projection("one", clock.now)
    assert restarted.finish(*claim, assessment=assessment, basis=basis)
    completed = _rows(database, Job)["one"]
    assert completed["status"] == "completed" and completed["attempt"] == 1
    assert completed["lease_owner"] is None and completed["lease_expires_at"] is None
    assert completed["assessment_hash"] == _digest(json.loads(completed["assessment_json"]))
    assert completed["basis_hash"] == _digest(json.loads(completed["basis_json"]))
    assert completed["next_attempt_at"] == NOW + timedelta(hours=6)
    assert await AnalyticalAssessmentService(database, clock=clock).process_due(limit=1) == 0
    assert calls == [] and _rows(database, Job)["one"] == completed
    assert _rows(database, Outlook) == original


@pytest.mark.parametrize("stale", ["owner", "attempt", "expired"])
def test_finish_cas_rejects_stale_owner_attempt_or_expired_live_lease(
    database: Database, stale: str,
):
    _add_outlook(database, "one")
    clock = _Clock()
    service = AnalyticalAssessmentService(database, clock=clock)
    service.discover(clock.now, MANIFEST)
    claim = service.claim(clock.now, MANIFEST)
    assert claim is not None
    identity, owner, attempt = claim
    if stale == "owner":
        owner += "-stale"
    elif stale == "attempt":
        attempt += 1
    else:
        clock.now += timedelta(minutes=2)
    before = _rows(database, Job)
    assessment, basis = _projection(identity, clock.now)
    assert not service.finish(identity, owner, attempt, assessment=assessment, basis=basis)
    assert _rows(database, Job) == before


def test_finish_cas_rejects_changed_manifest_even_with_valid_identity_and_result_hash(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "one")
    service = AnalyticalAssessmentService(database, clock=lambda: NOW)
    assert service.discover(NOW, MANIFEST) == 1
    claim = service.claim(NOW, MANIFEST)
    assert claim is not None
    before = _rows(database, Job)
    originals = _rows(database, Outlook)
    assessment, basis = _projection("one", NOW, manifest=NEW_MANIFEST)
    assert basis["assessment_hash"] == _digest(assessment.model_dump(mode="json"))
    assert assessment.outlook_id == claim[0] and basis["outlook_id"] == claim[0]
    assert assessment.run_id == originals["one"]["run_id"]
    assert assessment.study_id == originals["one"]["study_id"]
    assert before["one"]["status"] == "running"
    assert before["one"]["verifier_version"] == MANIFEST
    assert before["one"]["lease_owner"] == claim[1]
    assert before["one"]["assessment_json"] is None and before["one"]["basis_json"] is None
    # Hold a coherent controlled new loaded verifier, while the stored claim
    # still belongs to its old manifest. The actual claim CAS must reject it.
    monkeypatch.setattr(service_module, "loaded_assessment_manifest_hash", lambda: NEW_MANIFEST)
    assert not service.finish(*claim, assessment=assessment, basis=basis)
    assert _rows(database, Job) == before
    assert _rows(database, Outlook) == originals


def test_expired_claim_is_recovered_with_new_attempt_and_old_finish_cannot_overwrite(
    database: Database,
):
    _add_outlook(database, "one")
    clock = _Clock()
    service = AnalyticalAssessmentService(database, clock=clock)
    service.discover(clock.now, MANIFEST)
    old = service.claim(clock.now, MANIFEST)
    assert old is not None
    clock.now += timedelta(minutes=2)
    restarted = AnalyticalAssessmentService(database, clock=clock)
    assert restarted.discover(clock.now, MANIFEST) == 0
    recovered = _rows(database, Job)["one"]
    assert recovered["status"] == "retry" and recovered["error_code"] == "assessment_lease_expired"
    assert recovered["lease_owner"] is None and recovered["next_attempt_at"] == clock.now
    fresh = restarted.claim(clock.now, MANIFEST)
    assert fresh is not None and fresh[2] == 2 and fresh[1] != old[1]
    assessment, basis = _projection("one", clock.now)
    assert not service.finish(*old, assessment=assessment, basis=basis)
    assert restarted.finish(*fresh, assessment=assessment, basis=basis)
    before = _rows(database, Job)
    assert not restarted.finish(*fresh, error="late_failure")
    assert _rows(database, Job) == before


async def test_failed_earlier_job_has_backoff_and_does_not_block_later_job(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "a-failure", recorded_at=NOW - timedelta(seconds=1))
    _add_outlook(database, "b-success")
    clock = _Clock()

    def label(identity: str) -> _LabelStatus:
        if identity == "a-failure":
            raise OSError("controlled archive outage")
        return "verified"

    calls = _builder(monkeypatch, clock, callback=label)
    service = AnalyticalAssessmentService(database, clock=clock)
    assert await service.process_due(limit=2) == 1
    rows = _rows(database, Job)
    failed = rows["a-failure"]
    assert calls == ["a-failure", "b-success"]
    assert failed["status"] == "retry" and failed["error_code"] == "assessment_OSError"
    assert failed["next_attempt_at"] == NOW + timedelta(seconds=30)
    assert failed["assessment_json"] is None and failed["basis_json"] is None
    assert rows["b-success"]["status"] == "completed"
    clock.now += timedelta(seconds=29)
    assert await service.process_due(limit=2) == 0 and len(calls) == 2
    clock.now += timedelta(seconds=1)
    assert await service.process_due(limit=1) == 0
    assert _rows(database, Job)["a-failure"]["next_attempt_at"] == clock.now + timedelta(seconds=60)


async def test_not_due_refreshes_only_at_window_end_with_a_new_builder_result(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "one", window_end_at=NOW + timedelta(seconds=20))
    clock = _Clock()
    calls = _builder(
        monkeypatch, clock, callback=lambda _: "not_due" if clock.now < NOW + timedelta(seconds=20)
        else "verified",
    )
    service = AnalyticalAssessmentService(database, clock=clock)
    assert await service.process_due(limit=1) == 1
    first = _rows(database, Job)["one"]
    assert first["next_attempt_at"] == NOW + timedelta(seconds=20)
    assert json.loads(first["assessment_json"])["label_status"] == "not_due"
    clock.now += timedelta(seconds=19)
    assert await service.process_due(limit=1) == 0 and calls == ["one"]
    clock.now += timedelta(seconds=1)
    assert await service.process_due(limit=1) == 1 and calls == ["one", "one"]
    refreshed = _rows(database, Job)["one"]
    assert refreshed["attempt"] == 2 and refreshed["assessment_hash"] != first["assessment_hash"]
    assert json.loads(refreshed["assessment_json"])["label_status"] == "verified"


@pytest.mark.parametrize("label", ["pending", "retry"])
async def test_incomplete_label_is_refreshed_after_sixty_seconds_without_promoting_it(
    database: Database, monkeypatch: pytest.MonkeyPatch, label: _LabelStatus,
):
    _add_outlook(database, "one")
    clock = _Clock()
    calls = _builder(monkeypatch, clock, callback=lambda _: label)
    service = AnalyticalAssessmentService(database, clock=clock)
    assert await service.process_due(limit=1) == 1
    row = _rows(database, Job)["one"]
    assert row["next_attempt_at"] == NOW + timedelta(seconds=60)
    assert json.loads(row["assessment_json"])["label_status"] == label
    clock.now += timedelta(seconds=59)
    assert await service.process_due(limit=1) == 0 and calls == ["one"]
    clock.now += timedelta(seconds=1)
    assert await service.process_due(limit=1) == 1 and calls == ["one", "one"]
    assert json.loads(_rows(database, Job)["one"]["assessment_json"])["label_status"] == label


async def test_unverifiable_label_retains_its_verdict_without_a_sixty_second_hot_loop(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "one")
    clock = _Clock()
    calls = _builder(monkeypatch, clock, callback=lambda _: "unverifiable")
    service = AnalyticalAssessmentService(database, clock=clock)
    assert await service.process_due(limit=1) == 1
    assert _rows(database, Job)["one"]["next_attempt_at"] == NOW + timedelta(hours=6)
    clock.now += timedelta(minutes=1)
    assert await service.process_due(limit=1) == 0 and calls == ["one"]
    assert (
        json.loads(_rows(database, Job)["one"]["assessment_json"])["label_status"] == "unverifiable"
    )


@pytest.mark.parametrize("fault", ["outlook", "run", "study", "basis", "future"])
def test_finish_rejects_projection_identity_mismatch_before_persisting_or_clearing_claim(
    database: Database, fault: str,
):
    _add_outlook(database, "one")
    clock = _Clock()
    service = AnalyticalAssessmentService(database, clock=clock)
    service.discover(NOW, MANIFEST)
    claim = service.claim(NOW, MANIFEST)
    assert claim is not None
    assessment, basis = _projection("one", NOW)
    if fault == "basis":
        basis["outlook_id"] = "other"
    else:
        field, value = {
            "outlook": ("outlook_id", "other"), "run": ("run_id", "other-run"),
            "study": ("study_id", "other-study"),
            "future": ("assessed_at", NOW + timedelta(seconds=1)),
        }[fault]
        assessment = assessment.model_copy(update={field: value})
        basis["assessment_hash"] = _digest(assessment.model_dump(mode="json"))
    before = _rows(database, Job)
    with pytest.raises(ValueError, match="assessment_basis_identity_mismatch"):
        service.finish(*claim, assessment=assessment, basis=basis)
    assert _rows(database, Job) == before


async def test_manifest_change_requeues_completed_projection_and_requires_another_build(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "one")
    clock = _Clock()
    calls = _builder(monkeypatch, clock)
    service = AnalyticalAssessmentService(database, clock=clock)
    assert await service.process_due(limit=1) == 1
    old = _rows(database, Job)["one"]
    assert old["next_attempt_at"] > NOW
    assert service.discover(NOW, NEW_MANIFEST) == 0
    queued = _rows(database, Job)["one"]
    assert queued["status"] == "pending" and queued["verifier_version"] == NEW_MANIFEST
    assert queued["assessment_json"] == old["assessment_json"]
    assert calls == ["one"]
    claim = AnalyticalAssessmentService(database, clock=clock).claim(NOW, NEW_MANIFEST)
    assert claim is not None and claim[2] == 2
    assessment, basis = _projection("one", NOW, manifest=NEW_MANIFEST)
    monkeypatch.setattr(service_module, "loaded_assessment_manifest_hash", lambda: NEW_MANIFEST)
    assert service.finish(*claim, assessment=assessment, basis=basis)
    assert _rows(database, Job)["one"]["verifier_version"] == NEW_MANIFEST


@pytest.mark.parametrize("fault", ["manifest", "hash"])
async def test_changed_manifest_or_nonmatching_canonical_result_hash_never_completes(
    database: Database, monkeypatch: pytest.MonkeyPatch, fault: str,
):
    _add_outlook(database, "one")
    clock = _Clock()
    _builder(monkeypatch, clock)

    def bad_builder(db: Database, identity: str, *, assessed_at: datetime,
        archive: RawBodyArchive | None = None,
    ):
        assessment, basis = _projection(identity, assessed_at)
        basis["manifest_hash" if fault == "manifest" else "assessment_hash"] = "f" * 64
        return assessment, basis

    monkeypatch.setattr(service_module, "build_assessment_basis", bad_builder)
    assert await AnalyticalAssessmentService(database, clock=clock).process_due(limit=1) == 0
    row = _rows(database, Job)["one"]
    assert row["status"] == "retry" and row["error_code"] == "assessment_ValueError"
    assert row["assessment_json"] is None and row["basis_json"] is None


@pytest.mark.parametrize("limit", [0, 9])
async def test_processing_rejects_limits_outside_bounded_range_before_discovery(
    database: Database, limit: int,
):
    _add_outlook(database, "one")
    with pytest.raises(ValueError, match="assessment_batch_limit_invalid"):
        await AnalyticalAssessmentService(database).process_due(limit=limit)
    assert _rows(database, Job) == {}


async def test_builder_runs_off_loop_so_an_async_heartbeat_can_continue(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    _add_outlook(database, "one")
    clock = _Clock()
    started, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    _builder(monkeypatch, clock)

    def blocked_builder(db: Database, identity: str, *, assessed_at: datetime,
        archive: RawBodyArchive | None = None,
    ):
        worker_threads.append(threading.get_ident())
        started.set()
        if not release.wait(3):
            raise TimeoutError("controlled test release missing")
        return _projection(identity, assessed_at)

    monkeypatch.setattr(service_module, "build_assessment_basis", blocked_builder)
    task = asyncio.create_task(
        AnalyticalAssessmentService(database, clock=clock).process_due(limit=1),
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        beats = []
        for beat in range(5):
            await asyncio.sleep(0)
            beats.append(beat)
        assert beats == list(range(5)) and not task.done()
        assert worker_threads and worker_threads[0] != loop_thread
    finally:
        release.set()
    assert await asyncio.wait_for(task, 2) == 1


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_batch_retains_lock_until_worker_finishes_without_overlap(
    database: Database, monkeypatch: pytest.MonkeyPatch, cancellations: int,
):
    _add_outlook(database, "a-first", recorded_at=NOW - timedelta(seconds=1))
    _add_outlook(database, "b-next")
    clock = _Clock()
    first_started, second_started, release = threading.Event(), threading.Event(), threading.Event()
    first_finished = threading.Event()
    state_lock = threading.Lock()
    active = maximum = 0
    _builder(monkeypatch, clock)

    def blocked_builder(db: Database, identity: str, *, assessed_at: datetime,
        archive: RawBodyArchive | None = None,
    ):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
        try:
            if identity == "a-first":
                first_started.set()
                if not release.wait(3):
                    raise TimeoutError("controlled test release missing")
            else:
                second_started.set()
            return _projection(identity, assessed_at)
        finally:
            with state_lock:
                active -= 1
            if identity == "a-first":
                first_finished.set()

    monkeypatch.setattr(service_module, "build_assessment_basis", blocked_builder)
    service = AnalyticalAssessmentService(database, clock=clock)
    first = asyncio.create_task(service.process_due(limit=1))
    second = None
    overlapped = False
    try:
        assert await asyncio.to_thread(first_started.wait, 2)
        for _ in range(cancellations):
            first.cancel()
            await asyncio.sleep(0)
        second = asyncio.create_task(service.process_due(limit=1))
        overlapped = await asyncio.to_thread(second_started.wait, 0.1)
    finally:
        release.set()
    assert second is not None
    outcomes = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 2)
    assert await asyncio.to_thread(first_finished.wait, 2)
    assert isinstance(outcomes[0], asyncio.CancelledError) and outcomes[1] == 1
    assert not overlapped and maximum == 1
    assert {row["status"] for row in _rows(database, Job).values()} == {"completed"}


@pytest.mark.parametrize(("label", "boundary_seconds", "due_seconds"), [
    ("verified", 20, 20),
    ("not_due", 10, 10),
    ("pending", 45, 45),
    ("retry", 45, 45),
    ("verified", 8 * 3600, 6 * 3600),
    ("verified", None, 6 * 3600),
    ("not_due", None, 30 * 60),
])
def test_finish_caps_refresh_to_future_basis_boundary_without_promoting_source(
    database: Database, label: _LabelStatus, boundary_seconds: int | None, due_seconds: int,
) -> None:
    _add_outlook(database, "one")
    originals = _rows(database, Outlook)
    service = AnalyticalAssessmentService(database, clock=lambda: NOW)
    assert service.discover(NOW, MANIFEST) == 1
    claim = service.claim(NOW, MANIFEST)
    assert claim is not None
    assessment, basis = _projection("one", NOW, label=label)
    basis["next_time_boundary_at"] = (
        (NOW + timedelta(seconds=boundary_seconds)).isoformat()
        if boundary_seconds is not None else None
    )
    assert service.finish(*claim, assessment=assessment, basis=basis)
    saved = _rows(database, Job)["one"]
    assert saved["status"] == "completed" and saved["attempt"] == 1
    assert saved["next_attempt_at"] == NOW + timedelta(seconds=due_seconds)
    assert json.loads(saved["basis_json"]) == basis
    assert json.loads(saved["assessment_json"]) == assessment.model_dump(mode="json")
    assert saved["basis_hash"] == _digest(basis)
    assert saved["assessment_hash"] == _digest(assessment.model_dump(mode="json"))
    assert assessment.source_status == "unverifiable" and not assessment.directional_usable
    assert _rows(database, Outlook) == originals


@pytest.mark.parametrize("boundary_offset", [-1, 0])
def test_elapsed_completed_basis_requeues_before_later_due_and_requires_fresh_claim(
    database: Database, boundary_offset: int,
) -> None:
    _add_outlook(database, "one")
    originals = _rows(database, Outlook)
    service = AnalyticalAssessmentService(database, clock=lambda: NOW)
    assert service.discover(NOW, MANIFEST) == 1
    old_claim = service.claim(NOW, MANIFEST)
    assert old_claim is not None
    # A builder can finish after its captured boundary. Persisted old projections
    # must stay unusable until discovery obtains a new claim and a new build.
    assessment, basis = _projection("one", NOW - timedelta(seconds=2))
    basis["next_time_boundary_at"] = (NOW + timedelta(seconds=boundary_offset)).isoformat()
    assert service.finish(*old_claim, assessment=assessment, basis=basis)
    completed = _rows(database, Job)["one"]
    assert completed["status"] == "completed"
    assert completed["next_attempt_at"] == NOW + timedelta(hours=6)
    assert service.discover(NOW, MANIFEST) == 0
    pending = _rows(database, Job)["one"]
    assert pending["status"] == "pending" and pending["next_attempt_at"] == NOW
    assert pending["attempt"] == old_claim[2]
    assert pending["basis_json"] == completed["basis_json"]
    assert pending["basis_hash"] == completed["basis_hash"]
    assert pending["assessment_json"] == completed["assessment_json"]
    assert pending["assessment_hash"] == completed["assessment_hash"]
    assert not service.finish(*old_claim, assessment=assessment, basis=basis)
    fresh_claim = service.claim(NOW, MANIFEST)
    assert fresh_claim is not None and fresh_claim[1] != old_claim[1]
    assert fresh_claim[2] == old_claim[2] + 1
    fresh_assessment, fresh_basis = _projection("one", NOW)
    assert service.finish(*fresh_claim, assessment=fresh_assessment, basis=fresh_basis)
    fresh = _rows(database, Job)["one"]
    assert fresh["status"] == "completed" and fresh["attempt"] == 2
    assert fresh["assessment_json"] != completed["assessment_json"]
    assert fresh["basis_json"] != completed["basis_json"]
    assert fresh["lease_owner"] is None and fresh["lease_expires_at"] is None
    assert _rows(database, Outlook) == originals


@pytest.mark.parametrize("boundary_kind", ["future", "null", "absent"])
def test_discovery_preserves_completed_future_or_missing_basis_boundary(
    database: Database, boundary_kind: str,
) -> None:
    _add_outlook(database, "one")
    originals = _rows(database, Outlook)
    clock = _Clock()
    service = AnalyticalAssessmentService(database, clock=clock)
    assert service.discover(clock.now, MANIFEST) == 1
    claim = service.claim(clock.now, MANIFEST)
    assert claim is not None
    assessment, basis = _projection("one", clock.now)
    if boundary_kind != "absent":
        basis["next_time_boundary_at"] = (
            (NOW + timedelta(seconds=30)).isoformat() if boundary_kind == "future" else None
        )
    assert service.finish(*claim, assessment=assessment, basis=basis)
    completed = _rows(database, Job)
    clock.now += timedelta(seconds=1)
    assert service.discover(clock.now, MANIFEST) == 0
    assert service.claim(clock.now, MANIFEST) is None
    assert _rows(database, Job) == completed
    assert _rows(database, Outlook) == originals


def test_old_basis_version_still_requeues_without_rewriting_saved_projection(
    database: Database,
) -> None:
    _add_outlook(database, "one")
    originals = _rows(database, Outlook)
    assessment, basis = _projection("one", NOW)
    basis["schema_version"] = "analytical-assessment-basis.v2"
    basis.pop("loaded_verifier")
    basis["next_time_boundary_at"] = (NOW + timedelta(hours=1)).isoformat()
    # Explicit legacy derived-row fixture; never a current source certificate.
    with database.session() as session:
        session.add(Job(
            outlook_id="one", status="completed", attempt=1,
            evaluated_at=NOW, next_attempt_at=NOW + timedelta(hours=6),
            verifier_version=MANIFEST, basis_json=_canonical(basis), basis_hash=_digest(basis),
            assessment_json=_canonical(assessment.model_dump(mode="json")),
            assessment_hash=_digest(assessment.model_dump(mode="json")),
        ))
    completed = _rows(database, Job)["one"]
    service = AnalyticalAssessmentService(database, clock=lambda: NOW)
    assert service.discover(NOW, MANIFEST) == 0
    pending = _rows(database, Job)["one"]
    assert pending["status"] == "pending" and pending["next_attempt_at"] == NOW
    assert pending["basis_json"] == completed["basis_json"]
    assert pending["assessment_json"] == completed["assessment_json"]
    assert pending["attempt"] == 1 and _rows(database, Outlook) == originals
