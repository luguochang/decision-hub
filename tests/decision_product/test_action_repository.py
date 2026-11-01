"""Real SQLite repository boundaries; all research inputs here are synthetic."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update

from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
    DecisionSnapshotRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import (
    ActionRepository,
    ActionRepositoryError,
    make_command_receipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.action_plan import DecisionSnapshot

from .action_fixtures import (
    ASOF,
    HASH,
    make_head,
    make_opportunity,
    make_revision,
    make_snapshot,
    seed_plan,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'repository.sqlite3'}")
    database.create_all()
    yield database
    database.engine.dispose()


def receipt(**changes):
    return make_command_receipt(
        **{
            "owner_id": "owner-1",
            "request_id": "request-1",
            "plan_id": "plan-1",
            "operation": "display",
            "payload_hash": HASH,
            "response": {"status": "recorded", "note": "保留原回执"},
            "accepted_at": ASOF + timedelta(minutes=2),
            **changes,
        }
    )


def test_write_requires_caller_physical_transaction_and_no_hidden_commit(db):
    with db.session() as session:
        with pytest.raises(ActionRepositoryError, match="action_writer_transaction_required"):
            ActionRepository(session).register_opportunity(make_opportunity())
    with pytest.raises(RuntimeError, match="caller_rollback"):
        with db.session() as session:
            begin_immediate(session)
            seed_plan(session)
            ActionRepository(session).append_command_receipt(receipt())
            raise RuntimeError("caller_rollback")
    with db.session() as session:
        for record in (
            DecisionOpportunityRecord,
            DecisionSnapshotRecord,
            ActionPlanRevisionRecord,
            ActionPlanHeadRecord,
            ActionCommandReceiptRecord,
        ):
            assert session.scalar(select(func.count()).select_from(record)) == 0


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("horizon", ["30m", "24h", "72h"])
def test_exact_scope_horizon_roundtrip_and_registration_denominator(db, asset, horizon):
    opportunity = make_opportunity(asset=asset, horizon=horizon)
    with db.session() as session:
        begin_immediate(session)
        original = seed_plan(session, opportunity=opportunity)
        repository = ActionRepository(session)
        repository.set_opportunity_status(opportunity.id, expected_status="open", status="decided")
        repeated = repository.register_opportunity(opportunity)
        assert repeated.status == "decided"
        assert repeated.registered_at == opportunity.registered_at
        assert repository.append_snapshot(original[1]) == original[1]
        assert repository.create_plan(original[2], head=original[3]) == original[3]
    with db.session() as session:
        repository = ActionRepository(session)
        assert repository.get_opportunity_by_key(opportunity.key).status == "decided"
        assert repository.get_snapshot(original[1].id) == original[1]
        assert repository.get_revision(original[2].revision_id) == original[2]
        assert repository.get_head(original[3].plan_id) == original[3]
        assert session.scalar(select(func.count()).select_from(DecisionOpportunityRecord)) == 1


def test_same_key_cannot_shift_window_or_source_snapshot_identity(db):
    with db.session() as session:
        begin_immediate(session)
        opportunity, snapshot, _, _ = seed_plan(session)
        repository = ActionRepository(session)
        with pytest.raises(ActionRepositoryError, match="action_opportunity_key_conflict"):
            repository.register_opportunity(
                make_opportunity(time_end=opportunity.time_end + timedelta(hours=1))
            )
        with pytest.raises(ActionRepositoryError, match="action_identity_conflict"):
            repository.append_snapshot(make_snapshot(opportunity, id="different-id"))
        with pytest.raises(ActionRepositoryError, match="action_identity_conflict"):
            repository.append_snapshot(
                make_snapshot(opportunity, gaps=[{"scope": "price", "reason": "changed"}])
            )
        assert repository.get_snapshot(snapshot.id) == snapshot


@pytest.mark.parametrize(
    "changes",
    [
        {"scope_id": "crypto:spot:okx:ETH-USDT:trade:1m"},
        {"horizon": "72h"},
    ],
)
def test_snapshot_cannot_cross_parent_scope_or_horizon(db, changes):
    with db.session() as session:
        begin_immediate(session)
        opportunity = make_opportunity()
        repository = ActionRepository(session)
        repository.register_opportunity(opportunity)
        with pytest.raises(ActionRepositoryError, match="action_scope_horizon_mismatch"):
            repository.append_snapshot(make_snapshot(opportunity, **changes))


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"scope_id": "crypto:spot:okx:ETH-USDT:trade:1m"}, "action_scope_horizon_mismatch"),
        ({"horizon": "30m"}, "action_scope_horizon_mismatch"),
        ({"target_spec_hash": "b" * 64}, "action_target_recipe_mismatch"),
        ({"as_of": ASOF - timedelta(minutes=1)}, "action_revision_time_binding_invalid"),
        ({"valid_until": ASOF + timedelta(days=2)}, "action_revision_time_binding_invalid"),
    ],
)
def test_revision_rejects_wrong_parent_binding(db, changes, reason):
    with db.session() as session:
        begin_immediate(session)
        repository = ActionRepository(session)
        opportunity = repository.register_opportunity(make_opportunity())
        snapshot = repository.append_snapshot(make_snapshot(opportunity))
        revision = make_revision(snapshot, opportunity, **changes)
        with pytest.raises(ActionRepositoryError, match=reason):
            repository.create_plan(revision, head=make_head(revision))


def test_three_position_branches_share_one_opportunity_and_no_duplicate_flat(db):
    with db.session() as session:
        begin_immediate(session)
        repository = ActionRepository(session)
        opportunity = repository.register_opportunity(make_opportunity())
        snapshot = repository.append_snapshot(make_snapshot(opportunity))
        for position in ("flat", "long", "unknown"):
            revision = make_revision(
                snapshot,
                opportunity,
                revision_id="r-" + position,
                plan_id="p-" + position,
                position_context=position,
            )
            repository.create_plan(revision, head=make_head(revision))
        another = make_revision(snapshot, opportunity, revision_id="r-other", plan_id="p-other")
        with pytest.raises(ActionRepositoryError, match="action_position_branch_conflict"):
            repository.create_plan(another, head=make_head(another))
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(DecisionOpportunityRecord)) == 1
        assert session.scalar(select(func.count()).select_from(DecisionSnapshotRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ActionPlanHeadRecord)) == 3


def test_new_revision_cas_preserves_original_and_clears_old_lease(db):
    with db.session() as session:
        begin_immediate(session)
        opportunity, snapshot, old_revision, old_head = seed_plan(session)
        repository = ActionRepository(session)
        revision = make_revision(
            snapshot,
            opportunity,
            revision_id="revision-2",
            revision_no=2,
            previous_id=old_revision.revision_id,
            published_at=ASOF + timedelta(minutes=3),
        )
        head = make_head(revision, state_version=2, lease_generation=1)
        assert (
            repository.append_revision(
                revision,
                expected_revision_id=old_revision.revision_id,
                expected_state_version=1,
                next_head=head,
            )
            == head
        )
        assert repository.get_revision(old_revision.revision_id) == old_revision
        assert repository.get_head(old_head.plan_id) == head
        assert (
            repository.append_revision(
                revision,
                expected_revision_id=old_revision.revision_id,
                expected_state_version=1,
                next_head=head,
            )
            == head
        )
        with pytest.raises(ActionRepositoryError, match="action_revision_superseded"):
            repository.change_head(
                old_head.plan_id,
                expected_revision_id=old_revision.revision_id,
                expected_state_version=1,
                next_head=old_head,
            )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"revision_no": 3}, "action_revision_predecessor_mismatch"),
        ({"position_context": "long"}, "action_revision_predecessor_mismatch"),
        ({"previous_id": "not-current"}, "action_revision_predecessor_mismatch"),
    ],
)
def test_revision_predecessor_position_and_sequence_are_fixed(db, changes, reason):
    with db.session() as session:
        begin_immediate(session)
        opportunity, snapshot, old, _ = seed_plan(session)
        revision = make_revision(
            snapshot,
            opportunity,
            **{
                "revision_id": "revision-2",
                "revision_no": 2,
                "previous_id": old.revision_id,
                **changes,
            },
        )
        with pytest.raises(ActionRepositoryError, match=reason):
            ActionRepository(session).append_revision(
                revision,
                expected_revision_id=old.revision_id,
                expected_state_version=1,
                next_head=make_head(revision, state_version=2, lease_generation=1),
            )


def test_pause_resets_streak_but_keeps_bar_watermark_and_fences_claim(db):
    with db.session() as session:
        begin_immediate(session)
        _, _, revision, _ = seed_plan(session)
        session.execute(
            update(ActionPlanHeadRecord).values(
                enter_streak=2,
                last_input_watermark=ASOF,
                lease_generation=4,
                lease_owner="worker-old",
                lease_expires_at=ASOF + timedelta(minutes=5),
            )
        )
        repository = ActionRepository(session)
        original = repository.get_head(revision.plan_id)
        paused = make_head(
            revision,
            monitor_state="paused",
            next_due=None,
            state_version=2,
            lease_generation=5,
            last_input_watermark=ASOF,
        )
        assert (
            repository.change_head(
                revision.plan_id,
                expected_revision_id=revision.revision_id,
                expected_state_version=1,
                next_head=paused,
            )
            == paused
        )
        assert (
            repository.get_head(revision.plan_id).last_input_watermark
            == original.last_input_watermark
        )
        assert repository.get_head(revision.plan_id).enter_streak == 0


def test_two_writer_commands_with_same_expected_version_only_one_commits(db):
    with db.session() as session:
        begin_immediate(session)
        _, _, revision, _ = seed_plan(session)
    barrier = Barrier(2)

    def command():
        barrier.wait(timeout=5)
        try:
            with db.session() as session:
                begin_immediate(session)
                ActionRepository(session).change_head(
                    revision.plan_id,
                    expected_revision_id=revision.revision_id,
                    expected_state_version=1,
                    next_head=make_head(
                        revision,
                        monitor_state="paused",
                        next_due=None,
                        state_version=2,
                        lease_generation=1,
                    ),
                )
            return "committed"
        except ActionRepositoryError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(command), executor.submit(command)]
        results = sorted(f.result(timeout=10) for f in futures)
    assert results == ["action_state_version_conflict", "committed"]


def test_owner_receipt_replay_returns_original_time_and_response(db):
    first = receipt()
    with db.session() as session:
        begin_immediate(session)
        seed_plan(session)
        repository = ActionRepository(session)
        assert repository.append_command_receipt(first) == first
        later = receipt(
            accepted_at=ASOF + timedelta(hours=1), response={"status": "different response"}
        )
        assert repository.append_command_receipt(later) == first
        assert repository.get_command("owner-2", "request-1") is None
        other = receipt(owner_id="owner-2")
        assert repository.append_command_receipt(other) == other
        with pytest.raises(ActionRepositoryError, match="action_command_key_conflict"):
            repository.append_command_receipt(receipt(payload_hash="b" * 64))
    with db.session() as session:
        assert ActionRepository(session).get_command("owner-1", "request-1") == first


def test_notification_failure_rolls_back_all_three_branches_and_receipt(db):
    with pytest.raises(RuntimeError, match="notification_injected_failure"):
        with db.session() as session:
            begin_immediate(session)
            repository = ActionRepository(session)
            opportunity = repository.register_opportunity(make_opportunity())
            snapshot = repository.append_snapshot(make_snapshot(opportunity))
            for position in ("flat", "long", "unknown"):
                revision = make_revision(
                    snapshot,
                    opportunity,
                    plan_id="plan-" + position,
                    revision_id="revision-" + position,
                    position_context=position,
                )
                repository.create_plan(revision, head=make_head(revision))
            repository.append_command_receipt(receipt(plan_id="plan-flat"))
            session.add(
                OutboxRecord(
                    artifact_id=None,
                    subject_kind="action_revision",
                    subject_id="revision-flat",
                    payload_json="{}",
                    payload_hash=HASH,
                    channel="local",
                    dedupe_key="action:test",
                )
            )
            session.flush()
            raise RuntimeError("notification_injected_failure")
    with db.session() as session:
        for record in (
            DecisionOpportunityRecord,
            DecisionSnapshotRecord,
            ActionPlanRevisionRecord,
            ActionPlanHeadRecord,
            ActionCommandReceiptRecord,
            OutboxRecord,
        ):
            assert session.scalar(select(func.count()).select_from(record)) == 0


@pytest.mark.parametrize(
    "table,column,value,reader",
    [
        ("decision_snapshots", "payload_hash", "b" * 64, "snapshot"),
        ("decision_snapshots", "recipe_hash", "b" * 64, "snapshot"),
        ("action_plan_revisions", "opportunity_id", "different-parent", "revision"),
        ("action_command_receipts", "payload_hash", "b" * 64, "receipt"),
    ],
)
def test_reads_reject_corrupt_mirror_columns_even_if_payload_hash_valid(
    db, table, column, value, reader
):
    with db.session() as session:
        begin_immediate(session)
        seed_plan(session)
        ActionRepository(session).append_command_receipt(receipt())
    # Only this isolated test corrupts its fixture after disabling one trigger.
    with db.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table}_immutable_update")
        connection.exec_driver_sql(f"UPDATE {table} SET {column}=?", (value,))
    with db.session() as session:
        repository = ActionRepository(session)
        with pytest.raises(ActionRepositoryError, match="action_record_binding_mismatch"):
            {
                "snapshot": lambda: repository.get_snapshot("decision-1"),
                "revision": lambda: repository.get_revision("revision-1"),
                "receipt": lambda: repository.get_command("owner-1", "request-1"),
            }[reader]()


def test_reads_reject_changed_original_and_receipt_response(db):
    with db.session() as session:
        begin_immediate(session)
        seed_plan(session)
        ActionRepository(session).append_command_receipt(receipt())
    with db.engine.begin() as connection:
        for table in ("decision_snapshots", "action_command_receipts"):
            connection.exec_driver_sql(f"DROP TRIGGER trg_{table}_immutable_update")
        raw = json.loads(
            connection.exec_driver_sql("SELECT payload_json FROM decision_snapshots").scalar_one()
        )
        raw["gaps"] = [{"scope": "price", "reason": "tampered"}]
        connection.exec_driver_sql(
            "UPDATE decision_snapshots SET payload_json=?", (json.dumps(raw),)
        )
        saved = json.loads(
            connection.exec_driver_sql(
                "SELECT response_json FROM action_command_receipts"
            ).scalar_one()
        )
        saved["response"] = {"status": "forged"}
        connection.exec_driver_sql(
            "UPDATE action_command_receipts SET response_json=?", (json.dumps(saved),)
        )
    with db.session() as session:
        repository = ActionRepository(session)
        with pytest.raises(ActionRepositoryError, match="action_original_hash_mismatch"):
            repository.get_snapshot("decision-1")
        with pytest.raises(ActionRepositoryError, match="action_record_binding_mismatch"):
            repository.get_command("owner-1", "request-1")


def test_unsafe_model_copy_cannot_bypass_finite_probability_or_original_hash(db):
    with db.session() as session:
        begin_immediate(session)
        repository = ActionRepository(session)
        opportunity = repository.register_opportunity(make_opportunity())
        original = make_snapshot(opportunity)
        broken = original.model_copy(update={"snapshot_hash": "b" * 64})
        with pytest.raises(ActionRepositoryError, match="action_original_hash_mismatch"):
            repository.append_snapshot(broken)
        broken = original.model_copy(
            update={"probability": original.probability.model_copy(update={"value": float("nan")})}
        )
        with pytest.raises(ValidationError, match="decision_non_finite_number"):
            repository.append_snapshot(broken)


def test_utc_offset_normalization_gives_same_snapshot_hash():
    original = make_snapshot()
    wire = original.model_dump(mode="json")
    wire["decision_cutoff_at"] = "2026-10-13T12:00:00+08:00"
    wire["known_at"] = "2026-10-13T12:00:00+08:00"
    assert DecisionSnapshot.model_validate(wire) == original


def test_terminal_plan_is_not_restarted_by_command(db):
    with db.session() as session:
        begin_immediate(session)
        _, _, revision, _ = seed_plan(session)
        session.execute(
            update(ActionPlanHeadRecord).values(
                state="expired", monitor_state="stopped", next_due_at=None
            )
        )
        with pytest.raises(ActionRepositoryError, match="action_terminal_plan_restart_forbidden"):
            ActionRepository(session).change_head(
                revision.plan_id,
                expected_revision_id=revision.revision_id,
                expected_state_version=1,
                next_head=make_head(revision, state_version=2, lease_generation=1),
            )
