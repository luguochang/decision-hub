"""Policy receipt storage uses real SQLite; no policy execution is claimed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyCheckReceipt,
)
from packages.kernel.decision_hub_kernel.application.research_method import recipe_hash
from packages.kernel.decision_hub_kernel.application.research_policy_receipts import (
    ResearchPolicyReceiptError,
    policy_receipt_id,
    policy_receipt_identity_hash,
    policy_receipt_role,
    read_policy_check_receipt,
    store_policy_check_receipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    OutboxRecord,
    RunRecord,
    RunRoleOutputRecord,
)

NOW = datetime(2026, 10, 2, 1, tzinfo=UTC)
RUN = "run-policy-storage"
EVENT = "event-policy-storage"
DIGEST = hashlib.sha256(b"controlled storage fixture, no policy execution").hexdigest()


@pytest.fixture
def database(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'receipts.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(RunRecord(
            run_id=RUN, event_id=EVENT, created_at=NOW, updated_at=NOW,
        ))
    try:
        yield db
    finally:
        db.engine.dispose()


def receipt_body() -> dict[str, Any]:
    binding = {
        "schema_version": "research-policy-method-binding.v1",
        "run_id": RUN, "event_id": EVENT,
        "full_task_hash": DIGEST, "scope_hash": DIGEST, "requirements_hash": DIGEST,
        "request_pack_hash": DIGEST, "first_execution_request_hash": DIGEST,
        "first_execution_hash_kind": "actual_first_execution_request",
        "gate_policy_hash": DIGEST, "rubric_hash": DIGEST, "compiled_policy_hash": DIGEST,
        "compiler_version": "controlled-prepared-compiler", "compiler_code_hash": DIGEST,
        "checker_version": "controlled-storage-only", "checker_code_hash": DIGEST,
        "checker_activation": "prepared",
    }
    binding["binding_hash"] = recipe_hash(binding)
    return {
        "schema_version": "research-policy-check.v1",
        "receipt_id": "policy_check_" + DIGEST, "full_identity_hash": DIGEST,
        "method_hash": DIGEST, "run_id": RUN, "event_id": EVENT, "phase": "commit",
        "binding": binding, "trigger_snapshot_id": "snapshot-controlled",
        "trigger_snapshot_hash": DIGEST, "decision_snapshot_id": None,
        "decision_snapshot_hash": None, "reference_at": NOW,
        "result_content_hash": None, "result_payload_hash": None, "input_set_hash": DIGEST,
        "checks": [{
            "rule_id": "citation-precision-review", "rule_version": "1.0.0",
            "status": "not_available", "applicability": "applicable",
            "blocking_for": ["research_publication"], "reason_code": "missing_native_proof",
            "input_hash": DIGEST, "evidence_refs": [], "reference_at": NOW,
            "observed_at": NOW,
        }],
        "all_applicable_blocking_rules_passed": False, "assessed_at": NOW,
        "assessment_state": "executed", "checker_version": "controlled-storage-only",
        "checker_code_hash": DIGEST,
    }


def receipt() -> ResearchPolicyCheckReceipt:
    body = receipt_body()
    identity = policy_receipt_identity_hash(ResearchPolicyCheckReceipt.model_validate(body))
    body["full_identity_hash"], body["receipt_id"] = identity, policy_receipt_id(identity)
    return ResearchPolicyCheckReceipt.model_validate(body)


def write(session: Session, value: ResearchPolicyCheckReceipt) -> ResearchPolicyCheckReceipt:
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    return store_policy_check_receipt(session, value)


def test_same_identity_and_content_returns_original_without_rewriting(database: Database) -> None:
    original = receipt()
    with database.session() as session:
        saved = write(session, original)
    with database.session() as session:
        before = session.get(RunRoleOutputRecord, saved.receipt_id)
        assert before is not None
        before_row = (before.payload_json, before.payload_hash, before.created_at)
    with database.session() as session:
        assert write(session, original) == saved
    with database.session() as session:
        after = session.get(RunRoleOutputRecord, saved.receipt_id)
        assert after is not None
        assert (after.payload_json, after.payload_hash, after.created_at) == before_row
        assert session.query(RunRoleOutputRecord).count() == 1
        assert read_policy_check_receipt(session, saved.receipt_id) == saved
    assert saved.binding.checker_activation == "prepared"
    assert not saved.all_applicable_blocking_rules_passed


def test_different_check_content_is_rejected_and_original_is_preserved(database: Database) -> None:
    original = receipt()
    with database.session() as session:
        saved = write(session, original)
    changed = receipt()
    changed.checks[0].reason_code = "different_check_result"
    with pytest.raises(ResearchPolicyReceiptError, match="policy_receipt_content_conflict"):
        with database.session() as session:
            write(session, changed)
    with database.session() as session:
        assert read_policy_check_receipt(session, saved.receipt_id) == saved
        assert session.query(RunRoleOutputRecord).count() == 1


@pytest.mark.parametrize("damage", ["hash", "schema", "role", "time", "resigned_identity"])
def test_saved_corruption_cannot_be_reused(database: Database, damage: str) -> None:
    from datetime import timedelta

    original = receipt()
    with database.session() as session:
        write(session, original)
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, original.receipt_id)
        assert row is not None
        if damage == "hash":
            row.payload_hash = "0" * 64
        elif damage == "schema":
            row.schema_version = "research-policy-check.invalid"
        elif damage == "role":
            row.role = "policy_check:wrong-slot"
        elif damage == "time":
            row.created_at = NOW + timedelta(seconds=1)
        else:
            body = json.loads(row.payload_json)
            body["input_set_hash"] = "1" * 64
            row.payload_json = json.dumps(body, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"))
            row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    with pytest.raises(ResearchPolicyReceiptError, match="policy_receipt_saved_invalid"):
        with database.session() as session:
            read_policy_check_receipt(session, original.receipt_id)


def test_an_occupied_short_digest_slot_is_rejected(database: Database) -> None:
    original = receipt()
    with database.session() as session:
        session.add(RunRoleOutputRecord(
            output_id="foreign-policy-output", run_id=RUN,
            role=policy_receipt_role(original.phase, original.full_identity_hash),
            schema_version="foreign-schema", payload_json="{}", payload_hash=DIGEST,
            created_at=NOW,
        ))
    with pytest.raises(ResearchPolicyReceiptError, match="policy_receipt_saved_invalid"):
        with database.session() as session:
            write(session, original)
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, "foreign-policy-output")
        assert row is not None and row.payload_json == "{}"
        assert session.query(RunRoleOutputRecord).count() == 1


def test_occupied_full_output_id_is_rejected(database: Database) -> None:
    original = receipt()
    with database.session() as session:
        session.add(RunRoleOutputRecord(
            output_id=original.receipt_id, run_id="different-run", role="foreign-role",
            schema_version="foreign-schema", payload_json="{}", payload_hash=DIGEST,
            created_at=NOW,
        ))
    with pytest.raises(ResearchPolicyReceiptError, match="policy_receipt_output_id_collision"):
        with database.session() as session:
            write(session, original)


def test_storage_cannot_start_its_own_write_transaction(database: Database) -> None:
    with pytest.raises(ResearchPolicyReceiptError, match="caller_write_transaction_required"):
        with database.session() as session:
            session.get(RunRecord, RUN)  # SQLAlchemy autobegin is still not a SQLite write BEGIN.
            store_policy_check_receipt(session, receipt())
    with database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0


def test_receipt_artifact_and_outbox_rollback_in_the_same_session(database: Database) -> None:
    original = receipt()
    with pytest.raises(ValueError, match="controlled_publication_failure"):
        with database.session() as session:
            write(session, original)
            session.add(ArtifactRecord(
                artifact_id="storage-transaction-artifact", run_id=RUN, event_id=EVENT,
                gate_status="blocked", headline="Controlled transaction fixture",
                summary="No product publication or execution", payload_json="{}", created_at=NOW,
            ))
            session.add(OutboxRecord(
                artifact_id="storage-transaction-artifact", channel="controlled-storage-only",
                dedupe_key="storage-transaction-outbox",
            ))
            session.flush()
            raise ValueError("controlled_publication_failure")
    with database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0
        assert session.query(ArtifactRecord).count() == 0
        assert session.query(OutboxRecord).count() == 0


def test_store_does_not_commit_the_callers_transaction(database: Database) -> None:
    with database.session() as session:
        saved = write(session, receipt())
        with database.session() as reader:
            assert reader.get(RunRoleOutputRecord, saved.receipt_id) is None
    with database.session() as reader:
        assert reader.get(RunRoleOutputRecord, saved.receipt_id) is not None


@pytest.mark.parametrize("damage,code", [
    ("wrong_event", "binding_invalid"),
    ("binding_resigned_wrong_run", "run_identity_mismatch"),
    ("changed_input_set", "identity_invalid"),
    ("duplicate_rule", "duplicate_rule"),
    ("claimed_pass_with_unavailable", "claimed_pass_inconsistent"),
    ("checker_identity_changed", "binding_invalid"),
])
def test_invalid_declarations_cannot_be_inserted(
    database: Database, damage: str, code: str,
) -> None:
    bad = receipt()
    if damage == "wrong_event":
        bad.event_id = "another-event"
    elif damage == "binding_resigned_wrong_run":
        bad.event_id = "another-event"
        bad.binding.event_id = bad.event_id
        bad.binding.binding_hash = recipe_hash(
            bad.binding.model_dump(mode="json", exclude={"binding_hash"}),
        )
        bad.full_identity_hash = policy_receipt_identity_hash(bad)
        bad.receipt_id = policy_receipt_id(bad.full_identity_hash)
    elif damage == "changed_input_set":
        bad.input_set_hash = "1" * 64
    elif damage == "duplicate_rule":
        duplicate = bad.checks[0].model_copy(deep=True)
        duplicate.reason_code = "different_outcome_for_same_rule"
        bad.checks.append(duplicate)
    elif damage == "claimed_pass_with_unavailable":
        bad.all_applicable_blocking_rules_passed = True
    else:
        bad.checker_code_hash = "1" * 64
    with pytest.raises(ResearchPolicyReceiptError, match=code):
        with database.session() as session:
            write(session, bad)
    with database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0


def test_modified_constructed_model_is_revalidated(database: Database) -> None:
    bad = receipt().model_copy(update={"all_applicable_blocking_rules_passed": 1})
    with pytest.raises(ResearchPolicyReceiptError, match="contract_invalid"):
        with database.session() as session:
            write(session, bad)


def test_a_reused_session_reloads_the_current_run_before_inserting(database: Database) -> None:
    caller = database.factory()
    try:
        cached_run = caller.get(RunRecord, RUN)
        assert cached_run is not None and cached_run.event_id == EVENT
        caller.commit()
        with database.session() as other:
            current = other.get(RunRecord, RUN)
            assert current is not None
            current.event_id = "event-changed-by-another-connection"
        # Keep a strong ORM reference across transactions, as a real caller can.
        assert cached_run.event_id == EVENT
        caller.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ResearchPolicyReceiptError, match="run_identity_mismatch"):
            store_policy_check_receipt(caller, receipt())
    finally:
        caller.rollback()
        caller.close()


@pytest.mark.parametrize("operation", ["read", "store"])
def test_a_reused_session_reloads_changed_receipt_rows(
    database: Database, operation: str,
) -> None:
    original = receipt()
    with database.session() as session:
        write(session, original)
    caller = database.factory()
    try:
        cached_row = caller.get(RunRoleOutputRecord, original.receipt_id)
        assert cached_row is not None
        old_hash = cached_row.payload_hash
        caller.commit()
        with database.session() as other:
            changed = other.get(RunRoleOutputRecord, original.receipt_id)
            assert changed is not None
            changed.payload_hash = "0" * 64
        assert cached_row.payload_hash == old_hash
        caller.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ResearchPolicyReceiptError, match="policy_receipt_saved_invalid"):
            if operation == "read":
                read_policy_check_receipt(caller, original.receipt_id)
            else:
                store_policy_check_receipt(caller, original)
    finally:
        caller.rollback()
        caller.close()


@pytest.mark.parametrize("different_content", [False, True])
def test_two_connections_serialize_identical_or_conflicting_receipts(
    database: Database, different_content: bool,
) -> None:
    first, second = receipt(), receipt()
    if different_content:
        second.checks[0].reason_code = "second_writer_different_result"
    other = Database(str(database.engine.url))
    first_ready, second_begin, first_release = Event(), Event(), Event()

    def before_second_begin(
        _connection: object, _cursor: object, statement: str,
        _parameters: object, _context: object, _executemany: bool,
    ) -> None:
        if statement == "BEGIN IMMEDIATE":
            second_begin.set()

    event.listen(other.engine, "before_cursor_execute", before_second_begin)

    def first_writer() -> str:
        with database.session() as session:
            saved = write(session, first)
            first_ready.set()
            assert first_release.wait(timeout=5)
            return saved.receipt_id

    def second_writer() -> str:
        assert first_ready.wait(timeout=5)
        with other.session() as session:
            try:
                return write(session, second).receipt_id
            except ResearchPolicyReceiptError as exc:
                assert different_content
                return exc.error_code

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first_future = workers.submit(first_writer)
            assert first_ready.wait(timeout=5)
            second_future = workers.submit(second_writer)
            assert second_begin.wait(timeout=5)
            first_release.set()
            assert first_future.result(timeout=5) == first.receipt_id
            assert second_future.result(timeout=5) == (
                "policy_receipt_content_conflict" if different_content else first.receipt_id
            )
        with database.session() as session:
            saved = read_policy_check_receipt(session, first.receipt_id)
            assert saved is not None
            assert saved.checks[0].reason_code == "missing_native_proof"
            assert session.query(RunRoleOutputRecord).count() == 1
    finally:
        first_release.set()
        event.remove(other.engine, "before_cursor_execute", before_second_begin)
        other.engine.dispose()
