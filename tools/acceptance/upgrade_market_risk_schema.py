"""Explicit installed 0071/0076/0080/0090 -> registered target rehearsal/apply.

Preparation never opens a database. Running --probe reads the named existing live
DB and upgrades only a consistent private backup. --apply requires a passed probe,
the original standard build guard, and the standard stopped-product state. Failed
outputs/backups are retained. This tool never stamps, downgrades or creates a live DB.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMED_DATABASE = ROOT / "data/core-live-20260913/db/decision_hub.sqlite3"
EXPECTED_HEAD = "0071_study_effect_tracking_request_jobs"
EXPECTED_HEADS = (
    EXPECTED_HEAD,
    "0076_legacy_index_reconciliation",
    "0080_risk_research_requests",
    "0090_history_model_qualification_v3",
    "0092_research_report_audits",
    "0093_decision_product_v1",
    "0095_action_execution",
)

STOPPED_GUARD = r"""param([string]$RepositoryRoot,[string]$ReceiptPath)
$ErrorActionPreference='Stop'
. (Join-Path $RepositoryRoot 'infra/dsh/product-processes.ps1')
. (Join-Path $RepositoryRoot 'infra/dsh/validated-product-builds.ps1')
$runtime=Join-Path $RepositoryRoot 'data/product-local'
if(-not(Test-Path -LiteralPath (Join-Path $runtime 'stop.request') -PathType Leaf)){
 throw 'standard_stop_request_missing'
}
if(Test-Path -LiteralPath (Join-Path $runtime 'processes.json')){
 throw 'managed_process_roster_not_clear'
}
$supervisor=Get-Content -LiteralPath (Join-Path $runtime 'supervisor.json') -Raw|ConvertFrom-Json
if($supervisor.state -cne 'stopped' -or -not $supervisor.started_ticks){
 throw 'standard_supervisor_not_stopped'
}
foreach($entry in @($supervisor)+@($supervisor.children)){
 if($null -ne $entry -and (Test-ProductProcessIdentity $entry)){
  throw 'managed_original_lifetime_alive'
 }
}
$ports=@(Get-NetTCPConnection -State Listen -ErrorAction Stop|
 Where-Object LocalPort -In 8047,3097,8037,8038)
if($ports.Count){throw 'standard_product_ports_not_clear'}
$validation=Test-ValidatedProductBuilds -ReceiptPath $ReceiptPath -RepositoryRoot $RepositoryRoot
[pscustomobject]@{passed=$true;supervisor=$supervisor;validation=$validation;
 ports=@();stop_request=$true}|ConvertTo-Json -Depth 8
"""


# Preserve STOPPED_GUARD for existing historical maintenance callers. Only the
# normal build verifier dispatch changes; all process/port gates remain identical.
SCHEMA_STOPPED_GUARD = STOPPED_GUARD.replace(
    ". (Join-Path $RepositoryRoot 'infra/dsh/validated-product-builds.ps1')",
    ". (Join-Path $RepositoryRoot 'infra/dsh/product-build-validation.ps1')",
).replace(
    "$validation=Test-ValidatedProductBuilds -ReceiptPath",
    "$validation=Test-ProductValidatedBuilds -ReceiptPath",
)
BUILD_CODEC_REF = "packages/kernel/decision_hub_kernel/application/product_release_provenance.py"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def bound_json(path: Path, expected: str) -> dict:
    if len(expected) != 64 or digest(path) != expected:
        raise ValueError("bound_receipt_hash_changed")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("bound_receipt_object_required")
    return value


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _readonly(action, _arg1, _arg2, _database, _trigger):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    allowed |= {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE}
    # Read-only integrity/FK/table introspection; no writable PRAGMA is allowed.
    if action == sqlite3.SQLITE_PRAGMA and _arg1 in {
        "table_info", "index_list", "index_info", "foreign_key_check", "integrity_check"
    }:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


@contextmanager
def read_database(path: Path):
    if not path.is_file():
        raise ValueError("existing_database_required")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.set_authorizer(_readonly)
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def head(connection) -> str:
    rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(rows) != 1:
        raise ValueError("one_alembic_head_required")
    return rows[0][0]


def snapshot(connection, original: dict | None = None) -> dict:
    """Stream every old column/row; additive columns cannot hide old content changes."""
    names = sorted(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ) if row[0] != "alembic_version") if original is None else sorted(original)
    result = {}
    for name in names:
        info = connection.execute(f"PRAGMA table_info({quote(name)})").fetchall()
        columns = [row[1] for row in info] if original is None else original[name]["columns"]
        if not columns or not set(columns).issubset({row[1] for row in info}):
            raise ValueError("original_table_or_column_missing:" + name)
        primary = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        order = ",".join(quote(value) for value in primary) if primary else "rowid"
        cursor = connection.execute(
            f"SELECT {','.join(quote(value) for value in columns)} "
            f"FROM {quote(name)} ORDER BY {order}"
        )
        count, value = 0, hashlib.sha256()
        for row in cursor:
            encoded = [({"sqlite_blob_hex": item.hex()} if isinstance(item, bytes) else item)
                       for item in row]
            value.update(json.dumps(encoded, ensure_ascii=False, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8") + b"\n")
            count += 1
        result[name] = {"columns": columns, "count": count, "sha256": value.hexdigest()}
    return result


def checks(connection) -> dict:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != ["ok"] or foreign_keys:
        raise ValueError("sqlite_integrity_or_foreign_keys_failed")
    return {"integrity": integrity, "foreign_key_violations": foreign_keys}


def consistent_backup(source: Path, destination: Path, expected: str) -> dict:
    if destination.exists() or destination.resolve() == source.resolve():
        raise ValueError("fresh_independent_backup_required")
    with read_database(source) as reader:
        if head(reader) != expected:
            raise ValueError("original_head_changed")
        before = snapshot(reader)
        checks(reader)
        with sqlite3.connect(destination) as target:
            reader.backup(target)
        after = snapshot(reader)
        if before != after or reader.total_changes != 0:
            raise ValueError("source_logical_snapshot_changed")
    with read_database(destination) as clone:
        if head(clone) != expected or snapshot(clone) != before:
            raise ValueError("backup_logical_snapshot_mismatch")
        verified = checks(clone)
    return {"path": str(destination), "sha256": digest(destination), "original_tables": before,
            "logical_snapshot_consistent": True, **verified}


def migration_inputs() -> dict:
    files = [ROOT / "alembic.ini", ROOT / "migrations/env.py", Path(__file__).resolve()]
    files += [ROOT / "infra/dsh" / name for name in (
        "validated-product-builds.ps1", "product-processes.ps1", "stop-product.ps1",
        "product-build-validation.ps1", "validated-product-builds-v2.ps1",
    )]
    files.append(ROOT / BUILD_CODEC_REF)
    files += sorted((ROOT / "migrations/versions").glob("*.py"))
    files += sorted((ROOT / "packages/kernel/decision_hub_kernel/persistence").glob("*.py"))
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in files}


def load_orm():
    # Imports happen only after explicit CLI gates, never at preparation/import time.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from packages.kernel.decision_hub_kernel.persistence.db import Base, Database

    directory = ROOT / "packages/kernel/decision_hub_kernel/persistence"
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        if any(isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id == "Base" for base in node.bases
        ) for node in tree.body):
            importlib.import_module("packages.kernel.decision_hub_kernel.persistence." + path.stem)
    return Base, Database


def upgrade(path: Path, target: str, output: Path, originals: dict, inputs: dict,
            *, expected_head: str = EXPECTED_HEAD) -> dict:
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect

    Base, Database = load_orm()
    # Database() creates auxiliary directories even with explicit URL; keep those private.
    old_environment = os.environ.get("DECISION_HUB_DATA_DIR")
    os.environ["DECISION_HUB_DATA_DIR"] = str(output / "database-bootstrap")
    try:
        database = Database(
            "sqlite+pysqlite:///file:" + path.resolve().as_posix() + "?mode=rw&uri=true"
        )
    finally:
        if old_environment is None:
            os.environ.pop("DECISION_HUB_DATA_DIR", None)
        else:
            os.environ["DECISION_HUB_DATA_DIR"] = old_environment
    try:
        config = database._alembic_config()
        config.attributes["configure_logger"] = False
        config.set_main_option("script_location", str(ROOT / "migrations"))
        scripts = ScriptDirectory.from_config(config)
        revision = scripts.get_revision(target)
        if revision is None or revision.revision != target:
            raise ValueError("explicit_exact_target_revision_required")
        if expected_head not in EXPECTED_HEADS:
            raise ValueError("unregistered_installed_head")
        chain = [item.revision for item in scripts.iterate_revisions(target, expected_head)]
        if not chain:
            raise ValueError("target_must_upgrade_expected_installed_head")
        with database.engine.connect() as writer:
            # env.py explicitly supports a supplied actual Connection/physical TX.
            # SQLite DDL must share BEGIN IMMEDIATE; Alembic's generic logical
            # transaction alone does not establish an atomic SQLite DDL boundary.
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            native = writer.connection.driver_connection
            if head(native) != expected_head or snapshot(native) != originals:
                raise ValueError("original_changed_before_physical_writer")
            config.attributes["connection"] = writer
            try:
                if migration_inputs() != inputs:
                    raise ValueError("migration_inputs_changed_before_upgrade")
                command.upgrade(config, target)
                inspector = inspect(writer)
                schema = {}
                for name, table in Base.metadata.tables.items():
                    if name not in inspector.get_table_names():
                        raise ValueError("orm_table_missing:" + name)
                    actual = {column["name"]: column for column in inspector.get_columns(name)}
                    if not set(table.columns.keys()).issubset(actual):
                        raise ValueError("orm_columns_missing:" + name)
                    actual_indexes = {item["name"] for item in inspector.get_indexes(name)}
                    if not {index.name for index in table.indexes}.issubset(actual_indexes):
                        raise ValueError("orm_indexes_missing:" + name)
                    schema[name] = {"columns": sorted(actual), "indexes": sorted(actual_indexes)}
                if head(native) != target or snapshot(native, originals) != originals:
                    raise ValueError("target_or_original_content_changed_inside_migration")
                checks(native)
                if migration_inputs() != inputs:
                    raise ValueError("migration_inputs_changed_before_commit")
                writer.commit()
            except BaseException:
                writer.rollback()
                raise
        return {"target": target, "migration_chain": chain, "orm_schema": schema,
                "physical_atomic_migration": True}
    finally:
        database.engine.dispose()


def stopped_guard(output: Path, receipt: Path, expected_hash: str) -> dict:
    receipt_body = bound_json(receipt, expected_hash)
    script = output / "standard-stopped-build-guard.ps1"
    if not script.exists():
        with script.open("x", encoding="utf-8") as stream:
            stream.write(SCHEMA_STOPPED_GUARD)
    if script.read_text(encoding="utf-8") != SCHEMA_STOPPED_GUARD:
        raise ValueError("generated_original_guard_bridge_changed")
    run = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script),
         "-RepositoryRoot", str(ROOT), "-ReceiptPath", str(receipt)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    with (output / ("guard-" + str(datetime.now(UTC).timestamp()) + ".log")).open(
        "x", encoding="utf-8"
    ) as stream:
        stream.write(run.stdout + run.stderr)
    if run.returncode:
        raise ValueError("original_standard_stopped_build_guard_failed")
    result = json.loads(run.stdout)
    if result.get("passed") is not True:
        raise ValueError("original_standard_stopped_build_guard_unverified")
    if receipt_body.get("schema") == "product-validated-builds.v2":
        proof = result.get("validation", {})
        if (proof.get("receipt_schema") != "product-validated-builds.v2"
                or proof.get("receipt_sha256") != expected_hash
                or proof.get("verifier_source_sha256") != digest(ROOT / BUILD_CODEC_REF)):
            raise ValueError("standard_v2_build_receipt_binding_invalid")
    bound_json(receipt, expected_hash)
    return result


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target", required=True, help="Exact checked-in revision, never 'head'")
    parser.add_argument("--expected-head", choices=EXPECTED_HEADS, default=EXPECTED_HEAD,
                        help="Exact installed release head; never stamp or infer")
    parser.add_argument("--probe-receipt", type=Path)
    parser.add_argument("--probe-sha256")
    parser.add_argument("--build-receipt", type=Path)
    parser.add_argument("--build-sha256")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = args.database.resolve(strict=True)
    if database != NAMED_DATABASE.resolve(strict=True) or not database.is_file():
        raise ValueError("only_existing_named_product_database_allowed")
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "artifacts"):
        raise ValueError("fresh_artifacts_output_namespace_required")
    if args.target in {"head", "heads", "base", args.expected_head} or "+" in args.target:
        raise ValueError("exact_upgrade_target_required")
    if args.apply and not all((args.probe_receipt, args.probe_sha256,
                               args.build_receipt, args.build_sha256)):
        raise ValueError("apply_requires_probe_and_standard_build_receipt_bindings")
    output.mkdir(parents=True)
    report = {"schema_version": "market-risk-schema-upgrade.v1", "passed": False,
              "mode": "probe" if args.probe else "apply", "database": str(database),
              "expected_head": args.expected_head, "target": args.target,
              "started_at": datetime.now(UTC).isoformat(), "phase": "inputs",
              "service_actions": 0, "build_actions": 0, "pointer_writes": 0,
              "formal_migrations": 0, "errors": []}
    try:
        inputs = migration_inputs()
        report["migration_inputs"] = inputs
        if args.apply:
            probe = bound_json(args.probe_receipt, args.probe_sha256)
            if (probe.get("schema_version") != report["schema_version"]
                    or probe.get("mode") != "probe" or probe.get("passed") is not True
                    or probe.get("database") != str(database) or probe.get("target") != args.target
                    or probe.get("expected_head") != args.expected_head
                    or probe.get("migration_inputs") != inputs):
                raise ValueError("same_source_target_passed_probe_required")
            report["probe_receipt"] = {"path": str(args.probe_receipt),
                                       "sha256": args.probe_sha256}
            report["standard_guard"] = stopped_guard(output, args.build_receipt, args.build_sha256)
            report["build_receipt"] = {"path": str(args.build_receipt),
                                       "sha256": args.build_sha256}
        report["phase"] = "consistent_backup"
        backup = output / "original-consistent-backup.sqlite3"
        report["backup"] = consistent_backup(database, backup, args.expected_head)
        write(output / "backup-receipt.json", report["backup"])
        if args.probe:
            working = output / "probe-upgrade.sqlite3"
            # Independent consistent clone rather than copying a possibly active WAL main file.
            consistent_backup(backup, working, args.expected_head)
        else:
            stopped_guard(output, args.build_receipt, args.build_sha256)
            with read_database(database) as reader:
                if (head(reader) != args.expected_head
                        or snapshot(reader) != report["backup"]["original_tables"]):
                    raise ValueError("original_changed_after_apply_backup")
            working = database
        report["phase"] = "explicit_upgrade"
        if args.apply:
            report["formal_migrations"] = 1  # Actual attempt, even if a later check fails.
        report["upgrade"] = upgrade(
            working, args.target, output, report["backup"]["original_tables"], inputs,
            expected_head=args.expected_head,
        )
        report["phase"] = "verify_originals"
        with read_database(working) as reader:
            if head(reader) != args.target:
                raise ValueError("target_head_not_loaded")
            after = snapshot(reader, report["backup"]["original_tables"])
            if after != report["backup"]["original_tables"]:
                raise ValueError("old_outbox_run_artifact_or_other_content_changed")
            report["after_checks"] = checks(reader)
        if digest(backup) != report["backup"]["sha256"] or migration_inputs() != inputs:
            raise ValueError("backup_or_migration_sources_changed")
        report["old_columns_hashes_counts_preserved"] = True
        report["old_outbox_identity_preserved"] = after["outbox"]
        report["old_run_artifact_result_preserved"] = {
            name: after[name] for name in ("runs", "artifacts", "research_results")
        }
        report["working_database"] = str(working)
        report["passed"], report["phase"] = True, "completed"
    except Exception as error:
        report["errors"].append({"type": type(error).__name__, "detail": str(error),
                                  "traceback": traceback.format_exc()})
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        write(output / "receipt.json", report)
    print(json.dumps({"passed": report["passed"], "phase": report["phase"],
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
