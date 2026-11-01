"""Fresh inventory codec and real private writer rollback; no provider calls."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    _bounded_current_inventory,
    _original_inventory,
    _ReadSourceGuard,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    _ConsumerSourceGuard,
    begin_immediate,
    prepare_originals,
)


def legacy_inventory(root: Path) -> tuple[str, ...]:
    return tuple(sorted(
        path.relative_to(root).as_posix()
        for directory, suffixes in (
            (root / "packages", {".py"}),
            (root / "contracts/schemas", {".json", ".yaml", ".yml"}),
        )
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in suffixes
    ))


def test_inventory_preserves_all_legacy_names_and_retained_capsules(tmp_path: Path) -> None:
    for name in (
        "packages/kernel/live.py",
        "packages/kernel/research_recipe_versions/old/root/packages/kernel/old.py",
        "packages/kernel/market_component_versions/old/root/packages/kernel/old.py",
        "packages/kernel/context_selector_versions/old.py",
        "packages/Case/upper.PY",
        "packages/.hidden.py",
        "contracts/schemas/nested/shape.yaml",
        "contracts/schemas/shape.json",
        "contracts/schemas/shape.yml",
        "contracts/schemas/ignore.JSON",
        "docs/ignored.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
    # Where Windows permits links, preserve file links and the original rule
    # that recursive glob does not descend into a directory symlink.
    try:
        (tmp_path / "packages/link.py").symlink_to(tmp_path / "packages/kernel/live.py")
        (tmp_path / "packages/linkdir").symlink_to(tmp_path / "packages/kernel", True)
    except OSError:
        pass
    assert _original_inventory(tmp_path) == legacy_inventory(tmp_path)
    assert any("research_recipe_versions" in name for name in _original_inventory(tmp_path))
    assert any("market_component_versions" in name for name in _original_inventory(tmp_path))


def test_inventory_is_fresh_for_addition_deletion_and_missing_tree(tmp_path: Path) -> None:
    assert _original_inventory(tmp_path) == ()
    path = tmp_path / "packages/kernel/research_recipe_versions/old/new.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")
    assert _original_inventory(tmp_path) == legacy_inventory(tmp_path)
    before = _original_inventory(tmp_path)
    added = path.with_name("added.py")
    added.write_bytes(b"new")
    assert _original_inventory(tmp_path) != before
    added.unlink()
    assert _original_inventory(tmp_path) == before
    path.unlink()
    assert _original_inventory(tmp_path) == ()


def test_bounded_inventory_prunes_only_exact_selected_archive_stores(tmp_path: Path) -> None:
    paths = (
        "packages/current/live.py",
        "contracts/schemas/current.json",
        "packages/kernel/research_recipe_versions/old/root/recipe.py",
        "packages/kernel/market_component_versions/old/root/component.py",
        "packages/kernel/research_skill_versions/old/root/skill.py",
        "packages/kernel/decision_hub_kernel/application/context_selector_versions/old.py",
    )
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
    inventory = _bounded_current_inventory(tmp_path)
    assert "packages/current/live.py" in inventory
    assert "contracts/schemas/current.json" in inventory
    assert not any("research_recipe_versions/old" in name for name in inventory)
    assert not any("market_component_versions/old" in name for name in inventory)
    assert any("research_skill_versions/old" in name for name in inventory)
    assert any("context_selector_versions/old.py" in name for name in inventory)

    for name in (
        "packages/kernel/research_recipe_versions/later/root/new.py",
        "packages/kernel/market_component_versions/later/root/new.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained")
    assert _bounded_current_inventory(tmp_path) == inventory

    added = tmp_path / "packages/another/live.py"
    added.parent.mkdir(parents=True)
    added.write_bytes(b"new current source")
    assert _bounded_current_inventory(tmp_path) != inventory


def test_callback_scope_identity_does_not_repeat_full_historical_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.kernel.decision_hub_kernel.application import research_graph_binding

    guard = _ReadSourceGuard.capture()
    assert guard is not None
    calls: list[Path] = []

    def inventory(root: Path) -> tuple[str, ...]:
        calls.append(root)
        return guard.inventory

    monkeypatch.setattr(research_graph_binding, "_original_inventory", inventory)
    assert guard.matches_scope_identity()
    assert calls == []
    assert guard.matches_identity()
    assert calls == [guard.root]


def test_consumer_hash_recheck_rejects_same_size_same_mtime_tamper(tmp_path: Path) -> None:
    class GraphBasis:
        def matches(self) -> bool:
            return True

    path = tmp_path / "packages/kernel/decision_hub_kernel/application/consumer.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    source = _ConsumerSourceGuard.capture(GraphBasis(), tmp_path)
    timestamp = path.stat()
    assert source.matches()
    path.write_bytes(b"altered!")
    os.utime(path, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    assert path.stat().st_size == timestamp.st_size
    assert path.stat().st_mtime_ns == timestamp.st_mtime_ns
    assert not source.matches()


def test_real_writer_recheck_rejects_original_mutation_and_rolls_back(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'originals.sqlite3'}")
    database.create_all()
    try:
        with database.session() as session:
            session.add(RunRoleOutputRecord(
                output_id="original", run_id="run", role="original", payload_json="{}",
                payload_hash="0" * 64, schema_version="original.v1", created_at=utcnow(),
            ))

        def original(session):
            assert session.get(RunRoleOutputRecord, "original") is not None
            assert session.get(RunRoleOutputRecord, "missing") is None

        prepared = prepare_originals(database, original)
        with pytest.raises(ValueError, match="original_preparation_original_changed"):
            with database.session() as session:
                begin_immediate(session)
                prepared.verify(session)
                row = session.get(RunRoleOutputRecord, "original")
                assert row is not None
                row.payload_json = '{"changed":true}'
                session.add(RunRoleOutputRecord(
                    output_id="publication", run_id="run", role="publication",
                    payload_json="{}", payload_hash="0" * 64,
                    schema_version="original.v1", created_at=utcnow(),
                ))
                session.flush()
                prepared.verify_after_writes(session)
        with database.session() as session:
            rows = session.scalars(select(RunRoleOutputRecord)).all()
            assert len(rows) == 1 and rows[0].payload_json == "{}"
    finally:
        database.engine.dispose()
