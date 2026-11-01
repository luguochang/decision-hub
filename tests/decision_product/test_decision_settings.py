"""Real SQLite settings gates; the seeded source reader is an explicit port fixture."""

from __future__ import annotations

import importlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import Column, MetaData, String, Table, event, func, select, text
from sqlalchemy.exc import IntegrityError

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    ProductCommandRecord,
)

APP = "packages.kernel.decision_hub_kernel.application.decision_settings"
RECORDS = "packages.kernel.decision_hub_kernel.persistence.decision_settings_records"
DTO = "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2"
OWNER = "settings-owner"
NOW = datetime(2026, 10, 19, tzinfo=UTC)
ORIGINALS = Table(
    "settings_gate_originals",
    MetaData(),
    Column("ref", String, primary_key=True),
    Column("hash", String, nullable=False),
    Column("owner", String),
)


def modules():
    assert importlib.util.find_spec(APP) is not None, "persistent settings service is missing"
    return tuple(importlib.import_module(name) for name in (APP, RECORDS, DTO))


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{(tmp_path / 'settings.sqlite3').as_posix()}")
    db.create_all()
    ORIGINALS.create(db.engine)
    with db.session() as session:
        session.execute(
            ORIGINALS.insert(),
            [
                {"ref": "instrument-btc", "hash": "a" * 64, "owner": None},
                {"ref": "instrument-eth", "hash": "b" * 64, "owner": None},
                {"ref": "paper-profile.v1", "hash": "c" * 64, "owner": None},
                {"ref": "paper-profile.v2", "hash": "d" * 64, "owner": None},
                {"ref": "paper-owned", "hash": "e" * 64, "owner": OWNER},
                {"ref": "paper-unowned", "hash": "f" * 64, "owner": "other-owner"},
            ],
        )
    yield db
    db.engine.dispose()


def binding_reader(session, owner, command):
    """Exercise real readset/ownership checks without claiming a production source adapter."""
    if command.values is None:
        return
    values = command.values
    if values.account_snapshot.owner_id != owner:
        raise PermissionError("settings_account_owner_mismatch")
    refs = [values.instrument, values.execution_profile]
    if command.partition.account_kind == "paper":
        refs.append(values.account_snapshot.original)
    for ref in refs:
        if ref is None:
            raise PermissionError("settings_paper_original_required")
        row = session.execute(select(ORIGINALS).where(ORIGINALS.c.ref == ref.ref)).mappings().one()
        if row["hash"] != ref.hash:
            raise ValueError("settings_original_hash_mismatch")
        if row["owner"] is not None and row["owner"] != owner:
            raise PermissionError("settings_paper_owner_mismatch")


def make_command(
    *,
    owner=OWNER,
    request="save-1",
    version=0,
    account="declared-a",
    kind="user_declared",
    instrument="okx:BTC-USDT-SWAP",
    profile="paper-profile.v1",
):
    _, _, dto = modules()
    instrument_ref = {
        "ref": "instrument-btc" if "BTC" in instrument else "instrument-eth",
        "hash": ("a" if "BTC" in instrument else "b") * 64,
    }
    profile_ref = {"ref": profile, "hash": ("c" if profile.endswith("v1") else "d") * 64}
    return dto.DecisionSettingsCommandV1.model_validate(
        {
            "schema_version": "decision-settings-command.v1",
            "request_id": request,
            "expected_state_version": version,
            "operation": "upsert",
            "partition": {
                "account_kind": kind,
                "account_id": account,
                "instrument_id": instrument,
                "profile_ref": profile,
            },
            "values": {
                "instrument": instrument_ref,
                "execution_profile": profile_ref,
                "position_context": {
                    "schema_version": "position-context.v2",
                    "instrument_id": instrument,
                    "instrument_ref": instrument_ref,
                    "state": "unknown",
                    "signed_contracts": None,
                    "entry_price": None,
                    "isolated_margin": None,
                    "margin_mode": "unknown",
                    "position_mode": "unknown",
                    "hedge_leg": None,
                    "leverage": None,
                    "liquidation_estimate": None,
                    "source": "unknown",
                    "ref": None,
                    "hash": None,
                    "account_ref": None,
                },
                "account_snapshot": {
                    "schema_version": "account-snapshot.v2",
                    "owner_id": owner,
                    "account_kind": kind,
                    "account_id": account,
                    "source": "unknown",
                        "as_of": NOW.isoformat().replace("+00:00", "Z"),
                    "account_mode": "unknown",
                    "positions": None,
                    "collateral": None,
                    "valuation_ccy": "USDT",
                    "equity": None,
                    "available_margin": None,
                    "fee_tier": None,
                    "limits": {
                        name: None
                        for name in (
                            "max_gross_notional",
                            "max_reserved_margin",
                            "max_fee_buffer",
                            "max_stress_loss",
                        )
                    },
                    "original": None,
                },
                "utility_policy": {
                    "schema_version": "utility-policy.v2",
                    "policy_ref": "risk.v1",
                    "hash": "1" * 64,
                    "source": "user",
                    "objective": "mean_net",
                    "risk_penalty": "0",
                    "max_gross_notional": None,
                    "max_planned_loss": None,
                    "tail_constraints": None,
                    "position_scenario": "unknown",
                    "margin_buffer_policy": None,
                    "allowed_leverage": None,
                    "allowed_sides": None,
                    "account_ref": None,
                },
            },
        }
    )


def service(database, reader=binding_reader):
    app, _, _ = modules()
    return app.DecisionSettingsService(database, now=lambda: NOW, binding_reader=reader)


def counts(database):
    _, records, _ = modules()
    with database.session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                records.DecisionSettingsRevisionRecord,
                records.DecisionSettingsHeadRecord,
                ProductCommandRecord,
                OutboxRecord,
            )
        )


def changed(command, **values):
    return type(command).model_validate({**command.model_dump(mode="json"), **values})


def test_save_reopens_original_receipt_and_pending_without_inventing_account_values(database):
    command = make_command()
    first = service(database).save(OWNER, command)
    reopened = Database(database.engine.url.render_as_string(hide_password=False))
    try:
        fresh = service(reopened)
        assert fresh.receipt(OWNER, command.request_id) == first
        assert fresh.save(OWNER, command) == first
        current = fresh.current(OWNER, command.partition)
        assert current.revision.revision_id == first.revision.ref
        assert current.revision.values.account_snapshot.equity is None
        assert current.revision.values.account_snapshot.available_margin is None
        assert current.revision.values.position_context.signed_contracts is None
        assert first.reevaluation_state == current.reevaluation_intent_state == "pending"
        pending = fresh.pending()
        assert len(pending) == 1
        assert (pending[0].revision_id, pending[0].revision_hash, pending[0].state_version) == (
            first.revision.ref,
            first.revision.hash,
            1,
        )
        assert counts(reopened) == (1, 1, 1, 0)
    finally:
        reopened.engine.dispose()


def test_original_replay_precedes_current_cas_and_unavailable_binding(database):
    command = make_command()
    first = service(database).save(OWNER, command)
    service(database).save(OWNER, changed(command, request_id="save-2", expected_state_version=1))

    def unavailable(*_):
        raise AssertionError("historical replay must not authenticate current sources")

    assert service(database, unavailable).save(OWNER, command) == first
    assert service(database).current(OWNER, command.partition).state_version == 2
    with pytest.raises(ValueError, match="decision_settings_request_conflict"):
        service(database, unavailable).save(OWNER, changed(command, expected_state_version=7))
    assert counts(database) == (2, 1, 2, 0)


def test_two_connections_same_cas_have_one_winner_and_no_losing_receipt(database):
    command = make_command()
    service(database).save(OWNER, command)
    barrier = Barrier(2)

    def reader(session, owner, cmd):
        binding_reader(session, owner, cmd)
        barrier.wait(timeout=30)

    def save(request):
        try:
            return (
                service(database, reader)
                .save(
                    OWNER,
                    changed(
                        command,
                        request_id=request,
                        expected_state_version=1,
                    ),
                )
                .state_version
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(save, ("racer-a", "racer-b")))
    assert sorted(map(str, result)) == ["2", "decision_settings_state_conflict"]
    assert counts(database) == (2, 1, 2, 0)
    assert service(database).pending()[0].scan_generation == 2


def test_all_partition_axes_store_independently_and_cross_owner_payload_is_rejected(database):
    commands = [
        make_command(),
        make_command(owner="other-owner"),
        make_command(account="declared-b"),
        make_command(instrument="okx:ETH-USDT-SWAP"),
        make_command(profile="paper-profile.v2"),
    ]
    for i, command in enumerate(commands):
        command = changed(command, request_id=f"partition-{i}")
        owner = command.values.account_snapshot.owner_id
        saved = service(database).save(owner, command)
        assert (
            service(database).current(owner, command.partition).revision.revision_id
            == saved.revision.ref
        )
    assert counts(database) == (5, 5, 5, 0)
    before = counts(database)
    with pytest.raises(PermissionError, match="owner"):
        service(database).save("forged-owner", make_command(request="forged"))
    with pytest.raises(ValueError, match="command_missing"):
        service(database).receipt("forged-owner", "partition-0")
    assert counts(database) == before


def test_paper_and_declared_same_id_have_separate_heads_and_unowned_paper_is_rejected(database):
    declared = make_command(account="paper-owned")
    service(database).save(OWNER, declared)
    paper = make_command(kind="paper", account="paper-owned", request="paper")
    raw = paper.model_dump(mode="json")
    raw["values"]["account_snapshot"].update(
        {
            "source": "paper_policy",
            "original": {"ref": "paper-owned", "hash": "e" * 64},
        }
    )
    paper = type(paper).model_validate(raw)
    saved = service(database).save(OWNER, paper)
    assert saved.settings_key != service(database).receipt(OWNER, "save-1").settings_key
    assert service(database).current(OWNER, paper.partition).state_version == 1
    raw["request_id"] = "unowned-paper"
    raw["partition"]["account_id"] = "paper-unowned"
    raw["values"]["account_snapshot"].update(
        {
            "account_id": "paper-unowned",
            "original": {"ref": "paper-unowned", "hash": "f" * 64},
        }
    )
    before = counts(database)
    with pytest.raises(PermissionError, match="paper_owner"):
        service(database).save(OWNER, type(paper).model_validate(raw))
    assert counts(database) == before == (2, 2, 2, 0)


@pytest.mark.parametrize("boundary", ["revision", "receipt"])
def test_failure_rolls_back_revision_head_receipt_and_pending(database, boundary):
    _, records, _ = modules()
    target = (
        records.DecisionSettingsRevisionRecord if boundary == "revision" else ProductCommandRecord
    )

    def fail(*_):
        raise RuntimeError("injected_atomic_failure")

    event.listen(target, "after_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="injected_atomic_failure"):
            service(database).save(OWNER, make_command())
    finally:
        event.remove(target, "after_insert", fail)
    assert counts(database) == (0, 0, 0, 0)
    assert not service(database).pending()


def test_prepared_source_change_before_writer_cannot_half_save(database):
    def reader(session, owner, command):
        binding_reader(session, owner, command)
        with database.session() as other:
            other.execute(
                ORIGINALS.update()
                .where(ORIGINALS.c.ref == "instrument-btc")
                .values(
                    hash="0" * 64,
                )
            )

    with pytest.raises(ValueError, match="^original_preparation_readset_changed$"):
        service(database, reader).save(OWNER, make_command())
    assert counts(database) == (0, 0, 0, 0)


def test_remove_and_restore_preserve_history_and_reset_pending_cursor(database):
    app, records, _ = modules()
    command = make_command()
    first = service(database).save(OWNER, command)
    with database.session() as session:
        head = session.get(
            records.DecisionSettingsHeadRecord, app.settings_key(OWNER, command.partition)
        )
        head.scan_after_plan_id, head.scan_generation = "scanned-plan", 7
    removed = service(database).save(
        OWNER,
        changed(
            command,
            request_id="remove",
            expected_state_version=1,
            operation="remove",
            values=None,
        ),
    )
    view = service(database).current(OWNER, command.partition)
    assert removed.state_version == 2 and view.revision.values is None
    assert view.revision.parent.ref == first.revision.ref
    assert view.scan_generation == 8 and view.scan_after_plan_id is None
    restored = service(database).save(
        OWNER,
        changed(
            command,
            request_id="restore",
            expected_state_version=2,
        ),
    )
    assert restored.state_version == 3
    assert service(database).receipt(OWNER, command.request_id) == first


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "REPLACE"])
def test_revision_is_sqlite_append_only(database, operation):
    saved = service(database).save(OWNER, make_command())
    with pytest.raises(IntegrityError):
        with database.session() as session:
            if operation == "UPDATE":
                session.execute(
                    text("UPDATE decision_settings_revisions SET payload_hash=:bad"),
                    {"bad": "0" * 64},
                )
            elif operation == "DELETE":
                session.execute(text("DELETE FROM decision_settings_revisions"))
            else:
                session.execute(
                    text(
                        "INSERT OR REPLACE INTO decision_settings_revisions "
                        "SELECT * FROM decision_settings_revisions WHERE revision_id=:id"
                    ),
                    {"id": saved.revision.ref},
                )
    assert service(database).receipt(OWNER, "save-1") == saved


def test_binding_reader_required_and_declared_identity_is_owner_scoped(database):
    app, _, _ = modules()
    with pytest.raises(ValueError, match="binding_reader_required"):
        app.DecisionSettingsService(database, now=lambda: NOW, binding_reader=None)
    assert app.declared_account_id(OWNER, "allocate") == app.declared_account_id(OWNER, "allocate")
    assert app.declared_account_id(OWNER, "allocate") != app.declared_account_id(
        "other", "allocate"
    )


def test_settings_migration_installs_tables_and_immutable_guards(tmp_path, monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    path = Path(__file__).resolve().parents[2] / "migrations/versions/0099_decision_settings.py"
    assert path.is_file(), "settings migration is missing"
    spec = importlib.util.spec_from_file_location("settings_migration_gate", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}")
    try:
        with engine.begin() as connection:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == {
                "decision_settings_revisions",
                "decision_settings_heads",
            }
            triggers = (
                connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name",
                    )
                )
                .scalars()
                .all()
            )
            assert len(triggers) == 3
            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    finally:
        engine.dispose()
