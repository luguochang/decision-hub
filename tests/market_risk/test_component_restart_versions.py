"""Two actual interpreter lifetimes, current DB head, immutable old callback bytes."""

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from apps.hub_worker.market_component_versions import (
    _closure,
    _PrivateComponentRelease,
    load_component_versions,
    prepare_component_archive,
)
from apps.hub_worker.market_components import InstalledComponent

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture_version(root, version, factor):
    directory = root / "packages/rollback_fixture"
    directory.mkdir(parents=True, exist_ok=True)
    bodies = {
        "functions.py": (
            b"from .values import FACTOR\nfrom packages.rollback_fixture.more import NAME\n"
            b"\ndef evaluate(payload, parameters):\n"
            b"    return {'value': payload['value'] * FACTOR, 'version': NAME}\n"
        ),
        "values.py": f"FACTOR = {factor}\n".encode(),
        "more.py": f"NAME = '{version}'\n".encode(),
    }
    for name, body in bodies.items():
        (directory / name).write_bytes(body)
    tools_file = root / "tools/rollback_fixture.py"
    tools_file.parent.mkdir(parents=True, exist_ok=True)
    tools_file.write_text("OFFSET = 0\n")
    body = (directory / "functions.py").read_bytes()
    body = (
        b"from tools.rollback_fixture import OFFSET\nfrom importlib import import_module\n"
        + body.replace(
            b"'value': payload['value'] * FACTOR, 'version': NAME",
            b"'value': payload['value'] * FACTOR + OFFSET, "
            b"'version': import_module('packages.rollback_fixture.more').NAME",
        )
    )
    (directory / "functions.py").write_bytes(body)
    bodies["functions.py"] = body
    files = {"packages/rollback_fixture/" + name: sha(body) for name, body in bodies.items()}
    files["tools/rollback_fixture.py"] = sha(tools_file.read_bytes())
    private = _PrivateComponentRelease(root, files, "prepare_" + version)
    callback = private.load("packages.rollback_fixture.functions").evaluate
    installed = InstalledComponent(
        "feature",
        "restart-fixture",
        SCOPE,
        version,
        "input.v1",
        "output.v1",
        callback,
        {name: root / name for name in files},
    )
    receipt = {
        "schema": "product-validated-builds.v1",
        "repository_root": str(root),
        "sources": files,
        "artifacts": {},
        "evidence": {},
        "plugin_build_hash": "a" * 64,
    }
    receipt_path = root / ("prior-" + version + ".json")
    raw = json.dumps(receipt).encode()
    receipt_path.write_bytes(raw)
    result = prepare_component_archive(
        repository_root=root,
        prior_receipt_path=receipt_path,
        prior_receipt_sha256=sha(raw),
        installed=(installed,),
        output_directory=root / "packages/kernel/market_component_versions" / version,
    )
    private.discard()
    return result


def run_probe(settings, stage):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/market_risk/component_restart_probe.py"),
            str(settings),
            stage,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=100,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_old_approved_function_rollback_after_real_process_restart(tmp_path):
    first = fixture_version(tmp_path, "one", 2)

    # Full process termination before new source bytes and a new release receipt.
    def settings_for(archives):
        source_map = {
            name: digest
            for archive in archives
            for name, digest in archive["source_additions"].items()
        }
        raw = json.dumps(
            {
                "schema": "product-validated-builds.v1",
                "repository_root": str(tmp_path),
                "sources": source_map,
            }
        ).encode()
        receipt = tmp_path / "current-release.json"
        receipt.write_bytes(raw)
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "root": str(tmp_path),
                    "receipt": str(receipt),
                    "archives": [
                        {
                            key: value
                            for key, value in item.items()
                            if key in ("index_ref", "index_sha256")
                        }
                        for item in archives
                    ],
                }
            )
        )
        return settings, receipt

    settings, _ = settings_for([first])
    before = run_probe(settings, "first")
    second = fixture_version(tmp_path, "two", 3)
    settings, receipt = settings_for([first, second])
    after = run_probe(settings, "second")
    assert before["pid"] != after["pid"]
    assert before["output"] == after["output"] == {"value": 8, "version": "one"}
    assert before["head"] == after["head"]
    assert after["audit_count"] > before["audit_count"]
    assert after["receipt"]["selection"]["pointer"]["generation"] == 3
    assert before["receipt"]["selection"]["manifest"] == after["receipt"]["selection"]["manifest"]
    assert not after["receipt"]["trading_authority"]
    receipt_path = (
        ROOT
        / "artifacts/market-risk-implementation-20261007/B4"
        / ("component-restart-process-receipts-" + uuid.uuid4().hex + ".json")
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps({"mechanics_only": True, "first": before, "after_real_restart": after}, indent=2)
    )
    # Corrupt a transitive dependency, not just the callback's source file.
    old_dependency = (
        tmp_path / Path(first["index_ref"]).parent / "root/packages/rollback_fixture/values.py"
    )
    old_dependency.write_text("FACTOR = 999\n")
    with pytest.raises(ValueError, match="component_archive_original_changed"):
        load_component_versions(
            repository_root=tmp_path,
            validated_receipt_path=receipt,
            validated_receipt_sha256=sha(receipt.read_bytes()),
            index_ref=first["index_ref"],
            index_sha256=first["index_sha256"],
        )


def test_actual_twelve_callback_closure_requires_new_managed_release(tmp_path):
    from apps.hub_worker.market_component_bootstrap import declare_market_components

    declarations = declare_market_components()
    assert len(declarations) == 12
    initial = {
        path.relative_to(ROOT).as_posix()
        for declaration in declarations
        for path in declaration.code_files.values()
    }
    payloads = _closure(ROOT, initial)
    inventory = {name: sha(raw) for name, raw in payloads.items()}
    for name, raw in payloads.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    private = _PrivateComponentRelease(tmp_path, inventory, "actual_twelve_prepare")
    originals = tuple(
        InstalledComponent(
            kind=item.kind,
            component_ref=item.component_ref,
            scope_id=item.scope_id,
            version_id=item.version_id,
            input_contract=item.input_contract,
            output_contract=item.output_contract,
            callback=getattr(private.load(item.callback.__module__), item.callback.__name__),
            code_files={
                name: tmp_path / path.relative_to(ROOT) for name, path in item.code_files.items()
            },
            parameters=item.parameters,
        )
        for item in declarations
    )
    source_map_path = tmp_path / "managed-source-inventory.json"
    source_map_path.write_text(json.dumps(inventory))
    prepared = prepare_component_archive(
        repository_root=tmp_path,
        installed=originals,
        candidate_source_map_path=source_map_path,
        candidate_source_map_sha256=sha(source_map_path.read_bytes()),
        output_directory=tmp_path / "packages/kernel/market_component_versions/current-candidate",
    )
    private.discard()
    assert prepared["source_binding_mode"] == "managed_candidate_inventory"
    # Preparation itself does not grant installation. A managed new receipt must
    # bind the index, source-binding inventory and every archived original.
    receipt = tmp_path / "validated-build.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "product-validated-builds.v1",
                "repository_root": str(tmp_path),
                "sources": {},
            }
        )
    )
    kwargs = dict(
        repository_root=tmp_path,
        validated_receipt_path=receipt,
        index_ref=prepared["index_ref"],
        index_sha256=prepared["index_sha256"],
    )
    with pytest.raises(ValueError, match="component_archive_index_unbound"):
        load_component_versions(**kwargs, validated_receipt_sha256=sha(receipt.read_bytes()))
    receipt.write_text(
        json.dumps(
            {
                "schema": "product-validated-builds.v1",
                "repository_root": str(tmp_path),
                "sources": prepared["source_additions"],
            }
        )
    )
    assert (
        load_component_versions(
            **kwargs,
            validated_receipt_sha256=sha(receipt.read_bytes()),
            already_installed=declarations,
        )
        == ()
    )
    restored = load_component_versions(**kwargs, validated_receipt_sha256=sha(receipt.read_bytes()))
    assert tuple(item.manifest() for item in restored) == tuple(
        item.manifest() for item in declarations
    )
    assert all(item.callback.__module__.startswith("_decision_hub_component_") for item in restored)
    # Plain JSON output crosses versions; archived types never enter current DB.
    parser = next(item for item in restored if item.kind == "source_parser")
    scope = __import__(
        "packages.kernel.decision_hub_kernel.ports.market_path", fromlist=["spot_scope"]
    ).spot_scope("BTC-USDT")
    payload = {
        "path": {
            "scope": scope.model_dump(mode="json"),
            "window_start_at": "2026-10-08T00:00:00Z",
            "window_end_at": "2026-10-08T00:01:00Z",
            "candles": [],
            "page_count": 0,
            "request_count": 0,
            "status": "partial",
            "semantic_hash": "a" * 64,
            "manifest_hash": "b" * 64,
        }
    }
    assert parser.callback(payload, {}) == declarations[0].callback(payload, {})
    parser.verify()
