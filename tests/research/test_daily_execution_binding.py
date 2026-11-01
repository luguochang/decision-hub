"""UNEXECUTED stage controls for real daily execution and original replay.

External exchange/Host transports are controlled offline. The production
Factory, arithmetic, Gateway writer, Graph, Review, Commit and assessor are
real. Negative writer seams change actual rows/returns, never inject a pass.
The basis carrier below is controlled derived-job metadata only; source and
SQL/archive verification use the actual builder/reader.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityResult,
    ResearchRunCommand,
)
from packages.evals import assessment_basis
from packages.kernel.decision_hub_kernel.application import daily_execution_binding as binding
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.decision import daily_strategy
from packages.kernel.decision_hub_kernel.persistence.db import (
    CapabilityReuseRecord,
    Database,
    DataJobRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.provider_adapters.historical.research import loaded_daily_implementation
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.workbench_adapters import durable_research_gateway as gateway_module
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.research.test_daily_original_assessment import (
    FIELDS,  # pyright: ignore[reportPrivateUsage]
    PublishedDaily,
    _assess_read_only,  # pyright: ignore[reportPrivateUsage]
    _create_published_daily,  # pyright: ignore[reportPrivateUsage]
    _NetworkFixture,  # pyright: ignore[reportPrivateUsage]
    _row_hashes,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(scope="module")
async def executed_seed(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[PublishedDaily]:
    network = pytest.MonkeyPatch()
    cast(_NetworkFixture, forbid_external_connections).__wrapped__(network)
    seeded: PublishedDaily | None = None
    try:
        seeded = await _create_published_daily(
            tmp_path_factory.mktemp("actual-current-daily-execution"),
            current_producer=True,
        )
        yield seeded
    finally:
        if seeded is not None:
            seeded.inputs.database.engine.dispose()
        network.undo()


@pytest.fixture
def executed(executed_seed: PublishedDaily, tmp_path: Path) -> Iterator[PublishedDaily]:
    source = executed_seed.inputs.database.sqlite_path
    assert source is not None
    destination = tmp_path / "isolated-current.sqlite3"
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as reader:
        with sqlite3.connect(destination) as writer:
            reader.backup(writer)
    shutil.copytree(executed_seed.directory / "raw-captures", tmp_path / "raw-captures")
    database = Database(f"sqlite+pysqlite:///{destination.as_posix()}")
    try:
        yield replace(
            executed_seed,
            inputs=replace(executed_seed.inputs, database=database),
            directory=tmp_path,
        )
    finally:
        database.engine.dispose()


def test_actual_loaded_callable_identity_is_repeatable_without_execution_claim() -> None:
    first = loaded_daily_implementation().identity()
    assert first == loaded_daily_implementation().identity()
    assert first.source_hashes and first.callable_code_hashes and first.defaults_hashes
    assert "decimal" in first.runtime
    assert binding.DailyLoadedIdentity.model_validate_json(first.model_dump_json()) == first
    assert binding.stable_daily_recipe(first) == binding.stable_daily_recipe(first)
    # Construction/identity never creates a computation witness or an admission row.
    assert binding._CURRENT.get() is None  # pyright: ignore[reportPrivateUsage]


def test_loaded_global_replacement_cannot_hide_behind_real_disk_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def replacement_mean(values: object) -> float:
        return 0.0

    monkeypatch.setattr(daily_strategy, "fmean", replacement_mean)
    with pytest.raises(ValueError, match="daily_loaded_callee_binding_changed"):
        loaded_daily_implementation().identity()


def test_loaded_code_replacement_is_rejected_before_identity_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function = loaded_daily_implementation().summary

    def replacement(dataset: str, rows: list[dict[str, object]]) -> dict[str, str]:
        return {"row_count": "0"}

    monkeypatch.setattr(function, "__code__", replacement.__code__)
    with pytest.raises(ValueError, match="daily_loaded_callable_differs_from_source"):
        loaded_daily_implementation().identity()


def test_actual_decimal_context_changes_method_identity() -> None:
    first = loaded_daily_implementation().identity()
    with localcontext() as context:
        context.clear_flags()
        before_arithmetic = loaded_daily_implementation().identity()
        assert Decimal(1) / Decimal(3) > 0
        assert any(context.flags.values())
        assert loaded_daily_implementation().identity() == before_arithmetic
    with localcontext() as context:
        context.prec += 1
        changed = loaded_daily_implementation().identity()
    assert first.runtime["decimal"] != changed.runtime["decimal"]
    assert binding.stable_daily_recipe(first) != binding.stable_daily_recipe(changed)


def test_genuine_current_gate_publication_has_source_and_method_proof(
    executed: PublishedDaily,
) -> None:
    before = _row_hashes(executed)
    replay, public = _assess_read_only(executed)
    assert replay.source_status == public.source_status == "verified"
    assert replay.method_status == public.method_status == "verified"
    assert replay.stable_recipe_hash == public.stable_recipe_hash
    assert replay.stable_recipe_hash is not None and len(replay.stable_recipe_hash) == 64
    assert public.method_scope == "daily_rule_loaded_implementation"
    assert public.label_status == "not_due" and not public.directional_usable
    assert any(ref.schema_version == "daily-execution-admission.v1" for ref in replay.refs)
    assert _row_hashes(executed) == before


@pytest.mark.parametrize("fault", ["missing_role", "resigned_role", "raw_return"])
def test_method_proof_is_independent_and_never_repaired(
    executed: PublishedDaily, fault: str
) -> None:
    with executed.inputs.database.session() as session:
        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(
                run_id=executed.inputs.request.run_id,
                schema_version="daily-execution-admission.v1",
            )
            .one()
        )
        if fault == "missing_role":
            session.delete(row)
        elif fault == "resigned_role":
            body = json.loads(row.payload_json)
            body["receipt"]["summary_json"] = _canonical({"row_count": "1"})
            row.payload_json = _canonical(body)
            row.payload_hash = _sha(row.payload_json)
            row.output_id = "daily_execution_" + row.payload_hash
        else:
            original = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=executed.inputs.request.run_id,
                    schema_version="research-adapter-return-observation.v1",
                )
                .one()
            )
            original.payload_hash = "0" * 64
    before = _row_hashes(executed)
    replay, public = _assess_read_only(executed)
    assert replay.source_status == public.source_status == "verified"
    assert replay.method_status != "verified" and public.method_status == "unverifiable"
    assert replay.stable_recipe_hash is None and public.stable_recipe_hash is None
    assert not public.directional_usable
    assert _row_hashes(executed) == before


def _assert_actual_outer_audit(directory: Path, *, semantic_rows: int, cancelled: bool) -> None:
    database = Database(f"sqlite+pysqlite:///{(directory / 'daily.sqlite3').as_posix()}")
    try:
        with database.session() as session:
            run = session.query(RunRecord).one()
            reservation = session.query(ResearchToolCallReservationRecord).one()
            assert reservation.status == "completed" and reservation.result_json is not None
            original = ResearchCapabilityResult.model_validate_json(reservation.result_json)
            assert original.cost_usd == 0.0  # This actual local computation has no exchange charge.
            assert len(original.facts or []) == len(FIELDS)
            assert session.query(ResearchFactRecord).count() == semantic_rows
            assert session.query(ResearchEvidenceRecord).count() == int(semantic_rows > 0)
            assert session.query(CapabilityReuseRecord).count() == 0
            assert (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    schema_version="daily-execution-admission.v1",
                )
                .count()
                == 0
            )
            assert (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    schema_version="research-adapter-return-observation.v1",
                )
                .count()
                == 1
            )
            capture = session.query(RawCaptureRecord).one()
            assert capture.body_ref is not None
            assert RawBodyArchive(directory / "raw-captures").read(capture.body_ref)
            if cancelled:
                assert run.status == "cancelled"
                assert reservation.error_json is None
                assert (
                    session.query(ResearchTraceRecord)
                    .filter_by(reference_type="cancellation_cutoff")
                    .count()
                    == 1
                )
            else:
                assert run.status == "running" and reservation.error_json is not None
    finally:
        database.engine.dispose()


@pytest.mark.asyncio
async def test_actual_cancel_keeps_late_originals_and_cost_without_method_role(
    tmp_path: Path,
) -> None:
    def cancel(inputs: PreparedInputs, actual: ResearchCapabilityResult) -> None:
        assert all(binding.ATTRIBUTE in fact.attributes for fact in actual.facts or [])
        command = ResearchRunCommand(
            schema_version="research-run-command.v1",
            request_id="daily-real-owner-cancel",
            command="cancel",
            reason="Controlled cancellation after actual arithmetic.",
        )
        assert (
            ResearchCommandService(inputs.database).apply(inputs.request.run_id, command).status
            == "accepted"
        )

    directory = tmp_path / "actual-cancel"
    attempt = await _create_published_daily(
        directory,
        current_producer=True,
        publish=False,
        after_actual=cancel,
    )
    try:
        assert attempt.outlook_id == "" and attempt.result is not None
        assert all(fact.quality == "accepted" for fact in attempt.result.facts or [])
        _assert_actual_outer_audit(directory, semantic_rows=len(FIELDS), cancelled=True)
    finally:
        attempt.inputs.database.engine.dispose()


@pytest.mark.asyncio
async def test_post_flush_actual_fact_drift_rolls_back_semantics_but_not_raw_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = gateway_module.freeze_daily_execution_in_session
    seen: list[object] = []

    def drift(session: Session, **kwargs: Any) -> str | None:
        seen.append(session.connection().connection.driver_connection)
        actual = session.query(ResearchFactRecord).filter_by(field="row_count").one()
        actual.value_json = '"1"'
        session.flush()
        return checker(session, **kwargs)

    monkeypatch.setattr(gateway_module, "freeze_daily_execution_in_session", drift)
    directory = tmp_path / "late-fact-drift"
    with pytest.raises(ResearchCapabilityError, match="validated capability result"):
        await _create_published_daily(directory, current_producer=True, publish=False)
    assert len(seen) == 1
    _assert_actual_outer_audit(directory, semantic_rows=0, cancelled=False)


@pytest.mark.asyncio
async def test_json_claim_without_actual_computation_witness_cannot_admit(tmp_path: Path) -> None:
    def add_json_claim(
        inputs: PreparedInputs, actual: ResearchCapabilityResult
    ) -> ResearchCapabilityResult:
        facts = []
        for fact in actual.facts or []:
            changed = fact.model_copy(
                update={
                    "attributes": {
                        **fact.attributes,
                        binding.ATTRIBUTE: '{"schema_version":"daily-execution.v1"}',
                    }
                }
            )
            keys = (
                "requirement_id",
                "metric_family",
                "field",
                "instrument",
                "venue",
                "value",
                "unit",
                "window_start_at",
                "window_end_at",
                "event_offset",
                "published_at",
                "source_id",
                "independence_group",
                "delay_class",
                "payload_schema_ref",
                "attributes",
            )
            digest = research_fact_payload_hash(**{key: getattr(changed, key) for key in keys})
            facts.append(
                changed.model_copy(
                    update={
                        "payload_hash": digest,
                        "fact_id": research_fact_instance_id(
                            evidence_id=changed.evidence_id, payload_hash=digest
                        ),
                    }
                )
            )
        return actual.model_copy(update={"facts": facts})

    directory = tmp_path / "json-without-witness"
    with pytest.raises(ResearchCapabilityError, match="validated capability result"):
        await _create_published_daily(directory, publish=False, after_actual=add_json_claim)
    _assert_actual_outer_audit(directory, semantic_rows=0, cancelled=False)


@pytest.mark.asyncio
async def test_two_genuine_runs_share_recipe_but_keep_distinct_originals(
    executed_seed: PublishedDaily,
    tmp_path: Path,
) -> None:
    peer = await _create_published_daily(tmp_path / "peer", current_producer=True)
    try:
        first, _ = _assess_read_only(executed_seed)
        second, _ = _assess_read_only(peer)
        assert first.source_status == second.source_status == "verified"
        assert first.method_status == second.method_status == "verified"
        assert first.stable_recipe_hash == second.stable_recipe_hash
        assert executed_seed.inputs.request.run_id != peer.inputs.request.run_id
        assert executed_seed.capture_id != peer.capture_id
        assert _row_hashes(executed_seed) != _row_hashes(peer)
    finally:
        peer.inputs.database.engine.dispose()


def _derived_basis(source: PublishedDaily):
    archive = RawBodyArchive(source.directory / "raw-captures")
    now = utcnow()
    assessment, basis = assessment_basis.build_assessment_basis(
        source.inputs.database,
        source.outlook_id,
        assessed_at=now,
        archive=archive,
    )
    assert assessment.source_status == assessment.method_status == "verified"
    assert basis["daily_runtime_identity_hash"]
    assert any("data_jobs" in item["statement"] for item in basis["read_queries"])
    assert basis["archive_reads"]
    payload, assessed = _canonical(basis), _canonical(assessment.model_dump(mode="json"))
    # Controlled projection metadata carrier; no fake original source/authentication.
    job = SimpleNamespace(
        outlook_id=source.outlook_id,
        status="completed",
        next_attempt_at=now + timedelta(days=1),
        evaluated_at=now,
        verifier_version=basis["manifest_hash"],
        basis_json=payload,
        basis_hash=_sha(payload),
        assessment_json=assessed,
        assessment_hash=_sha(assessed),
    )
    return cast(assessment_basis.AssessmentJob, job), archive, now


def _valid_basis(
    source: PublishedDaily,
    job: assessment_basis.AssessmentJob,
    archive: RawBodyArchive,
    now: datetime,
):
    path = source.inputs.database.sqlite_path
    assert path is not None
    engine = create_engine(
        "sqlite+pysqlite://",
        creator=lambda: sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        ),
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    checkouts: list[object] = []

    def checked_out(native: object, _record: object, _proxy: object) -> None:
        checkouts.append(native)

    event.listen(engine, "checkout", checked_out)
    try:
        with Session(engine) as session:
            connection = session.connection()
            connection.exec_driver_sql("BEGIN")
            connection.info["assessment_replay_authorizer_owned"] = True
            try:
                result = assessment_basis.read_valid_assessment(
                    session, job, now=now, archive=archive
                )
            finally:
                connection.info.pop("assessment_replay_authorizer_owned", None)
        assert len(checkouts) == 1
        return result
    finally:
        engine.dispose()


@pytest.mark.parametrize("fault", ["job", "body", "decimal"])
def test_real_basis_loses_daily_verdict_on_original_or_loaded_runtime_change(
    executed: PublishedDaily,
    fault: str,
) -> None:
    job, archive, now = _derived_basis(executed)
    assert _valid_basis(executed, job, archive, now) is not None
    if fault == "decimal":
        with localcontext() as context:
            context.prec += 1
            assert _valid_basis(executed, job, archive, now) is None
        assert _valid_basis(executed, job, archive, now) is not None
    else:
        with executed.inputs.database.session() as session:
            if fault == "job":
                session.query(DataJobRecord).one().requested_by = "changed-original-requester"
            else:
                capture = session.get(RawCaptureRecord, executed.capture_id)
                assert capture is not None and capture.body_hash is not None
                (
                    executed.directory / "raw-captures" / capture.body_hash[:2] / capture.body_hash
                ).write_bytes(b"changed")
        assert _valid_basis(executed, job, archive, now) is None


_FRESH_DAILY_READER = r"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from packages.evals.analytical_assessment import assess_outlook_in_session
from packages.evals.daily_assessment import assess_daily_source_in_session
from packages.kernel.decision_hub_kernel.persistence.db import AnalyticalOutlookRecord, utcnow
from packages.provider_adapters.research.raw_capture import RawBodyArchive

database_path, archive_path, outlook_id = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
assert database_path.is_file() and archive_path.is_dir()

def row_hashes():
    result = {}
    with sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for (name,) in tables:
            assert '"' not in name
            rows = connection.execute(f'SELECT * FROM "{name}"').fetchall()
            result[name] = sorted(hashlib.sha256(json.dumps(
                row, ensure_ascii=False,
                default=lambda value: value.hex() if isinstance(value, bytes) else str(value),
                separators=(",", ":"),
            ).encode()).hexdigest() for row in rows)
    return result

before = row_hashes()
engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(
    f"file:{database_path.as_posix()}?mode=ro", uri=True,
), poolclass=QueuePool, pool_size=1, max_overflow=0)
checkouts, statements = [], []

def checked_out(connection, _record, _proxy):
    checkouts.append(connection)

def observed(_connection, _cursor, statement, _parameters, _context, _many):
    statements.append(statement)

event.listen(engine, "checkout", checked_out)
event.listen(engine, "before_cursor_execute", observed)
try:
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN")
        outlook = session.get(AnalyticalOutlookRecord, outlook_id)
        assert outlook is not None
        archive, now = RawBodyArchive(archive_path), utcnow()
        replay = assess_daily_source_in_session(session, outlook, assessed_at=now, archive=archive)
        public = assess_outlook_in_session(session, outlook_id, assessed_at=now, archive=archive)
        assert replay.source_status == public.source_status == "verified"
        assert replay.method_status == public.method_status == "verified"
        assert replay.stable_recipe_hash == public.stable_recipe_hash
        assert replay.stable_recipe_hash is not None and len(replay.stable_recipe_hash) == 64
        assert public.method_scope == "daily_rule_loaded_implementation"
        assert public.label_status == "not_due" and not public.directional_usable
        roles = sorted((ref.role, ref.output_id, ref.payload_hash) for ref in replay.refs
                       if ref.schema_version == "daily-execution-admission.v1")
        assert len(roles) == 1
        assert not session.new and not session.dirty and not session.deleted
    assert len(checkouts) == 1
    assert statements and all(statement.lstrip().split(None, 1)[0].upper()
                              in {"SELECT", "BEGIN", "PRAGMA", "WITH"} for statement in statements)
    assert row_hashes() == before
    print(json.dumps({
        "pid": os.getpid(), "executable": sys.executable,
        "source_status": public.source_status, "method_status": public.method_status,
        "stable_recipe_hash": public.stable_recipe_hash, "roles": roles, "row_hashes": before,
    }, sort_keys=True))
finally:
    engine.dispose()
"""


def test_saved_genuine_daily_execution_is_verified_by_fresh_python_reader(
    executed_seed: PublishedDaily,
) -> None:
    """Reuse the current seed; the child imports no test fixture or producer."""
    database_path = executed_seed.inputs.database.sqlite_path
    assert database_path is not None
    before = _row_hashes(executed_seed)
    replay, public = _assess_read_only(executed_seed)
    assert replay.source_status == public.source_status == "verified"
    assert replay.method_status == public.method_status == "verified"
    assert replay.stable_recipe_hash == public.stable_recipe_hash
    assert public.stable_recipe_hash is not None
    roles = sorted(
        [ref.role, ref.output_id, ref.payload_hash]
        for ref in replay.refs
        if ref.schema_version == "daily-execution-admission.v1"
    )
    assert len(roles) == 1
    root = Path(__file__).resolve().parents[2]
    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _FRESH_DAILY_READER,
            str(root),
            str(database_path),
            str(executed_seed.directory / "raw-captures"),
            executed_seed.outlook_id,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    observed = json.loads(child.stdout)
    assert observed["pid"] != os.getpid()
    assert Path(observed["executable"]).resolve() == Path(sys.executable).resolve()
    assert observed["source_status"] == observed["method_status"] == "verified"
    assert observed["stable_recipe_hash"] == public.stable_recipe_hash
    assert observed["roles"] == roles
    assert observed["row_hashes"] == before
    assert _row_hashes(executed_seed) == before
