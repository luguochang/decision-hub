"""Finite private transport controls; no real corpus, CLI, model fit or finance P."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import history_forecast_transfer39 as transfer
from packages.kernel.decision_hub_kernel.application import history_model_freeze_v3 as freeze
from tests.market_risk.test_history_model_native_copy45 import row, source


def sha(body):
    return hashlib.sha256(body).hexdigest()


def package(tmp_path, *, version=4, mutate=None, extra=None, compression=None):
    root = tmp_path / ("transport" + str(version))
    root.mkdir()
    native = root / "input.sqlite3"
    table = "market_history_v2_jobs"
    columns = transfer.FULL_SCHEMA_COLUMNS[table]
    # Includes every storage class, signed REAL zero and the seven extra job fields.
    full = ("job", "epoch", "plan", "scope", "start", "end", b"\0cursor", 700,
            -0.0, None, 0, 2, "state", b"lease", 1.0, None, "尾\0🙂" * 180000)
    with sqlite3.connect(native) as writer:
        writer.execute(f'CREATE TABLE "{table}" (' + ",".join(f'"{name}"' for name in columns) + ")")
        writer.execute(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in full)})', full)
        schemas = ((table, tuple(tuple(value) for value in writer.execute(f'PRAGMA table_xinfo("{table}")'))),)
    image = native.read_bytes()
    original = "原始\0🙂".encode() * 180000
    original_hash = sha(original)
    model_id = "hfit2_" + "a" * 48
    # A container-only placeholder; the genuine typed models are covered by the
    # unchanged existing prepare/publish independent-confirmation integration.
    model = b"{}"
    manifest = dict(schema_version=f"history-model-frozen-bundle.v{version}",
                    owner_claim="private", request_id="private46", fixture_mode=True,
                    schemas=schemas, row_counts={table: 1}, row_keys={table: ["job"]},
                    native_format="sqlite-native-unaffinitized.v1",
                    native_snapshot=("native.sqlite3", sha(image), len(image)),
                    files=[("logical-original", original_hash, len(original), "originals/" + original_hash)],
                    raw_aliases=[("logical-alias", original_hash, "originals/" + original_hash)],
                    source_files=[], models=[(model_id, sha(model), "models/" + model_id + ".json",
                                              "private-model-original", sha(model))],
                    financial_qualified=False)
    if version == 4:
        manifest["transport_limits"] = freeze._transport_limits46()
    if mutate is not None:
        mutate(manifest)
    manifest_body = freeze.encoded(manifest)
    path = root / "bundle.zip"
    method = (zipfile.ZIP_DEFLATED if version == 4 else zipfile.ZIP_STORED) if compression is None else compression
    with zipfile.ZipFile(path, "w", compression=method, compresslevel=1 if method == zipfile.ZIP_DEFLATED else None) as writer:
        writer.writestr("manifest.json", manifest_body)
        writer.writestr("native.sqlite3", image)
        writer.writestr("originals/" + original_hash, original)
        writer.writestr("models/" + model_id + ".json", model)
        if extra is not None:
            writer.writestr(extra, b"unexpected")
    return SimpleNamespace(root=root, path=path, manifest=manifest, manifest_hash=sha(manifest_body),
                           image=image, original=original, full=full, table=table, digest=sha(path.read_bytes()))


def reader(item, check=lambda: None):
    return freeze.FrozenModelBundleV3(item.root, item.path, item.digest, item.manifest_hash, checkpoint=check)


def test_v4_full_native_17_fields_alias_original_and_old_v3_compatibility(tmp_path):
    assert (freeze.PACK_MAX, freeze.MAX_BYTES, freeze.V4_NATIVE_MAX, freeze.V4_UNPACK_MAX,
            freeze.V4_STAGING_MAX, freeze.MAX_ROWS, freeze.NATIVE_BATCH_BYTES, freeze.MEMORY_CHUNK) == (
        512 * 1024**2, 512 * 1024**2, 768 * 1024**2, 1024**3, 2 * 1024**3,
        300000, 16 * 1024**2, 1024**2)
    for version in (4, 3):
        item = package(tmp_path, version=version)
        bundle = reader(item)
        assert bundle.read("logical-original") == item.original == bundle.read("logical-alias")
        with bundle.snapshot_session() as session:
            actual = tuple(session.connection().exec_driver_sql(f'SELECT * FROM "{item.table}"').one())
            assert transfer.native_equal39(actual, item.full)
            assert len(actual) == 17
            assert session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() == 1
            assert session.connection().connection.driver_connection.total_changes == 0
        assert not tuple((item.root / "snapshots").glob("*.partial"))
        with zipfile.ZipFile(item.path) as archive:
            logical = sum(info.file_size for info in archive.infolist())
            assert all(info.compress_type == (zipfile.ZIP_DEFLATED if version == 4 else zipfile.ZIP_STORED)
                       for info in archive.infolist())
        print("CONTROL46 " + json.dumps(dict(version=version, native_bytes=len(item.image),
              unique_unpacked_bytes=logical, packed_bytes=item.path.stat().st_size,
              typed_fields=17, source_writes=0, real_model=False)))


@pytest.mark.parametrize("bad", ["limits", "downgrade", "extra", "compression", "trailing"])
def test_v4_strict_contract_no_downgrade_or_extra_transport(tmp_path, bad):
    mutation = None
    if bad == "limits":
        mutation = lambda manifest: manifest["transport_limits"].update(native_bytes_max=freeze.V4_NATIVE_MAX + 1)
    elif bad == "downgrade":
        mutation = lambda manifest: manifest.update(schema_version="history-model-frozen-bundle.v3")
    item = package(tmp_path, mutate=mutation, extra="unexpected" if bad == "extra" else None,
                   compression=zipfile.ZIP_STORED if bad == "compression" else None)
    if bad == "trailing":
        with item.path.open("ab") as handle:
            handle.write(b"trailing")
        item.digest = sha(item.path.read_bytes())
    with pytest.raises(ValueError):
        reader(item)
    assert not (item.root / "snapshots").exists()


def test_v3_original_logical_cap_and_v4_native_unpacked_bounds(tmp_path, monkeypatch):
    old = package(tmp_path, version=3)
    new = package(tmp_path, version=4)
    # Scaled negative fixture; the production constants are asserted separately.
    monkeypatch.setattr(freeze, "MAX_BYTES", len(old.image) - 1)
    with pytest.raises(ValueError, match="bundle_members_invalid"):
        reader(old)
    assert reader(new).manifest["schema_version"] == "history-model-frozen-bundle.v4"
    monkeypatch.undo()
    monkeypatch.setattr(freeze, "V4_NATIVE_MAX", len(new.image) - 1)
    with pytest.raises(ValueError, match="transport46_contract_invalid"):
        reader(new)  # Manifest cannot authorize a code-exceeding resource policy.
    monkeypatch.undo()
    monkeypatch.setattr(freeze, "V4_UNPACK_MAX", len(new.image))
    with pytest.raises(ValueError, match="bundle_members_invalid"):
        reader(new)


def test_actual_stream_count_and_original_hash_not_only_declared_length():
    class LyingArchive:
        def getinfo(self, _member):
            return SimpleNamespace(file_size=8)

        def open(self, _info):
            return io.BytesIO(b"12345678unexpected")

    output = io.BytesIO()
    with pytest.raises(freeze.HistoryModelQualificationBoundV3Error, match="unpacked_bound46_reached"):
        freeze._stream_member46(LyingArchive(), "member", sha(b"12345678"), 8, 8, lambda: None, output=output)
    assert output.getvalue() == b""
    class AlteredArchive(LyingArchive):
        def open(self, _info):
            return io.BytesIO(b"12345679")
    with pytest.raises(ValueError, match="member_original46_changed"):
        freeze._stream_member46(AlteredArchive(), "member", sha(b"12345678"), 8, 8, lambda: None)


def test_cancel_partial_cleanup_and_existing_cache_conflict_no_overwrite(tmp_path):
    item = package(tmp_path)
    cancellation = {"active": False}
    def check():
        if cancellation["active"]:
            raise ValueError("private_cancel46")
    bundle = reader(item, check)
    target = item.root / "snapshots" / (sha(item.image) + ".sqlite3")
    cancellation["active"] = True
    with pytest.raises(ValueError, match="private_cancel46"):
        with bundle.snapshot_session():
            pass
    assert not target.exists() and not tuple(target.parent.glob("*.partial"))
    cancellation["active"] = False
    target.write_bytes(b"previous-invalid-cache")
    with pytest.raises(ValueError, match="frozen_native_snapshot_changed"):
        with bundle.snapshot_session():
            pass
    assert target.read_bytes() == b"previous-invalid-cache"
    assert not tuple(target.parent.glob("*.partial"))
    assert sha(item.path.read_bytes()) == item.digest


def test_native_row_and_sqlite_page_allocation_limits_source_unchanged(tmp_path):
    with source(tmp_path, (row("private", "x" * 60000),)) as (session, schemas, keys, native):
        with pytest.raises(freeze.HistoryModelQualificationBoundV3Error, match="native_row_bound46_reached"):
            freeze._copy_native_snapshot45(session, schemas, keys, tmp_path / "row.sqlite3",
                                          lambda: None, native_maximum=32768)
        with pytest.raises(freeze.HistoryModelQualificationBoundV3Error, match="native_allocation_bound46_reached"):
            freeze._copy_native_snapshot45(session, schemas, keys, tmp_path / "page.sqlite3",
                                          lambda: None, native_maximum=65536)
        assert native.total_changes == 0


def test_packed_write_staging_count_and_cleanup_only_own_new_stage(tmp_path, monkeypatch):
    stage = tmp_path / ("prepare46_" + "a" * 32)
    stage.mkdir()
    (stage / "native.sqlite3").write_bytes(b"n" * 32)
    path = stage / "bundle.zip"
    with path.open("w+b") as handle:
        writer = freeze._BoundedPackWriter46(handle, stage, lambda: None)
        writer.write(b"p" * 16)
        assert writer.peak == 16
        observed = freeze._staging_limit46(stage, lambda: None, packed_bytes=writer.peak)
        assert observed == 48
        monkeypatch.setattr(freeze, "PACK_MAX", 17)
        with pytest.raises(freeze.HistoryModelQualificationBoundV3Error, match="packed_bound46_reached"):
            writer.write(b"too-big")
        assert handle.tell() == 16
        monkeypatch.setattr(freeze, "V4_STAGING_MAX", 48)
        assert freeze._staging_limit46(stage, lambda: None, packed_bytes=16) == 48
        with pytest.raises(freeze.HistoryModelQualificationBoundV3Error, match="staging_bound46_reached"):
            freeze._staging_limit46(stage, lambda: None, packed_bytes=17)
    old = tmp_path / ("prepare_" + "b" * 32)
    old.mkdir()
    (old / "native.sqlite3").write_bytes(b"old44-45-must-remain")
    freeze._cleanup_staging46(stage, tmp_path)
    assert not stage.exists()
    with pytest.raises(ValueError, match="cleanup46_identity_invalid"):
        freeze._cleanup_staging46(old, tmp_path)
    assert (old / "native.sqlite3").read_bytes() == b"old44-45-must-remain"
    print("CONTROL46 " + json.dumps(dict(staging_guard_observed_peak_bytes=48,
          staging_scope="own new stage only", packed_writer_exact_end=16,
          old_failed_stage_unchanged=True, scaled_bounds_fixture=True)))


def test_actual_writer_rejects_grown_source_before_member_write_and_cleans_own_stage(tmp_path, monkeypatch):
    grown = tmp_path / "grown-original"
    declared = b"original"
    grown.write_bytes(declared + b"unexpected-growth" * 100)
    original_hash = sha(declared)
    writes = []
    original_open = zipfile.ZipFile.open

    class Spy:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *arguments):
            self.handle.close()
        def write(self, body):
            writes.append(len(body))
            return self.handle.write(body)

    def open_member(self, name, mode="r", pwd=None, *, force_zip64=False):
        handle = original_open(self, name, mode, pwd, force_zip64=force_zip64)
        return Spy(handle) if name == "originals/" + original_hash and mode == "w" else handle

    monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    publisher = object.__new__(freeze.HistoryModelFreezeV3Publisher)
    publisher.root = tmp_path / "qualification-root"
    publisher.forecasts = SimpleNamespace(owner_claim="private", originals=SimpleNamespace(fixture_mode=True))
    fake_model = SimpleNamespace(model_id="hfit2_" + "a" * 48, payload_hash=sha(b"{}"))
    # Direct internal transport control, not an issued model proof or qualification.
    group = SimpleNamespace(table="market_history_v2_models", rows=(("",) * 8 + ("private-model", sha(b"{}")),))
    proof = SimpleNamespace(rows=(group,), model_bytes=b"{}")
    with source(tmp_path, (row("private", "native"),)) as (session, schemas, keys, native):
        with pytest.raises(ValueError, match="original_changed_while_copying"):
            publisher._freeze_bundle46(session, schemas, keys, {str(grown): (original_hash, len(declared))},
                ((None, proof, fake_model),), (), "private46", lambda: None, None)
        assert native.total_changes == 0
    assert writes == []  # The unexpected actual bytes never enter the ZIP member.
    assert not tuple(publisher.root.glob("prepare46_*"))
    assert not (publisher.root / "bundles").exists()
    assert grown.read_bytes() == declared + b"unexpected-growth" * 100
