"""Private boundary controls using saved actual39 bytes, not a new R2 pass.

The lifecycle carrier is injected directly into the issuer's private map only
to test save/confirm/cancel/COMMIT boundaries. No control claims that this
carrier passed a fresh full original/frozen replay. No original reader is
stubbed, the 91-second original consumer is not repeated, and no live DB is
opened. A separate test calls the real current reader on a legacy private Run.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from packages.kernel.decision_hub_kernel.application import research_quality_view as quality_module
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import (
    BundleArchive, BundleCommitUnconfirmed, PublicationBundleLifecycle,
)
from packages.kernel.decision_hub_kernel.application.research_publication_frozen_reader import (
    FrozenPublicationPreparer, OriginalPublicationRead, PreparedFrozenPublication,
    frozen_bundle_from_verified_read, read_registered_original,
    require_original_material_matches,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.frozen_publication_material import (
    authenticated_material_session, restore_authenticated_native,
)
from packages.kernel.decision_hub_kernel.persistence.publication_dependency_native import (
    Limits, NativeCollector, SourceFile,
)
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleConfirmationRecord as Ack,
    PublicationBundleJobRecord as Job,
    PublicationBundleRecord as Saved,
)
from packages.kernel.decision_hub_kernel.persistence.run_spot_admission import RunSpotAdmissionRecord

DEV = Path(__file__).resolve().parents[2]
ROOT = DEV.parent / "decision-hub"
B2 = ROOT / "artifacts/market-risk-implementation-20261007/B2"
NOW = datetime(2026, 10, 10, 0, 30, tzinfo=UTC)
BOUNDS = Limits(32, 128, 2 * 1024 * 1024, 1000000, 2000000)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode_native(item):
    kind, value = item["type"], item["value"]
    if kind == "blob":
        return base64.b64decode(value, validate=True)
    if kind == "real":
        return float.fromhex(value)
    return value


@pytest.fixture(scope="module")
def actual39():
    fixture_raw = (B2 / "release45/publication-originals39-native-fixture45_01.json").read_bytes()
    assert sha(fixture_raw) == "456fcac681244d7630d3062839af999122015aca6f9c087dd520d9555b21356c"
    fixture = json.loads(fixture_raw)
    quality_raw = (B2 / "release39/eth-terminal39_01/quality.json").read_bytes()
    assert sha(quality_raw) == "49e3b32d49357f023879317c68ec40d98b277e8e39c6bfbe0a3dcf084e3368c1"
    quality = quality_module.ResearchQualityViewV1.model_validate_json(quality_raw)
    return fixture, quality


@pytest.fixture
def boundary(tmp_path, monkeypatch, actual39):
    fixture, quality = actual39
    path = tmp_path / "private-boundaries.sqlite3"
    with closing(sqlite3.connect(path)) as native, native:
        for table in fixture["tables"]:
            if table["name"] not in {"runs", "run_role_outputs"}:
                continue
            native.execute(table["create_table_sql"])
            sql = 'INSERT INTO "' + table["name"] + '" VALUES ('
            sql += ",".join("?" for _ in table["column_order"]) + ")"
            for row in table["rows"]:
                native.execute(sql, tuple(map(decode_native, row["cells"])))
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    database = Database("sqlite+pysqlite:///" + path.as_posix())
    for table in (Job.__table__, Saved.__table__, Ack.__table__):
        table.create(database.engine)
    preparer = FrozenPublicationPreparer(trusted_root=DEV)
    archive = BundleArchive(tmp_path / "archive", max_bundle_bytes=2 * 1024 * 1024,
                            max_material_bytes=2 * 1024 * 1024)
    lifecycle = PublicationBundleLifecycle(database, archive, preparer=preparer, clock=lambda: NOW)
    with database.session() as session:
        run = session.get(RunRecord, quality.run_id)
        event_id, artifact_id = run.event_id, run.artifact_id
    source = Path(quality_module.__file__).resolve()
    relative, raw = source.relative_to(DEV).as_posix(), source.read_bytes()
    material = {"sources": [{"relative_ref": relative, "sha256": sha(raw), "bytes": len(raw),
                              "base64": base64.b64encode(raw).decode()}]}
    material_path = tmp_path / "boundary-only-material.json"
    material_path.write_text(json.dumps(material), encoding="utf-8")
    original = OriginalPublicationRead(quality.model_dump_json(), event_id, artifact_id, sha(raw))
    bundle = frozen_bundle_from_verified_read(original, original,
        material_ref="materials/" + sha(material_path.read_bytes()) + ".json",
        material_sha256=sha(material_path.read_bytes()), material_bytes=material_path.stat().st_size)
    prepared = PreparedFrozenPublication("private-boundary-test-carrier", bundle)
    # This private seam tests the lifecycle only; it is not R2 qualification.
    preparer._issued[prepared.nonce] = (prepared, material_path, bundle.material_sha256, material)
    claim = lifecycle.enqueue(bundle.run_id, owner="internal-private-worker",
                              request_id="private-boundary-request", lease_seconds=60)
    yield database, lifecycle, preparer, claim, prepared
    lifecycle.close()
    database.engine.dispose()


def test_original_report_bytes_unknown_and_usage_preserved(boundary):
    database, lifecycle, _, claim, prepared = boundary
    lifecycle.save_verified(claim, prepared)
    stored = lifecycle.read(claim.run_id)
    assert stored == prepared.value
    assert sha(stored.report()) == "f3c1629fd171f4b21e03afcfb6829515325d91cb4c051dfdb1644bff6211c523"
    assert len(stored.report()) == 308477
    before = quality_module.ResearchQualityViewV1.model_validate_json(prepared.value.quality_json)
    after = stored.quality(read_at=NOW)
    assert after.as_of == NOW and after.published_at == before.published_at
    assert after.decision == before.decision and after.usage == before.usage
    assert after.unknown_receipt_codes == before.unknown_receipt_codes
    with database.session() as session:
        ack, job = session.get(Ack, claim.run_id), session.get(Job, claim.run_id)
        assert ack.confirmed_at.replace(tzinfo=UTC) == NOW
        assert job.owner is None and job.lease_expires_at is None
    assert not lifecycle.archive.fenced(claim.run_id)


def test_foreign_preparation_cannot_be_saved(boundary):
    _, lifecycle, _, claim, prepared = boundary
    forged = PreparedFrozenPublication(prepared.nonce, prepared.value)
    with pytest.raises(ValueError, match="issuer_changed"):
        lifecycle.save_verified(claim, forged)


def test_cancel_first_preserves_terminal_run_and_blocks_save(boundary):
    database, lifecycle, _, claim, prepared = boundary
    lifecycle.cancel_preparation(claim)
    with pytest.raises(PermissionError, match="claim_lost"):
        lifecycle.save_verified(claim, prepared)
    with database.session() as session:
        assert session.get(RunRecord, claim.run_id).status in {"completed", "degraded"}
        assert session.get(Ack, claim.run_id) is None
    with pytest.raises(ValueError, match="not_independently_confirmed"):
        lifecycle.read(claim.run_id)


def test_cancel_between_save_and_confirm_wins_CAS(boundary, monkeypatch):
    database, lifecycle, preparer, claim, prepared = boundary
    verify, calls = preparer.verify, 0
    def cancellation_on_saved_readback(value):
        nonlocal calls
        verify(value)
        calls += 1
        if calls == 3:
            lifecycle.cancel_preparation(claim)
    monkeypatch.setattr(preparer, "verify", cancellation_on_saved_readback)
    with pytest.raises(PermissionError, match="claim_lost"):
        lifecycle.save_verified(claim, prepared)
    with database.session() as session:
        assert session.get(Job, claim.run_id).state == "cancelled"
        assert session.get(Ack, claim.run_id) is None


@pytest.mark.parametrize("phase,number,expected", [
    ("prepared", 1, "saved_prepared_requires_explicit_confirmation_recovery"),
    ("confirmed", 2, "saved_confirmed_requires_explicit_fence_recovery"),
])
def test_unknown_commit_retains_fence_and_never_serves(boundary, phase, number, expected):
    database, lifecycle, _, claim, prepared = boundary
    class CommitAcknowledgementLost(Session):
        commits = 0
        def commit(self):
            super().commit()
            type(self).commits += 1
            if type(self).commits == number:
                raise OSError("private-control-commit-acknowledgement-lost")
    database.factory = sessionmaker(database.engine, class_=CommitAcknowledgementLost,
                                   expire_on_commit=False)
    with pytest.raises(BundleCommitUnconfirmed) as error:
        lifecycle.save_verified(claim, prepared)
    assert error.value.phase == phase
    assert lifecycle.archive.fenced(claim.run_id)
    assert lifecycle.reconciliation_state(claim.run_id) == expected
    with pytest.raises(ValueError, match="not_independently_confirmed"):
        lifecycle.read(claim.run_id)
    with pytest.raises(ValueError, match="reconciliation"):
        lifecycle.save_verified(claim, prepared)
    assert lifecycle.archive.fenced(claim.run_id)


def test_saved_corrupt_bundle_never_falls_back(boundary):
    database, lifecycle, _, claim, prepared = boundary
    lifecycle.save_verified(claim, prepared)
    with database.session() as session:
        reference = session.scalar(select(Saved.bundle_ref).where(Saved.run_id == claim.run_id))
    path = lifecycle.archive._path(reference)
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + b" ")
    with pytest.raises(ValueError, match="saved_bytes_changed"):
        lifecycle.read(claim.run_id)


@pytest.mark.parametrize("failure", ["same_length_tamper", "missing", "hardlink"])
def test_full_external_material_is_required_on_every_serving_read(boundary, tmp_path, failure):
    _, lifecycle, preparer, claim, prepared = boundary
    original_stage = preparer.material_source(prepared)
    lifecycle.save_verified(claim, prepared)
    # Serving uses the archive constituent, independent of producer staging.
    original_stage.unlink()
    assert lifecycle.read(claim.run_id) == prepared.value
    material = lifecycle.archive._path(prepared.value.material_ref)
    if failure == "missing":
        material.unlink()
        expected = FileNotFoundError
    elif failure == "hardlink":
        import os
        os.link(material, tmp_path / "private-material-second-link")
        expected = ValueError
    else:
        raw = material.read_bytes()
        material.write_bytes(b" " + raw[1:])
        expected = ValueError
    with pytest.raises(expected):
        lifecycle.read(claim.run_id)


def test_external_material_bound_rejects_before_any_bundle_commit(boundary):
    database, lifecycle, _, claim, prepared = boundary
    lifecycle.archive.max_material_bytes = prepared.value.material_bytes - 1
    with pytest.raises(ValueError, match="material_bound_exceeded"):
        lifecycle.save_verified(claim, prepared)
    with database.session() as session:
        assert session.get(Job, claim.run_id).state == "queued"
        assert session.scalar(select(Saved).where(Saved.run_id == claim.run_id)) is None
        assert session.get(Ack, claim.run_id) is None


def test_external_material_reference_must_be_exact_server_content_path(boundary):
    from packages.kernel.decision_hub_kernel.ports.research_publication_bundle import FrozenPublicationBundleV1
    _, _, _, _, prepared = boundary
    body = prepared.value.model_dump(mode="json")
    for reference in ("materials/" + "a" * 64 + ".json", "../materials/" + body["material_sha256"] + ".json",
                      "https://external.invalid/material.json"):
        with pytest.raises(ValueError, match="material_ref_invalid"):
            FrozenPublicationBundleV1.model_validate_json(json.dumps({**body, "material_ref": reference}))


def test_nonempty_caller_source_inventory_cannot_manufacture_closed_authority(boundary):
    _, _, preparer, _, prepared = boundary
    captured = preparer._issued[prepared.nonce][3]
    assert captured["sources"]
    with pytest.raises(ValueError, match="full_source_closure_unverified"):
        preparer._require_full_source_closure(captured)


def test_current_publication_quality_binding_cannot_be_replaced_by_bundle(boundary):
    database, lifecycle, _, claim, prepared = boundary
    lifecycle.save_verified(claim, prepared)
    with database.session() as session:
        source = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == claim.run_id,
            RunRoleOutputRecord.role == "research_publication_source"))
        body = json.loads(source.payload_json)
        body["quality_binding"]["source_result_hash"] = "a" * 64
        source.payload_json = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        source.payload_hash = sha(source.payload_json.encode())
        session.get(Job, claim.run_id).publication_hash = source.payload_hash
    with pytest.raises(ValueError, match="quality_binding_changed"):
        lifecycle.read(claim.run_id)


def test_fresh_exit_RO_observes_concurrent_revoke(boundary, monkeypatch):
    database, lifecycle, _, claim, prepared = boundary
    lifecycle.save_verified(claim, prepared)
    confirmed, calls = lifecycle._confirmed, 0
    def revoked_after_entry_snapshot(session, run_id, **kwargs):
        nonlocal calls
        calls += 1
        value = confirmed(session, run_id, **kwargs)
        if calls == 1:
            with database.session() as writer:
                writer.connection().exec_driver_sql("BEGIN IMMEDIATE")
                writer.get(Job, run_id).state = "revoked"
        return value
    monkeypatch.setattr(lifecycle, "_confirmed", revoked_after_entry_snapshot)
    with pytest.raises(ValueError, match="not_independently_confirmed"):
        lifecycle.read(claim.run_id)
    assert calls == 2


def test_only_missing_job_has_compatibility_branch(boundary):
    _, lifecycle, _, _, _ = boundary
    assert lifecycle.read("private-no-bundle-run") is None


@pytest.fixture
def native_material(tmp_path):
    path = tmp_path / "original-private.sqlite3"
    with closing(sqlite3.connect(path)) as native, native:
        native.execute("CREATE TABLE native_rows (id INTEGER PRIMARY KEY, scope TEXT, value BLOB)")
        native.executemany("INSERT INTO native_rows VALUES (?,?,?)",
                           [(i, "same", v) for i, v in enumerate([None, 1, 1.0, "1", b"\x00\xff"], 1)])
    engine = create_engine("sqlite+pysqlite://", poolclass=NullPool,
        creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            session.connection().exec_driver_sql("BEGIN")
            with NativeCollector(session, limits=BOUNDS, trace_slot_owned=True) as collector:
                session.connection().exec_driver_sql("SELECT id FROM native_rows WHERE scope=? ORDER BY id LIMIT ?",
                                                     ("same", 1)).all()
                session.connection().exec_driver_sql("SELECT id FROM native_rows WHERE scope=?", ("missing",)).all()
            yield collector.snapshot()
            session.rollback()
    finally:
        engine.dispose()


def test_material_restores_full_native_columns_types_ranges_and_zero(tmp_path, native_material):
    with authenticated_material_session(tmp_path / "stage", native_material) as session:
        values = session.connection().exec_driver_sql("SELECT * FROM native_rows WHERE scope=? ORDER BY id", ("same",)).all()
        assert [type(value[2]) for value in values] == [type(None), int, float, str, bytes]
        assert session.connection().exec_driver_sql("SELECT * FROM native_rows WHERE scope=?", ("missing",)).all() == []
        assert session.connection().connection.driver_connection.total_changes == 0


def test_material_affinity_changes_reject_instead_of_retyping(tmp_path, native_material):
    original = copy.deepcopy(native_material)
    native_material["rows"][2]["cells"][2] = {"kind": "integer", "value": "1"}
    with pytest.raises(ValueError, match="native_schema_changed"):
        require_original_material_matches(original, native_material)


def test_failed_SQL_cannot_hide_later_direct_native_select(tmp_path):
    path = tmp_path / "private-errors.sqlite3"
    with closing(sqlite3.connect(path)) as native, native:
        native.execute("CREATE TABLE native_rows(id INTEGER PRIMARY KEY)")
    engine = create_engine("sqlite+pysqlite://", poolclass=NullPool,
        creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            session.connection().exec_driver_sql("BEGIN")
            with NativeCollector(session, limits=BOUNDS, trace_slot_owned=True) as collector:
                with pytest.raises(Exception, match="no such column"):
                    session.connection().exec_driver_sql("SELECT missing FROM native_rows WHERE id=?", (1,)).all()
                collector.native.execute("SELECT id FROM native_rows WHERE id=1").fetchall()
            reasons = {item["reason_code"] for item in collector.snapshot()["unresolved"]}
            assert {"r1_native_execution_failed", "r1_direct_native_select_unresolved"} <= reasons
    finally:
        engine.dispose()


@pytest.mark.parametrize("statement,expected", [
    ("SELECT COUNT(*) FROM native_rows WHERE scope=?", 5),
    ("SELECT COUNT(value) AS count_1 FROM native_rows WHERE scope=?", 4),
    ("SELECT count(*) AS count_1 FROM (SELECT id,value FROM native_rows WHERE scope=?) AS anon_1", 5),
])
def test_supported_count_preserves_full_predicate_not_scalar_shape(tmp_path, native_material, statement, expected):
    with authenticated_material_session(tmp_path / "stage", native_material) as session:
        with NativeCollector(session, limits=BOUNDS, trace_slot_owned=True) as collector:
            assert session.connection().exec_driver_sql(statement, ("same",)).scalar_one() == expected
            assert session.connection().exec_driver_sql(statement, ("missing",)).scalar_one() == 0
        captured = collector.snapshot()
        assert captured["observed_queries_status"] == "complete"
        assert len(captured["rows"]) == 5
        assert captured["queries"][0]["range_result"]["row_count"] == 5
        assert captured["queries"][1]["empty_range"] is True
        assert captured["queries"][1]["result"]["row_count"] == 1


def test_count_join_stays_unsupported_and_incomplete(tmp_path, native_material):
    with authenticated_material_session(tmp_path / "stage", native_material) as session:
        with NativeCollector(session, limits=BOUNDS, trace_slot_owned=True) as collector:
            session.connection().exec_driver_sql(
                "SELECT count(*) FROM native_rows a JOIN native_rows b ON a.id=b.id WHERE a.scope=?",
                ("same",)).scalar_one()
        captured = collector.snapshot()
        assert captured["observed_queries_status"] == "incomplete"
        assert any(item["reason_code"] == "r1_relational_shape_unsupported" for item in captured["unresolved"])


def test_actual_current_reader_refuses_unpublished_private_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "private-legacy.sqlite3"
    database = Database("sqlite+pysqlite:///" + path.as_posix())
    for table in (RunRecord.__table__, RunRoleOutputRecord.__table__, RunSpotAdmissionRecord.__table__):
        table.create(database.engine)
    with database.session() as session:
        session.add(RunRecord(run_id="private-legacy", event_id="private-event", status="completed",
                              created_at=NOW, updated_at=NOW, finished_at=NOW))
    engine = create_engine("sqlite+pysqlite://", poolclass=NullPool,
        creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            session.connection().exec_driver_sql("BEGIN")
            with pytest.raises(ValueError, match="original_not_available"):
                read_registered_original(session, "private-legacy", as_of=NOW)
            material = tmp_path / "must-not-be-written.json"
            preparer = FrozenPublicationPreparer(trusted_root=DEV)
            with pytest.raises(ValueError, match="original_not_available"):
                preparer.capture_and_prepare(session, run_id="private-legacy", as_of=NOW,
                    material_path=material,
                    material_stage=tmp_path / "stage", sources=(), limits=BOUNDS,
                    max_material_bytes=1000000)
            assert not material.exists() and preparer._issued == {}
    finally:
        engine.dispose()
        database.engine.dispose()
