import json
import sqlite3

import pytest

from tools.ops.product_assets import restore, snapshot


def test_snapshot_restores_business_checkpoint_sessions_and_rejects_tampering(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tools.ops.product_assets.ensure_offline", lambda: None)
    hub, dsh, archive, restored = [
        tmp_path / name for name in ("hub", "dsh", "archive", "restored")
    ]
    for label in ("db", "checkpoints", "documents"):
        (hub / label).mkdir(parents=True)
    for label in ("sessions", "storages"):
        (dsh / label).mkdir(parents=True)
    for name in ("db/business.sqlite3", "checkpoints/graph.sqlite3"):
        with sqlite3.connect(hub / name) as db:
            db.execute("create table marker(value text)")
            db.execute("insert into marker values ('frozen')")
    (hub / "documents/source.txt").write_text("official source", encoding="utf-8")
    (dsh / "sessions/session.jsonl").write_text('{"run":"one"}\n', encoding="utf-8")
    (dsh / "storages/workspace.json").write_text("{}", encoding="utf-8")
    (dsh / ".env").write_text("SECRET=not-in-archive", encoding="utf-8")
    result = snapshot(hub, dsh, archive)
    assert len(result["files"]) == 5
    assert restore(archive, restored) == 5
    assert not (restored / "dsh/.env").exists()
    for name in ("db/business.sqlite3", "checkpoints/graph.sqlite3"):
        with sqlite3.connect(restored / "hub" / name) as db:
            assert db.execute("select value from marker").fetchone() == ("frozen",)
    assert (restored / "dsh/sessions/session.jsonl").read_text() == '{"run":"one"}\n'
    with pytest.raises(FileExistsError):
        restore(archive, restored)
    (archive / "files/hub/documents/source.txt").write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="hash_mismatch"):
        restore(archive, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    manifest = json.loads((archive / "manifest.json").read_text())
    manifest["files"][0]["path"] = "../../outside"
    (archive / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="path_escape"):
        restore(archive, tmp_path / "escaped")
