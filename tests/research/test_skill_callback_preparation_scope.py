"""Physical SQLite skill preparation reuse and durable callback fences, no Provider.

The event reader's expensive CPI algorithm and callback domain material are
controlled seams; native scope, raw file checks, preparation readset, job/Run/link
fences and the actual callback transaction remain production implementations.
"""
from contextlib import nullcontext
from datetime import timedelta
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from apps.hub_worker.research_skill_callbacks import CallbackLeaseWatch

from packages.kernel.decision_hub_kernel.application import (
    event_scenario_autoproduction as scenarios,
    research_skill_callbacks as callbacks,
    research_skill_service as skills,
)
from packages.kernel.decision_hub_kernel.application.cpi_pre_event_context import _archive
from packages.kernel.decision_hub_kernel.persistence import original_preparation as prep
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord, RawCaptureRecord, RunRecord, RunRoleOutputRecord, utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.research_skill_callback_jobs import (
    CallbackLease, ResearchSkillCallbackJobRecord, ResearchSkillCallbackJobs,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.kernel.test_original_preparation import database as database


@pytest.fixture
def event_original(database, tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "source-data"))
    archive = _archive()
    digest, ref = archive.put(b"actual raw CPI bytes for scope boundary test")
    now = utcnow()
    with database.session() as session:
        session.add(RawCaptureRecord(
            capture_id="capture", context_id="context", url="https://example.test/cpi",
            body_hash=digest, body_ref=ref, byte_count=44, media_type="text/plain",
            adapter_version="controlled.v1", started_at=now, completed_at=now, recorded_at=now,
        ))
    reads = []

    def complete_read(session, forecast_id, *, archive, cutoff_at):
        # Keep a real captured native SQL original and real archive verification.
        row = session.get(RunRoleOutputRecord, "original", populate_existing=True)
        capture = session.get(RawCaptureRecord, "capture", populate_existing=True)
        archive.read(capture.body_ref)
        reads.append(forecast_id)
        return SimpleNamespace(
            source_guard=(("raw_capture", "capture", "guard"),), published_at=now,
            payload=row.payload_json,
        )

    monkeypatch.setattr(scenarios, "_read_event_owned_scenario_in_session", complete_read)
    return SimpleNamespace(
        database=database, archive=archive, digest=digest, now=now, reads=reads,
        cutoff=now + timedelta(seconds=10),
    )


def read_scenario(session, original, *, cutoff=None, archive=None):
    return scenarios.read_event_owned_scenario_in_session(
        session, "forecast", cutoff_at=cutoff or original.cutoff, archive=archive,
    )


def test_actual_prepared_skill_scope_reuses_default_archive_and_keeps_writer_readset(event_original):
    original = event_original

    def read(session):
        first = read_scenario(session, original)
        second = read_scenario(session, original)
        assert first.payload == second.payload == "{}"
        return first.payload

    saved = skills.prepare_skill_originals(original.database, read)
    assert original.reads == ["forecast"]  # New default archive instances must still hit.
    assert saved.queries and len(saved.rows) == 2
    with original.database.session() as session:
        session.get(RunRoleOutputRecord, "original").payload_json = '{"changed":true}'
    with original.database.session() as session, pytest.raises(ValueError, match="readset_changed"):
        prep.begin_immediate(session)
        saved.verify(session)


@pytest.mark.parametrize("change", ["raw", "cutoff"])
def test_scenario_hit_rechecks_external_bytes_and_each_caller_cutoff(event_original, change):
    original = event_original

    def read(session):
        read_scenario(session, original)
        if change == "raw":
            path = original.archive.root / original.digest[:2] / original.digest
            path.write_bytes(b"tampered")
            read_scenario(session, original)
        else:
            read_scenario(session, original, cutoff=original.now - timedelta(seconds=1))

    error = "archive_corrupt|raw_original_changed" if change == "raw" else "original_identity_invalid"
    with pytest.raises(ValueError, match=error):
        skills.prepare_skill_originals(original.database, read)


def test_new_physical_snapshot_rereads_changed_original(event_original):
    original = event_original

    def read(session):
        read_scenario(session, original)
        return read_scenario(session, original).payload

    assert skills.prepare_skill_originals(original.database, read).value == "{}"
    with original.database.session() as session:
        session.get(RunRoleOutputRecord, "original").payload_json = '{"new":true}'
    assert skills.prepare_skill_originals(original.database, read).value == '{"new":true}'
    assert original.reads == ["forecast", "forecast"]


@pytest.mark.parametrize("different", ["root", "class"])
def test_other_archive_root_or_custom_reader_does_not_share_results(event_original, tmp_path, different):
    original = event_original
    if different == "root":
        alternate = RawBodyArchive(tmp_path / "other")
    else:
        class CustomArchive(RawBodyArchive):
            pass

        alternate = CustomArchive(original.archive.root)
    alternate.put(b"actual raw CPI bytes for scope boundary test")

    def read(session):
        read_scenario(session, original)
        return read_scenario(session, original, archive=alternate).payload

    skills.prepare_skill_originals(original.database, read)
    assert original.reads == ["forecast", "forecast"]


def test_scope_cannot_carry_results_across_commit_in_same_session(event_original):
    original = event_original
    with original.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        with pytest.raises(ValueError), skills.skill_original_read_scope(session):
            read_scenario(session, original)
            session.commit()
            session.connection().exec_driver_sql("BEGIN")
            read_scenario(session, original)


def test_existing_event_scope_still_blocks_writes_and_restores_connection(database):
    from packages.kernel.decision_hub_kernel.application.event_read_scope import native_event_read_scope

    with database.session() as session:
        with native_event_read_scope(session, archive=RawBodyArchive(database.sqlite_path.parent)):
            assert session.get(RunRoleOutputRecord, "original") is not None
            with pytest.raises(OperationalError, match="readonly"):
                session.connection().exec_driver_sql(
                    "UPDATE run_role_outputs SET payload_json='changed' WHERE output_id='original'",
                )
        # The pre-existing lifecycle caller returns its connection to ordinary use.
        session.get(RunRoleOutputRecord, "original").payload_json = '{"after":true}'
    with database.session() as session:
        assert session.get(RunRoleOutputRecord, "original").payload_json == '{"after":true}'


class CountedJobs(ResearchSkillCallbackJobs):
    def __init__(self, database):
        super().__init__(database)
        self.full_checks = 0

    def check(self, lease):
        self.full_checks += 1
        return super().check(lease)


def callback_case(database, monkeypatch, count):
    now, deadline = utcnow(), utcnow() + timedelta(minutes=10)
    identity = dict(run_id="run", generation=1, root_session_id="native", execution_session_id="native", request_hash="a" * 64)
    body = dict(identity=identity, arguments={"operation": "load"})
    envelope = skills.encoded(dict(kind="tool", body=body)).decode()
    with database.session() as session:
        session.add(RunRecord(
            run_id="run", event_id="event", status="running", created_at=now, updated_at=now,
            lease_owner="research-owner", lease_expires_at=deadline,
        ))
        session.add(DshSessionLinkRecord(
            **{key: identity[key] for key in ("run_id", "generation", "request_hash")},
            dsh_session_id="native", state="running", upstream_identity_json="{}",
            plugin_build_hash="b" * 64, deadline_at=deadline, created_at=now, updated_at=now,
        ))
        session.add(ResearchSkillCallbackJobRecord(
            job_id="job", submission_key="c" * 64, semantic_operation_key="d" * 64,
            protocol_version="research-skill-callback.v1", kind="tool", **identity,
            proof_request_hash=hashlib.sha256(envelope.encode()).hexdigest(),
            original_occurrence_json="{}", stage_request_id="stage", operation="load",
            request_json=envelope, producer_identity="producer", deadline_at=deadline,
            status="running", phase="claimed", attempt=1, max_attempts=8, lease_epoch=1,
            cancel_version=0, lease_owner="callback-owner", lease_until=deadline,
            next_attempt_at=now, accepted_at=now, updated_at=now,
        ))
        for index in range(count):
            session.add(RunRoleOutputRecord(
                output_id=f"scope-row-{index}", run_id="run", role=f"scope-row:{index}",
                payload_json="{}", payload_hash="0" * 64, schema_version="fixture.v1", created_at=now,
            ))
    service = object.__new__(skills.ResearchSkillService)
    service.database = database
    service.packages = SimpleNamespace(contracts=SimpleNamespace(check=lambda *_: None))
    service.use = SimpleNamespace(selections=SimpleNamespace(originals_preparer=skills.prepare_skill_originals))
    service._identity = lambda *_: (dict(selection_id="selection", compiled_plan_ref="plan", compiled_plan_hash="e" * 64), SimpleNamespace(method_hash="f" * 64, task_request_hash="a" * 64), None)

    def material(session, _body):
        assert len(session.scalars(select(RunRoleOutputRecord)).all()) == count + 1
        return skills.SkillCallbackMaterial("response", skills.encoded({"complete": True}))

    service._tool_in_session = material
    monkeypatch.setattr(skills, "read_prepared_execution_in_session", lambda *_: SimpleNamespace(execution=SimpleNamespace(request=SimpleNamespace(deadline_at=deadline))))
    # Native source HANDLE behavior has dedicated tests; keep this callback test portable.
    monkeypatch.setattr(callbacks, "callback_source_lease_scope", lambda *_: nullcontext(None))
    jobs = CountedJobs(database)
    actual = callbacks.ResearchSkillCallbackService(service, jobs, producer_identity=lambda: "producer")
    return actual, jobs, CallbackLease("job", "callback-owner", 1, 1, 0)


@pytest.mark.parametrize("count", [1, 33])
def test_actual_callback_full_permission_checks_do_not_scale_with_readset(database, monkeypatch, count):
    actual, jobs, lease = callback_case(database, monkeypatch, count)
    watch = CallbackLeaseWatch(jobs, lease)
    local_checks = []

    def check():
        watch.check()
        local_checks.append(True)

    assert actual.process_claimed(lease, execution_check=check) == {"complete": True}
    with database.session() as session:
        row = session.get(ResearchSkillCallbackJobRecord, "job")
        assert row.status == "completed" and row.response_json == '{"complete":true}'
    assert jobs.full_checks == 2  # Fresh entry + after-preparation, independent of N / VM work.
    assert len(local_checks) > count


def test_cancel_after_preparation_still_rejects_actual_writer(database, monkeypatch):
    actual, jobs, lease = callback_case(database, monkeypatch, 3)
    watch = CallbackLeaseWatch(jobs, lease)
    commit = actual._commit

    def cancel_then_write(*args, **kwargs):
        with database.session() as session:
            session.get(RunRecord, "run").status = "cancelled"
        return commit(*args, **kwargs)

    monkeypatch.setattr(actual, "_commit", cancel_then_write)
    with pytest.raises(ValueError, match="skill_callback_cancelled"):
        actual.process_claimed(lease, execution_check=watch.check)
    with database.session() as session:
        row = session.get(ResearchSkillCallbackJobRecord, "job")
        assert row.status == "running" and row.response_json is None
        assert session.get(RunRecord, "run").status == "cancelled"

