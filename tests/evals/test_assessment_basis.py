"""Real SQLite read dependencies and raw-byte invalidation, without external I/O."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from packages.evals import assessment_basis as module
from packages.evals.assessment_basis import (
    AssessmentJob,
    assessment_manifest_hash,
    build_assessment_basis,
    read_valid_assessment,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord,
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    Database,
    RawCaptureRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.research.test_analytical_assessment import Published, published, published_seed
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert published is not None and published_seed is not None
assert forbid_external_connections is not None
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _job(assessment: Any, basis: dict[str, Any]) -> SimpleNamespace:
    text = assessment.model_dump(mode="json")
    return SimpleNamespace(
        outlook_id=assessment.outlook_id,
        status="completed",
        evaluated_at=assessment.assessed_at,
        next_attempt_at=assessment.assessed_at + timedelta(hours=6),
        verifier_version=basis["manifest_hash"],
        basis_json=_json(basis),
        basis_hash=_hash(_json(basis)),
        assessment_json=_json(text),
        assessment_hash=_hash(_json(text)),
    )


@contextmanager
def _snapshot(database: Database) -> Generator[Session, None, None]:
    with database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        connection.info["assessment_replay_authorizer_owned"] = True
        try:
            yield session
        finally:
            connection.info.pop("assessment_replay_authorizer_owned", None)


def _read(
    database: Database,
    job: SimpleNamespace,
    *,
    now: datetime = NOW,
    archive: RawBodyArchive | None = None,
    memo: dict[Any, Any] | None = None,
):
    with _snapshot(database) as session:
        return read_valid_assessment(
            session, cast(AssessmentJob, job), now=now, archive=archive, memo=memo
        )


@pytest.fixture
def ordinary(tmp_path: Path) -> Generator[Database, None, None]:
    """An ordinary daily row, intentionally never an AI original-source certificate."""
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'basis.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            AnalyticalOutlookRecord(
                outlook_id="basis-daily",
                run_id="ordinary-control",
                artifact_id="ordinary-artifact",
                study_id=None,
                market="crypto",
                instrument="BTC-USDT",
                method_family="daily_rule",
                method_version="daily-trend-v0.1",
                horizon="7d",
                action="long",
                subjective_probability=None,
                probability_status="not_available",
                data_as_of=NOW - timedelta(minutes=1),
                window_start_at=NOW,
                window_end_at=NOW + timedelta(days=7),
                result_hash="1" * 64,
                evidence_refs_json="[]",
                fact_version_refs_json="[]",
                raw_payload_json="{}",
                trigger="ordinary test",
                invalidation="ordinary test",
                cohort="prospective",
                eligibility="excluded",
                exclusion_reasons_json='["ordinary_test_only"]',
                research_quality="not_available",
                recorded_at=NOW,
            )
        )
    try:
        yield database
    finally:
        database.engine.dispose()


def test_real_builder_preserves_unverified_daily_and_round_trips_actual_reads(
    ordinary: Database,
) -> None:
    assessed, basis = build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)
    assert assessed.source_status == "unverifiable" and assessed.label_status == "not_due"
    assert not assessed.directional_usable
    assert basis["read_queries"] and all(
        item["statement"].startswith("SELECT") for item in basis["read_queries"]
    )
    saved = _read(ordinary, _job(assessed, basis))
    assert saved is not None
    assert saved == assessed and saved.selection_status == "unchecked"
    with ordinary.session() as session:
        assert session.query(AnalyticalOutlookRecord).count() == 1
        assert session.query(RunRoleOutputRecord).count() == 0


def test_old_loaded_verifier_cannot_attest_new_disk_implementation(
    ordinary: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the old worker ABI reading the unchanged, new release files.

    This uses the real SQLite builder and replay reader on an explicitly
    unverified daily control. It never grants original-source certification.
    """
    assert module.SCHEMA == "analytical-assessment-basis.v3"
    current_manifest = assessment_manifest_hash()
    assessed, current_basis = build_assessment_basis(
        ordinary, "basis-daily", assessed_at=NOW, archive=None,
    )
    # The new import-time boundary prevents even a stale ABI from running a
    # build against files that no longer match its already loaded manifest.
    with monkeypatch.context() as stale_worker:
        stale_worker.setattr(module, "SCHEMA", "analytical-assessment-basis.v2")
        assert assessment_manifest_hash() != current_manifest
        with pytest.raises(ValueError, match="assessment_loaded_verifier_source_changed"):
            build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)
    old_basis = copy.deepcopy(current_basis)
    old_basis["schema_version"] = "analytical-assessment-basis.v2"
    old_basis.pop("loaded_verifier")
    assert old_basis["manifest_hash"] == current_manifest
    assert _read(ordinary, _job(assessed, old_basis)) is None
    assert _read(ordinary, _job(assessed, current_basis)) == assessed
    assert assessed.source_status == "unverifiable" and not assessed.directional_usable


def test_original_row_change_and_previously_absent_observation_invalidate(
    ordinary: Database,
) -> None:
    assessed, basis = build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)
    job = _job(assessed, basis)
    with ordinary.session() as session:
        row = session.get(AnalyticalOutlookRecord, "basis-daily")
        assert row is not None
        row.trigger = "changed original content, no hash-column update"
    assert _read(ordinary, job) is None
    with ordinary.session() as session:
        row = session.get(AnalyticalOutlookRecord, "basis-daily")
        assert row is not None
        row.trigger = "ordinary test"
        session.add(
            AnalyticalObservationRecord(
                outlook_id="basis-daily",
                status="pending",
                attempt=0,
                next_attempt_at=NOW,
                benchmark="close_1m",
            )
        )
    assert _read(ordinary, job) is None


@pytest.mark.parametrize("fault", ["due", "future", "manifest", "hash", "missing_scope"])
def test_job_time_manifest_and_owned_snapshot_are_required(ordinary: Database, fault: str) -> None:
    assessed, basis = build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)
    job = _job(assessed, basis)
    if fault == "due":
        job.next_attempt_at = NOW
    elif fault == "future":
        assert _read(ordinary, job, now=NOW - timedelta(seconds=1)) is None
        return
    elif fault == "manifest":
        job.verifier_version = "2" * 64
    elif fault == "hash":
        job.basis_json += " "
    else:
        with ordinary.session() as session:
            assert (
                read_valid_assessment(session, cast(AssessmentJob, job), now=NOW, archive=None)
                is None
            )
        return
    assert _read(ordinary, job) is None


def test_not_due_time_boundary_invalidates_even_when_job_due_is_incorrectly_long(
    ordinary: Database,
) -> None:
    assessed, basis = build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)
    job = _job(assessed, basis)
    job.next_attempt_at = NOW + timedelta(days=8)
    assert _read(ordinary, job, now=NOW + timedelta(days=7)) is None


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM analytical_outlooks",
        "SELECT 1; DELETE FROM analytical_outlooks",
        "SELECT * FROM sqlite_master",
        "SELECT load_extension(?)",
        "SELECT * FROM analytical_assessment_jobs",
    ],
)
def test_resigned_receipt_cannot_replay_mutation_or_unknown_objects(
    ordinary: Database,
    sql: str,
) -> None:
    assessed, basis = build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)
    altered = copy.deepcopy(basis)
    altered["read_queries"][0]["statement"] = sql
    altered["read_queries"][0]["parameters"] = {"kind": "positional", "values": []}
    job = _job(assessed, altered)
    assert _read(ordinary, job) is None
    with ordinary.session() as session:
        assert session.get(AnalyticalOutlookRecord, "basis-daily") is not None
        assert session.query(AnalyticalOutlookRecord).count() == 1


def test_authorizer_cleanup_and_connection_local_override_are_safe(ordinary: Database) -> None:
    with _snapshot(ordinary) as session:
        connection = session.connection()
        native = connection.connection.driver_connection
        assert isinstance(native, sqlite3.Connection)
        with module._readonly(connection):  # pyright: ignore[reportPrivateUsage] # Safety seam.
            assert native.execute(
                "SELECT outlook_id FROM analytical_outlooks WHERE method_family LIKE ?",
                ("daily%",),
            ).fetchone() == ("basis-daily",)
            with pytest.raises(sqlite3.DatabaseError):
                native.execute("DELETE FROM analytical_outlooks")
        assert native.execute("SELECT count(*) FROM analytical_outlooks").fetchone() == (1,)
        def overridden_length(_value: object) -> int:
            return 99

        native.create_function("length", 1, overridden_length)
        try:
            with module._readonly(connection):  # pyright: ignore[reportPrivateUsage]
                with pytest.raises(sqlite3.DatabaseError):
                    native.execute("SELECT length(trigger) FROM analytical_outlooks")
        finally:
            # This isolated test engine is disposed by its fixture; the custom
            # registration is never installed on a formal or reused application engine.
            native.create_function("length", 1, None)


def test_strict_parameter_codec_preserves_bytes_floats_and_rejects_custom_values() -> None:
    values = (None, True, 2, -0.0, "literal $() ; --", b"\x00\xff")
    encoded = module._parameters(values)  # pyright: ignore[reportPrivateUsage]
    decoded = module._unparameters(encoded)  # pyright: ignore[reportPrivateUsage]
    assert isinstance(decoded, tuple)
    assert decoded == values and type(decoded[1]) is bool
    assert decoded[3].hex() == "-0x0.0p+0"
    with pytest.raises(ValueError, match="native_type"):
        module._parameters((object(),))  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="parameter_invalid"):
        module._unparameters({"kind": "positional", "values": [["int", True]]})  # pyright: ignore


def test_query_limit_does_not_mint_a_partial_basis(
    ordinary: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_QUERIES", 1)
    with pytest.raises(ValueError, match="query_limit"):
        build_assessment_basis(ordinary, "basis-daily", assessed_at=NOW, archive=None)


def test_manifest_detects_actual_file_bytes_even_if_size_and_name_stay_equal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "manifest"
    (root / "sources").mkdir(parents=True)
    path = root / "sources" / "reader.py"
    path.write_bytes(b"original")
    gateway_ref = "packages/workbench_adapters/durable_research_gateway.py"
    gateway = root / gateway_ref
    gateway.parent.mkdir(parents=True)
    gateway.write_bytes((Path(module.__file__).resolve().parents[2] / gateway_ref).read_bytes())
    monkeypatch.setattr(module, "_ROOT", root)
    monkeypatch.setattr(module, "_SOURCE_DIRS", ("sources",))
    first = assessment_manifest_hash()
    path.write_bytes(b"modified")
    assert assessment_manifest_hash() != first


def _completed_label(database: Database, archive: RawBodyArchive) -> None:
    start, end = NOW - timedelta(hours=1), NOW - timedelta(minutes=30)
    window = {}
    with database.session() as session:
        outlook = session.get(AnalyticalOutlookRecord, "basis-daily")
        assert outlook is not None
        outlook.window_start_at, outlook.recorded_at = start, start
        outlook.window_end_at, outlook.data_as_of = end, start
        for side, boundary, price in (("entry", start, 100), ("exit", end, 110)):
            stamp = int((boundary - timedelta(minutes=1)).timestamp() * 1000)
            body = _json(
                {"code": "0", "data": [[str(stamp), "1", "1", "1", str(price), "1", "1", "1", "1"]]}
            ).encode()
            digest, reference = archive.put(body)
            session.add(
                RawCaptureRecord(
                    capture_id=side,
                    context_id="analytical-observation:basis-daily:1",
                    url=(
                        "https://www.okx.com/api/v5/market/history-candles?"
                        f"instId=BTC-USDT&bar=1m&after={int(boundary.timestamp() * 1000)}"
                    ),
                    status_code=200,
                    body_ref=reference,
                    body_hash=digest,
                    byte_count=len(body),
                    media_type="application/json",
                    adapter_version="test-only-archived-candle",
                    error_code=None,
                    started_at=end,
                    completed_at=NOW,
                    recorded_at=NOW,
                )
            )
            window[side] = {
                "observed_at": boundary.isoformat(),
                "received_at": NOW.isoformat(),
                "last": price,
                "instrument": "BTC-USDT",
            }
        market_return = (110 / 100 - 1) * 100
        session.add(
            AnalyticalObservationRecord(
                outlook_id="basis-daily",
                status="completed",
                attempt=1,
                next_attempt_at=NOW,
                evaluated_at=NOW,
                benchmark="close_1m",
                quality_status="estimated",
                market_return_pct=market_return,
                directional_return_pct=market_return,
                direction_correct=1,
                window_json=_json(window),
            )
        )
        session.add(
            AnalyticalObservationAttemptRecord(
                outlook_id="basis-daily",
                attempt=1,
                status="completed",
                started_at=end,
                finished_at=NOW,
                error_code=None,
            )
        )


@pytest.mark.parametrize("fault", ["corrupt", "missing", "extra_capture", "attempt"])
def test_real_archived_label_and_capture_set_changes_invalidate(
    ordinary: Database,
    tmp_path: Path,
    fault: str,
) -> None:
    archive = RawBodyArchive(tmp_path / "raw")
    _completed_label(ordinary, archive)
    assessed, basis = build_assessment_basis(
        ordinary, "basis-daily", assessed_at=NOW, archive=archive
    )
    assert assessed.label_status == "verified" and assessed.source_status == "unverifiable"
    assert not assessed.directional_usable and len(basis["archive_reads"]) == 2
    job = _job(assessed, basis)
    assert _read(ordinary, job, archive=archive) == assessed
    capture = basis["archive_reads"][0]
    digest = capture["ref"].rsplit("/", 1)[1]
    path = archive.root / digest[:2] / digest
    if fault == "corrupt":
        path.write_bytes(b"changed actual bytes, unchanged raw_captures hash")
    elif fault == "missing":
        path.unlink()
    elif fault == "attempt":
        with ordinary.session() as session:
            attempt = session.get(AnalyticalObservationAttemptRecord, ("basis-daily", 1))
            assert attempt is not None
            attempt.status = "failed"
    else:
        with ordinary.session() as session:
            original = session.get(RawCaptureRecord, "entry")
            assert original is not None
            columns = {
                column.key: getattr(original, column.key) for column in original.__table__.columns
            }
            columns["capture_id"] = "duplicate-entry"
            session.add(RawCaptureRecord(**columns))
    assert _read(ordinary, job, archive=archive) is None


def test_recorded_missing_archive_can_be_reused_only_while_it_is_still_missing(
    ordinary: Database,
    tmp_path: Path,
) -> None:
    archive = RawBodyArchive(tmp_path / "raw")
    _completed_label(ordinary, archive)
    with ordinary.session() as session:
        capture = session.get(RawCaptureRecord, "entry")
        assert capture is not None and capture.body_hash is not None
        path = archive.root / capture.body_hash[:2] / capture.body_hash
    original = path.read_bytes()
    path.unlink()
    assessed, basis = build_assessment_basis(
        ordinary, "basis-daily", assessed_at=NOW, archive=archive
    )
    assert assessed.label_status == "invalid"
    assert basis["archive_reads"][0]["status"] == "missing"
    job = _job(assessed, basis)
    assert _read(ordinary, job, archive=archive) == assessed
    path.write_bytes(original)
    assert _read(ordinary, job, archive=archive) is None


async def test_genuine_factory_runtime_graph_publication_basis_detects_original_prompt_drift(
    published: Published,
) -> None:
    """Reuse the existing bounded genuine producer, not a stubbed verified assessor."""
    database = published.source.database
    now = utcnow()
    assessed, basis = build_assessment_basis(
        database, published.outlook_id, assessed_at=now, archive=None
    )
    assert assessed.source_status == assessed.method_status == "verified"
    assert any("dsh_session_prompts" in query["statement"] for query in basis["read_queries"])
    assert any("run_input_refs" in query["statement"] for query in basis["read_queries"])
    job = _job(assessed, basis)
    assert _read(database, job, now=now) == assessed
    with database.session() as session:
        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(
                run_id=published.source.request.run_id,
                role="dsh_prompt_integrity:1",
            )
            .one()
        )
        row.payload_json += " "  # Keep hash/identity untouched: raw-byte footprint must notice.
    assert _read(database, job, now=now) is None
