"""Actual opt-in Factory/SQLite bundle boundaries; no Host or provider calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import socket
import sqlite3
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import event as sql_event

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DocumentUpload,
    ResearchCompiledPolicyReceiptV1,
    ResearchRequest,
    ResearchRequestPackReceiptV2,
    ResearchSessionRequest,
    ResearchTaskReceiptV2,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_policy_resources import canonical_hash
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    EventWatchRecord,
    ResearchRunScopeRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StudyRecord,
)
from packages.provider_adapters.documents import (
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore
from tests.evolution.test_research_worker import (
    PACK_ROOT,
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.kernel.test_research_policy_compiler import policy_bodies

PACK_ROLE = "research_request_pack"
TASK_ROLE = "research_task_request"
COMPILED_ROLE = "research_compiled_policy"
BUNDLE_ROLES = (PACK_ROLE, TASK_ROLE, COMPILED_ROLE)
CAPABILITIES = ("replay.research", "document.readback", "web.search")


@pytest.fixture(autouse=True)
def forbid_external_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    pair_threads: set[int] = set()

    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("HM04-B1 must not open a network connection")

    def internal_pair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        thread_id = threading.get_ident()
        pair_threads.add(thread_id)
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            pair_threads.remove(thread_id)

    def guarded_connect(connection: socket.socket, address: Any) -> None:
        # Windows asyncio uses the stdlib's authenticated loopback socketpair
        # for its internal waker. This narrow exception is not an HTTP client.
        if (
            threading.get_ident() in pair_threads
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return original_connect(connection, address)
        denied()

    monkeypatch.setattr(socket, "socketpair", internal_pair)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def _prepared_pack(directory: Path) -> tuple[Path, dict[str, Any]]:
    root = directory / "prepared-pack"
    shutil.copytree(PACK_ROOT, root)
    manifest = yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))
    gate, rubric = policy_bodies()
    gate_ref = "gates/prepared-horizons.yaml"
    rubric_ref = "evaluations/prepared-rubric.yaml"
    (root / gate_ref).write_text(yaml.safe_dump(gate), encoding="utf-8")
    (root / rubric_ref).write_text(yaml.safe_dump(rubric), encoding="utf-8")
    role_refs = []
    for old_ref in manifest["role_profile_refs"]:
        profile = yaml.safe_load((root / old_ref).read_text(encoding="utf-8"))
        profile["evaluation_policy_ref"] = f"../{rubric_ref}"
        new_ref = f"profiles/prepared-{Path(old_ref).name}"
        (root / new_ref).write_text(yaml.safe_dump(profile), encoding="utf-8")
        role_refs.append(new_ref)
    manifest["gate_policy_ref"] = gate_ref
    manifest["evaluation_policy_ref"] = rubric_ref
    manifest["role_profile_refs"] = role_refs
    (root / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root, manifest


def _library(database: Database, directory: Path) -> ResearchLibraryService:
    store = DocumentBundleStore(directory / "library-bundles")
    return ResearchLibraryService(
        database,
        store,
        PypdfTextDocumentParser(store),
        FinancialSemanticCorrectionAdapter(store),
        HeuristicAnalystClaimExtractor(),
    )


def _ready_version(service: ResearchLibraryService, title: str) -> str:
    version = service.upload(
        DocumentUpload(
            schema_version="document-upload.v1",
            title=title,
            source_type="private_file",
            market_scopes=["crypto_macro"],
            instrument_scopes=["BTC-USDT-SWAP"],
            privacy_mode="local_only",
            visibility="private",
        ),
        filename="source.txt",
        data=(
            f"{title}: BTC evidence and macro transmission.\n"
            "Source context requires independent factual verification."
        ).encode(),
    )
    asyncio.run(service.process_next())
    parsed = service.get_version(version.version_id)
    assert parsed is not None and parsed.status == "parsed"
    service.request_correction(version.version_id)
    asyncio.run(service.process_next())
    ready = asyncio.run(service.accept_correction(version.version_id))
    assert ready.status == "ready"
    correction = service.get_correction(version.version_id)
    assert correction is not None and correction.accepted_at is not None
    return version.version_id


@dataclass(frozen=True)
class PreparedCase:
    database: Database
    event_id: str
    run_id: str
    pack_root: Path
    manifest: dict[str, Any]
    now: datetime
    library: ResearchLibraryService
    selected_version: str
    alternative_version: str


@pytest.fixture
def prepared_case(tmp_path: Path) -> Generator[PreparedCase, None, None]:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None
            study = session.get(StudyRecord, run.study_id)
            assert study is not None and study.horizon_scope == "30m,24h,72h"
        pack_root, manifest = _prepared_pack(tmp_path)
        library = _library(database, tmp_path)
        selected = _ready_version(library, "First reviewed source")
        alternative = _ready_version(library, "Second reviewed source")
        library.select_context(
            ResearchRequest(
                schema_version="research-request.v1",
                research_family="crypto_macro",
                instrument_scope=["BTC-USDT-SWAP"],
                focus="Original reviewed BTC macro research",
                library_context_mode="selected",
                allow_remote_library_content=True,
                selected_version_ids=[selected],
            ),
            run_id=run_id,
        )
        yield PreparedCase(
            database,
            event_id,
            run_id,
            pack_root,
            manifest,
            datetime.now(UTC),
            library,
            selected,
            alternative,
        )
    finally:
        database.engine.dispose()


def _rows(database: Database, run_id: str) -> dict[str, dict[str, Any]]:
    with database.session() as session:
        return {
            row.role: {
                "output_id": row.output_id,
                "schema_version": row.schema_version,
                "payload_json": row.payload_json,
                "payload_hash": row.payload_hash,
                "created_at": row.created_at.isoformat(),
            }
            for row in session.query(RunRoleOutputRecord).filter_by(run_id=run_id).all()
            if row.role in BUNDLE_ROLES
        }


def _state(case: PreparedCase) -> dict[str, Any]:
    with case.database.session() as session:
        run = session.get(RunRecord, case.run_id)
        scope = session.get(ResearchRunScopeRecord, case.run_id)
        assert run is not None and scope is not None
        return {
            "rows": _rows(case.database, case.run_id),
            "snapshot_id": run.snapshot_id,
            "snapshot_count": session.query(SnapshotRecord).filter_by(run_id=case.run_id).count(),
            "scope_input_receipts_json": scope.input_receipts_json,
        }


def _observe(tmp_path: Path, name: str, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    (tmp_path / f"{name}-observation.json").write_text(encoded, encoding="utf-8")
    print(f"HM04_B1_OBSERVATION {name}: {', '.join(payload)}")


def _payload_hash(encoded: str) -> str:
    return hashlib.sha256(encoded.encode()).hexdigest()


def _factory(case: PreparedCase, **changes: Any) -> ResearchRequestFactory:
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )

    arguments: dict[str, Any] = {
        "pack_root": case.pack_root,
        "clock": lambda: case.now,
        "allowed_capabilities": CAPABILITIES,
        "prepared_policy_refs": PreparedResearchPolicyRefs(
            gate_policy_ref=case.manifest["gate_policy_ref"],
            rubric_policy_ref=case.manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(case.manifest["role_profile_refs"]),
        ),
    }
    arguments.update(changes)
    return ResearchRequestFactory(case.database, **arguments)


def _decoded(
    case: PreparedCase,
) -> tuple[ResearchRequestPackReceiptV2, ResearchTaskReceiptV2, ResearchCompiledPolicyReceiptV1]:
    rows = _rows(case.database, case.run_id)
    assert set(rows) == set(BUNDLE_ROLES)
    for row in rows.values():
        assert row["payload_hash"] == _payload_hash(row["payload_json"])
    pack = ResearchRequestPackReceiptV2.model_validate_json(rows[PACK_ROLE]["payload_json"])
    task = ResearchTaskReceiptV2.model_validate_json(rows[TASK_ROLE]["payload_json"])
    compiled = ResearchCompiledPolicyReceiptV1.model_validate_json(
        rows[COMPILED_ROLE]["payload_json"]
    )
    for role, receipt in zip(BUNDLE_ROLES, (pack, task, compiled), strict=True):
        assert rows[role]["schema_version"] == receipt.schema_version
        assert receipt.run_id == case.run_id
    assert pack.definition_hash == canonical_hash(pack.definition.model_dump(mode="json"))
    request = ResearchSessionRequest.model_validate(task.request.model_dump(mode="json"))
    assert task.request_hash == canonical_hash(request.model_dump(mode="json"))
    assert task.request_hash_kind == "complete_task_request.v1"
    assert task.pack_definition_hash == compiled.pack_definition_hash == pack.definition_hash
    assert task.request_hash == task.scope.full_task_hash == compiled.full_task_hash
    assert task.compiled_policy_hash == compiled.compiled.compiled_hash
    assert compiled.compiled.scope_hash == task.scope.scope_hash
    assert compiled.compiled.requirements_hash == task.scope.requirements_hash
    assert compiled.compiled.allowed_execution is False
    assert compiled.compiled.status == compiled.compiled.checker_activation == "prepared"
    assert task.factory_source.run_id == case.run_id
    assert task.factory_source.event_id == compiled.event_id == case.event_id
    return pack, task, compiled


def test_legal_three_row_bundle_pins_real_snapshot_and_library_inputs(
    prepared_case: PreparedCase, tmp_path: Path
) -> None:
    case = prepared_case
    request = _factory(case).build(case.event_id, case.run_id)
    pack, task, compiled = _decoded(case)
    state = _state(case)
    _observe(
        tmp_path,
        "legal",
        {
            "state": state,
            "request": request.model_dump(mode="json"),
            "factory_source": task.factory_source.model_dump(mode="json"),
        },
    )
    assert type(request) is ResearchSessionRequest
    assert request == ResearchSessionRequest.model_validate(task.request.model_dump(mode="json"))
    assert state["snapshot_count"] == 1
    assert state["snapshot_id"] == request.trigger_snapshot_id
    assert task.factory_source.trigger.snapshot_id == request.trigger_snapshot_id
    assert task.scope.trigger_snapshot_hash == task.factory_source.trigger.snapshot_hash
    assert task.scope.horizon_scope == ["30m", "24h", "72h"]
    assert task.factory_source.library_scope is not None
    assert task.factory_source.library_scope.selected_version_ids == [case.selected_version]
    assert task.factory_source.library_scope.allow_remote_library_content is True
    receipts = [item.model_dump(mode="json") for item in task.library_inputs]
    assert receipts and json.loads(state["scope_input_receipts_json"]) == receipts
    assert {item["version_id"] for item in receipts} == {case.selected_version}
    by_id = {item.evidence_id: item for item in request.input_evidence}
    for item in receipts:
        assert item["excerpt"] == by_id[item["evidence_id"]].excerpt
        assert item["excerpt_hash"] == hashlib.sha256(item["excerpt"].encode()).hexdigest()
        version = case.library.get_version(item["version_id"])
        assert version is not None
        assert item["original_file_hash"] == version.original_file_hash
        assert (
            item["original_file_hash"]
            == hashlib.sha256(case.library.store.read(version.original_file_ref)).hexdigest()
        )
        assert item["locator_refs"]
    assert (
        compiled.compiled.gate_resource.raw_sha256
        == pack.definition.gate_policy_resource.raw_sha256
    )


class ControlledBundleFault(RuntimeError):
    """Injected only after a real native in-session producer has written."""


@pytest.mark.parametrize("boundary", ["snapshot", TASK_ROLE, COMPILED_ROLE])
def test_snapshot_task_and_compiled_failure_roll_back_every_native_part(
    prepared_case: PreparedCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from packages.kernel.decision_hub_kernel.application import research_binding as binding

    case = prepared_case
    before = _state(case)
    reached: list[str] = []
    if boundary == "snapshot":
        original = SnapshotService.freeze_for_run_in_session

        def fail_snapshot(service: SnapshotService, session: Any, run: str, event: str) -> Any:
            result = original(service, session, run, event)
            session.flush()
            reached.append(result[0])
            raise ControlledBundleFault(f"actual_{boundary}_written_then_failed")

        monkeypatch.setattr(SnapshotService, "freeze_for_run_in_session", fail_snapshot)
    else:
        # Deliberate fault seam: run the real insert/flush before raising.
        insert = binding._insert_receipt_in_session  # pyright: ignore[reportPrivateUsage]

        def fail_insert(session: Any, role: str, receipt: Any, recorded_at: datetime) -> Any:
            result = insert(session, role, receipt, recorded_at)
            if role == boundary:
                reached.append(result.output_id)
                raise ControlledBundleFault(f"actual_{boundary}_written_then_failed")
            return result

        monkeypatch.setattr(binding, "_insert_receipt_in_session", fail_insert)
    with pytest.raises(ControlledBundleFault) as error:
        _factory(case).build(case.event_id, case.run_id)
    after = _state(case)
    _observe(
        tmp_path,
        f"rollback-{boundary}",
        {
            "boundary": boundary,
            "reached": reached,
            "exception": str(error.value),
            "before": before,
            "after": after,
        },
    )
    assert reached
    assert after == before
    assert after["rows"] == {}
    assert after["snapshot_count"] == 0
    assert after["scope_input_receipts_json"] == "[]"


def test_same_bundle_replay_keeps_original_time_and_every_raw_byte(
    prepared_case: PreparedCase, tmp_path: Path
) -> None:
    from packages.kernel.decision_hub_kernel.application import research_binding as binding

    case = prepared_case
    original = _factory(case).build(case.event_id, case.run_id)
    receipts = _decoded(case)
    before = _state(case)
    later_case = replace(case, now=case.now + timedelta(hours=1))
    assert _factory(later_case).build(case.event_id, case.run_id) == original
    with case.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        replayed = binding.freeze_research_bundle_in_session(
            session, case.run_id, *receipts, later_case.now
        )
    assert replayed.request == original
    assert replayed.recorded_at == case.now
    after = _state(case)
    _observe(
        tmp_path,
        "same-replay",
        {
            "before": before,
            "after": after,
            "returned_at": replayed.recorded_at.isoformat(),
        },
    )
    assert after == before


def _clear_bundle(database: Database, run_id: str) -> None:
    """Controlled fixture only: remove candidate rows without changing native inputs."""
    with database.session() as session:
        session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role.in_(BUNDLE_ROLES),
        ).delete(synchronize_session=False)


def _clone_native_database(case: PreparedCase, path: Path) -> Database:
    original = case.database.sqlite_path
    assert original is not None
    # SQLite backup includes committed WAL data and preserves native source times.
    with sqlite3.connect(original) as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    return Database(f"sqlite+pysqlite:///{path.as_posix()}")


def _alternative_candidate(
    case: PreparedCase, directory: Path
) -> tuple[ResearchRequestPackReceiptV2, ResearchTaskReceiptV2, ResearchCompiledPolicyReceiptV1]:
    root = directory / "alternative-pack"
    shutil.copytree(case.pack_root, root)
    gate_path = root / case.manifest["gate_policy_ref"]
    gate = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
    gate["policy_id"] += ".controlled-alternative"
    gate_path.write_text(yaml.safe_dump(gate), encoding="utf-8")
    clone = _clone_native_database(case, directory / "alternative.sqlite3")
    try:
        _clear_bundle(clone, case.run_id)
        alternative = replace(
            case, database=clone, pack_root=root, now=case.now + timedelta(seconds=1)
        )
        _factory(alternative).build(case.event_id, case.run_id)
        result = _decoded(alternative)
        with clone.session() as session:
            from packages.kernel.decision_hub_kernel.application import research_binding as binding

            assert (
                binding.read_factory_source_in_session(session, case.run_id)
                == result[1].factory_source
            )
        return result
    finally:
        clone.engine.dispose()


@pytest.mark.parametrize("same_content", [True, False], ids=["same", "different"])
def test_two_native_connections_compare_or_insert_one_complete_winner(
    prepared_case: PreparedCase, tmp_path: Path, same_content: bool
) -> None:
    from packages.kernel.decision_hub_kernel.application import research_binding as binding

    case = prepared_case
    _factory(case).build(case.event_id, case.run_id)
    candidate_a = _decoded(case)
    candidate_b = candidate_a if same_content else _alternative_candidate(case, tmp_path)
    if not same_content:
        assert candidate_a[0].definition_hash != candidate_b[0].definition_hash
        assert candidate_a[1].request_hash != candidate_b[1].request_hash
        assert candidate_a[1].factory_source == candidate_b[1].factory_source
    _clear_bundle(case.database, case.run_id)
    original_path = case.database.sqlite_path
    assert original_path is not None
    second = Database(f"sqlite+pysqlite:///{original_path.as_posix()}")
    barrier = threading.Barrier(2)

    def contend(
        database: Database,
        receipts: tuple[
            ResearchRequestPackReceiptV2, ResearchTaskReceiptV2, ResearchCompiledPolicyReceiptV1
        ],
        offset: int,
    ) -> dict[str, Any]:
        barrier.wait(timeout=10)
        try:
            with database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                value = binding.freeze_research_bundle_in_session(
                    session, case.run_id, *receipts, case.now + timedelta(seconds=offset)
                )
            return {
                "status": "accepted",
                "pack_hash": value.pack.definition_hash,
                "request_hash": value.task.request_hash,
                "recorded_at": value.recorded_at.isoformat(),
            }
        except binding.ResearchBindingError as error:
            return {"status": "rejected", "error_code": error.error_code}

    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hm04-b1-controlled") as pool:
            first = pool.submit(contend, case.database, candidate_a, 0)
            other = pool.submit(contend, second, candidate_b, 1)
            outcomes = [first.result(timeout=20), other.result(timeout=20)]
        final = _decoded(case)
        state = _state(case)
        _observe(
            tmp_path,
            f"concurrent-{'same' if same_content else 'different'}",
            {
                "outcomes": outcomes,
                "state": state,
                "candidate_a_request_hash": candidate_a[1].request_hash,
                "candidate_b_request_hash": candidate_b[1].request_hash,
            },
        )
        accepted = [item for item in outcomes if item["status"] == "accepted"]
        rejected = [item for item in outcomes if item["status"] == "rejected"]
        assert len(accepted) == (2 if same_content else 1)
        assert rejected == (
            []
            if same_content
            else [
                {
                    "status": "rejected",
                    "error_code": "research_bundle_changed",
                }
            ]
        )
        assert final in (candidate_a, candidate_b)
        assert final[0].definition_hash == accepted[0]["pack_hash"]
        assert final[1].request_hash == accepted[0]["request_hash"]
        if same_content:
            assert accepted[0] == accepted[1]
        assert len({row["created_at"] for row in state["rows"].values()}) == 1
    finally:
        second.engine.dispose()


@pytest.mark.parametrize("source_kind", ["event", "watch", "unknown", "none"])
def test_first_factory_source_is_not_replaced_by_current_event_or_watch(
    prepared_case: PreparedCase, tmp_path: Path, source_kind: str
) -> None:
    case = prepared_case
    if source_kind == "watch":
        EventWatchService(case.database, clock=lambda: case.now).ensure_watch(
            event_id=case.event_id,
            source_id="controlled-calendar",
            event_family="inflation_release",
            scheduled_at=case.now + timedelta(hours=1),
        )
    with case.database.session() as session:
        event = session.get(EventRecord, case.event_id)
        assert event is not None
        original_event = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "received_at": event.received_at,
            "generation": event.generation,
            "status": event.status,
        }
        if source_kind == "unknown":
            event.event_type = "unrecognized_original_family"
        elif source_kind == "none":
            session.delete(event)
    original_request = _factory(case).build(case.event_id, case.run_id)
    _, original_task, _ = _decoded(case)
    before = _state(case)
    source = original_task.factory_source
    assert source.selected_from == (source_kind if source_kind in {"watch", "none"} else "event")
    if source_kind == "none":
        assert source.event is source.watch is None
        assert original_task.scope.event_family_basis == "unknown_conservative_default"
    if source_kind == "unknown":
        assert source.event is not None
        assert (
            source.event.event_type
            == original_task.scope.event_family
            == "unrecognized_original_family"
        )
    if source_kind == "watch":
        assert source.watch is not None
        assert original_task.scope.event_family == source.watch.event_family == "inflation_release"
    with case.database.session() as session:
        event = session.get(EventRecord, case.event_id)
        if event is None:
            event = EventRecord(**original_event)
            session.add(event)
        event.event_type = "crypto_market_snapshot"
        event.generation += 1
        for watch in session.query(EventWatchRecord).filter_by(event_id=case.event_id).all():
            watch.event_family = "labor_release"
    replayed = _factory(replace(case, now=case.now + timedelta(hours=2))).build(
        case.event_id, case.run_id
    )
    _, replayed_task, _ = _decoded(case)
    after = _state(case)
    _observe(
        tmp_path,
        f"source-{source_kind}",
        {
            "original_source": source.model_dump(mode="json"),
            "replayed_source": replayed_task.factory_source.model_dump(mode="json"),
            "before": before,
            "after": after,
        },
    )
    assert replayed == original_request
    assert replayed_task.factory_source == source
    assert after == before


@pytest.mark.parametrize(
    "drift",
    [
        "study-horizons",
        "study-question",
        "capability",
        "library-version",
        "library-consent",
        "library-inputs",
    ],
)
def test_current_scope_or_permission_drift_rejects_without_rewriting_original_bundle(
    prepared_case: PreparedCase, tmp_path: Path, drift: str
) -> None:
    case = prepared_case
    _factory(case).build(case.event_id, case.run_id)
    original_rows = _rows(case.database, case.run_id)
    if drift != "capability":
        with case.database.session() as session:
            run = session.get(RunRecord, case.run_id)
            scope = session.get(ResearchRunScopeRecord, case.run_id)
            assert run is not None and scope is not None
            study = session.get(StudyRecord, run.study_id)
            assert study is not None
            if drift == "study-horizons":
                study.horizon_scope = "30m"
            elif drift == "study-question":
                study.question_key += ".changed"
            elif drift == "library-version":
                scope.selected_version_ids_json = json.dumps([case.alternative_version])
            elif drift == "library-consent":
                scope.allow_remote_library_content = False
            else:
                scope.input_receipts_json = "[]"
    changes = {"allowed_capabilities": CAPABILITIES[:2]} if drift == "capability" else {}
    with pytest.raises(ValueError) as error:
        _factory(case, **changes).build(case.event_id, case.run_id)
    expected = (
        "research_pack_capability_revoked"
        if drift == "capability"
        else "research_bundle_study_scope_changed"
        if drift.startswith("study-")
        else "research_bundle_library_inputs_changed"
        if drift == "library-inputs"
        else "research_bundle_library_scope_changed"
    )
    _observe(
        tmp_path,
        f"drift-{drift}",
        {
            "error_code": str(error.value),
            "before_rows": original_rows,
            "after_rows": _rows(case.database, case.run_id),
        },
    )
    assert str(error.value) == expected
    assert _rows(case.database, case.run_id) == original_rows


@pytest.mark.parametrize("missing_role", BUNDLE_ROLES)
def test_partial_v2_is_rejected_by_factory_and_both_public_readers(
    prepared_case: PreparedCase, tmp_path: Path, missing_role: str
) -> None:
    case = prepared_case
    _factory(case).build(case.event_id, case.run_id)
    with case.database.session() as session:
        session.query(RunRoleOutputRecord).filter_by(run_id=case.run_id, role=missing_role).delete(
            synchronize_session=False
        )
    partial = _rows(case.database, case.run_id)
    assert len(partial) == 2
    errors = []
    for reader in (
        lambda: _factory(case).build(case.event_id, case.run_id),
        lambda: ResearchPackService(case.database).get(case.run_id),
        lambda: ResearchTaskService(case.database).get(case.run_id),
    ):
        with pytest.raises(ValueError) as error:
            reader()
        errors.append(str(error.value))
    _observe(
        tmp_path,
        f"partial-{missing_role}",
        {
            "errors": errors,
            "partial_rows": partial,
            "after_rows": _rows(case.database, case.run_id),
        },
    )
    assert errors == [
        "research_bundle_partial_or_mixed",
        "research_bundle_partial_or_mixed",
        "research_task_request_invalid",
    ]
    assert _rows(case.database, case.run_id) == partial


def test_actual_v1_pack_mixed_with_v2_task_and_compiled_is_not_repaired(
    prepared_case: PreparedCase, tmp_path: Path
) -> None:
    case = prepared_case
    _factory(case).build(case.event_id, case.run_id)
    clone = _clone_native_database(case, tmp_path / "legacy.sqlite3")
    try:
        _clear_bundle(clone, case.run_id)
        ResearchRequestFactory(
            clone,
            pack_root=PACK_ROOT,
            allowed_capabilities=CAPABILITIES,
            clock=lambda: case.now,
        ).build(case.event_id, case.run_id)
        old = _rows(clone, case.run_id)[PACK_ROLE]
        assert old["schema_version"] == "research-request-pack.v1"
        with case.database.session() as session:
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=case.run_id, role=PACK_ROLE)
                .one()
            )
            row.schema_version = old["schema_version"]
            row.payload_json = old["payload_json"]
            row.payload_hash = old["payload_hash"]
            row.created_at = datetime.fromisoformat(old["created_at"])
        mixed = _rows(case.database, case.run_id)
        with pytest.raises(ValueError, match="^research_bundle_partial_or_mixed$") as error:
            _factory(case).build(case.event_id, case.run_id)
        _observe(
            tmp_path,
            "mixed-native-versions",
            {
                "error_code": str(error.value),
                "mixed_rows": mixed,
                "after_rows": _rows(case.database, case.run_id),
            },
        )
        assert _rows(case.database, case.run_id) == mixed
    finally:
        clone.engine.dispose()


@pytest.mark.parametrize("freeze_task", [False, True], ids=["pack-only", "complete-v1-task"])
def test_existing_actual_v1_receipts_are_never_upgraded_in_place(
    prepared_case: PreparedCase, tmp_path: Path, freeze_task: bool
) -> None:
    case = prepared_case
    original = ResearchRequestFactory(
        case.database,
        pack_root=PACK_ROOT,
        allowed_capabilities=CAPABILITIES,
        clock=lambda: case.now,
    ).build(case.event_id, case.run_id)
    if freeze_task:
        ResearchTaskService(case.database, clock=lambda: case.now).freeze(original)
    before = _state(case)
    returned = _factory(replace(case, now=case.now + timedelta(minutes=1))).build(
        case.event_id, case.run_id
    )
    after = _state(case)
    _observe(
        tmp_path,
        f"legacy-{'task' if freeze_task else 'pack-only'}",
        {
            "before": before,
            "after": after,
            "returned_request": returned.model_dump(mode="json"),
        },
    )
    assert after == before
    assert set(after["rows"]) == ({PACK_ROLE, TASK_ROLE} if freeze_task else {PACK_ROLE})
    assert all(row["schema_version"].endswith(".v1") for row in after["rows"].values())
    if freeze_task:
        assert returned == original


@pytest.mark.parametrize("wrong_ref", ["gate", "rubric", "roles"])
def test_explicit_opt_in_refs_must_match_manifest_before_any_sql(
    prepared_case: PreparedCase, tmp_path: Path, wrong_ref: str
) -> None:
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )

    case = prepared_case
    refs = PreparedResearchPolicyRefs(
        gate_policy_ref=case.manifest["gate_policy_ref"],
        rubric_policy_ref=case.manifest["evaluation_policy_ref"],
        role_profile_refs=tuple(case.manifest["role_profile_refs"]),
    )
    refs = replace(
        refs,
        **(
            {"gate_policy_ref": "gates/other.yaml"}
            if wrong_ref == "gate"
            else {"rubric_policy_ref": "evaluations/other.yaml"}
            if wrong_ref == "rubric"
            else {"role_profile_refs": tuple(reversed(refs.role_profile_refs))}
        ),
    )
    before = _state(case)
    sql: list[str] = []

    def record_sql(_connection: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        sql.append(statement)

    sql_event.listen(case.database.engine, "before_cursor_execute", record_sql)
    try:
        with pytest.raises(ValueError, match="^research_bundle_opt_in_manifest_mismatch$"):
            _factory(case, prepared_policy_refs=refs)
    finally:
        sql_event.remove(case.database.engine, "before_cursor_execute", record_sql)
    after = _state(case)
    _observe(
        tmp_path,
        f"manifest-{wrong_ref}",
        {
            "sql_statements": sql,
            "before": before,
            "after": after,
        },
    )
    assert sql == []
    assert after == before


def _snapshot_body(case: PreparedCase) -> dict[str, Any]:
    with case.database.session() as session:
        run = session.get(RunRecord, case.run_id)
        assert run is not None
        snapshot = session.get(SnapshotRecord, run.snapshot_id)
        assert snapshot is not None
        return {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "cutoff_at": snapshot.cutoff_at.isoformat(),
            "evidence_json": snapshot.evidence_json,
            "run_id": snapshot.run_id,
            "event_id": snapshot.event_id,
        }


@pytest.mark.parametrize("damage", ["none", "later-cutoff", "rebound-id", "semantic-evidence-id"])
def test_first_snapshot_native_recipe_is_verified_before_bundle_write(
    prepared_case: PreparedCase, tmp_path: Path, damage: str
) -> None:
    case = prepared_case
    SnapshotService(case.database, clock=lambda: case.now).freeze_for_run(
        case.run_id, case.event_id
    )
    original = _snapshot_body(case)
    assert _rows(case.database, case.run_id) == {}
    if damage != "none":
        with case.database.session() as session:
            run = session.get(RunRecord, case.run_id)
            assert run is not None
            snapshot = session.get(SnapshotRecord, run.snapshot_id)
            assert snapshot is not None
            if damage == "later-cutoff":
                snapshot.cutoff_at += timedelta(seconds=1)
            elif damage == "rebound-id":
                snapshot.snapshot_id = (
                    "snap_" + hashlib.sha256(b"controlled-wrong-id").hexdigest()[:32]
                )
                run.snapshot_id = snapshot.snapshot_id
            else:
                evidence = json.loads(snapshot.evidence_json)
                evidence[0]["evidence_id"] = "ev_" + "a" * 32
                snapshot.evidence_json = json.dumps(evidence, ensure_ascii=False)
                snapshot.snapshot_hash = hashlib.sha256(
                    json.dumps(evidence, sort_keys=True).encode()
                ).hexdigest()
                snapshot.snapshot_id = (
                    "snap_"
                    + hashlib.sha256(
                        f"{case.run_id}:{snapshot.snapshot_hash}".encode()
                    ).hexdigest()[:32]
                )
                run.snapshot_id = snapshot.snapshot_id
    before = _state(case)
    presented_snapshot = _snapshot_body(case)
    error_code = None
    try:
        _factory(case).build(case.event_id, case.run_id)
    except ValueError as error:
        error_code = str(error)
    after = _state(case)
    _observe(
        tmp_path,
        f"trigger-native-{damage}",
        {
            "damage": damage,
            "error_code": error_code,
            "original_snapshot": original,
            "presented_snapshot": presented_snapshot,
            "before": before,
            "after": after,
        },
    )
    assert _snapshot_body(case) == presented_snapshot
    if damage == "none":
        assert error_code is None
        _decoded(case)
        assert set(after["rows"]) == set(BUNDLE_ROLES)
    else:
        assert error_code == "research_bundle_trigger_invalid"
        assert after == before


@pytest.mark.parametrize(
    "invalid_file",
    [
        "oversize-manifest",
        "duplicate-manifest-key",
        "bool-budget",
        "string-budget",
    ],
)
def test_prepared_constructor_rejects_actual_manifest_boundaries_before_sql(
    prepared_case: PreparedCase, tmp_path: Path, invalid_file: str
) -> None:
    case = prepared_case
    manifest_path = case.pack_root / "pack.yaml"
    original = manifest_path.read_bytes()
    if invalid_file == "oversize-manifest":
        manifest_path.write_bytes(original + b"\n#" + b"x" * 65_536)
    elif invalid_file == "duplicate-manifest-key":
        manifest_path.write_bytes(original + b"\npack_id: crypto_macro\n")
    else:
        manifest = yaml.safe_load(original.decode("utf-8"))
        manifest["execution_budget"]["max_tool_calls"] = (
            True if invalid_file == "bool-budget" else "100"
        )
        manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    presented = manifest_path.read_bytes()
    before = _state(case)
    sql: list[str] = []
    error_code = None

    def record_sql(_connection: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        sql.append(statement)

    sql_event.listen(case.database.engine, "before_cursor_execute", record_sql)
    try:
        try:
            _factory(case)
        except ValueError as error:
            error_code = str(error)
    finally:
        sql_event.remove(case.database.engine, "before_cursor_execute", record_sql)
    after = _state(case)
    _observe(
        tmp_path,
        f"manifest-boundary-{invalid_file}",
        {
            "invalid_file": invalid_file,
            "error_code": error_code,
            "sql_statements": sql,
            "presented_size_bytes": len(presented),
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "presented_sha256": hashlib.sha256(presented).hexdigest(),
            "before": before,
            "after": after,
        },
    )
    assert error_code is not None
    assert sql == []
    assert after == before
