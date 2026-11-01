"""Back up/rehearse restoring Hub files, SQLite checkpoints and DSH session state.

Stop the managed product first. Restore always targets a new directory; existing
business data and credentials are never replaced by this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

from tools.ops.database import backup, integrity


def native_path(path: Path) -> Path:
    resolved = path.resolve()
    if os.name == "nt" and not str(resolved).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(resolved))
    return resolved


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root: Path):
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            path = Path(folder) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"asset_snapshot_external_directory:{path}")
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_symlink():
                raise ValueError(f"asset_snapshot_external_file:{path}")
            if not name.endswith(("-wal", "-shm", "-journal")):
                yield path


def ensure_offline(ports=(8047, 3097, 8037, 8038)) -> None:
    for port in ports:
        with socket.socket() as connection:
            connection.settimeout(0.5)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"product_must_be_stopped:port_{port}")


def snapshot(hub: Path, dsh: Path, output: Path, extra_dsh: tuple[Path, ...] = ()) -> dict:
    ensure_offline()
    hub, dsh, output = map(native_path, (hub, dsh, output))
    roots = {
        "hub": hub.resolve(),
        "dsh/sessions": (dsh / "sessions").resolve(),
        "dsh/storages": (dsh / "storages").resolve(),
    }
    for index, legacy in enumerate(extra_dsh):
        root = native_path(legacy)
        roots[f"dsh_legacy/{index}/sessions"] = root / "sessions"
        if (root / "storages").is_dir():
            roots[f"dsh_legacy/{index}/storages"] = root / "storages"
    for root in roots.values():
        if not root.is_dir():
            raise ValueError(f"asset_root_missing:{root}")
        if output.resolve().is_relative_to(root):
            raise ValueError("snapshot_must_be_outside_source")
    output.mkdir(parents=True, exist_ok=False)
    records = []
    started = datetime.now(UTC).isoformat()
    for label, root in roots.items():
        for source in inventory(root):
            relative = Path(label) / source.relative_to(root)
            target = output / "files" / relative
            before = source.stat()
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix in {".sqlite", ".sqlite3", ".db"} and before.st_size:
                backup(source, target)
                if not integrity(target):
                    raise RuntimeError(f"asset_database_invalid:{relative}")
            else:
                shutil.copy2(source, target)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"asset_changed_during_snapshot:{relative}")
            records.append(
                {
                    "path": relative.as_posix(),
                    "bytes": target.stat().st_size,
                    "sha256": digest(target),
                }
            )
    ensure_offline()
    manifest = {
        "schema_version": "product-assets.v1",
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "roots": {label: str(root) for label, root in roots.items()},
        "files": records,
        "runtime_prerequisites": [
            "Existing DSH credentials/.env and provider authorization",
            "Repository build and launcher presets",
            "Original root paths for an in-place recovery; relocated references require mapping",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def verified_files(snapshot_dir: Path):
    snapshot_dir = native_path(snapshot_dir)
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        root = (snapshot_dir / "files").resolve()
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or relative.is_absolute() or source == root:
            raise ValueError("asset_manifest_path_escape")
        if digest(source) != entry["sha256"]:
            raise ValueError(f"asset_hash_mismatch:{relative}")
        yield relative, source


def restore(snapshot_dir: Path, output: Path) -> int:
    snapshot_dir, output = map(native_path, (snapshot_dir, output))
    # Validate the entire archive before producing a recovery tree.
    files = list(verified_files(snapshot_dir))
    output.mkdir(parents=True, exist_ok=False)
    for relative, source in files:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != digest(source):
            raise RuntimeError(f"asset_restore_hash_mismatch:{relative}")
        if (
            source.suffix in {".sqlite", ".sqlite3", ".db"}
            and source.stat().st_size
            and not integrity(target)
        ):
            raise RuntimeError(f"asset_restore_database_invalid:{relative}")
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    take = sub.add_parser("snapshot")
    take.add_argument("--hub", type=Path, required=True)
    take.add_argument("--dsh", type=Path, required=True)
    take.add_argument("--output", type=Path, required=True)
    take.add_argument("--extra-dsh-home", type=Path, action="append", default=[])
    recover = sub.add_parser("restore")
    recover.add_argument("--snapshot", type=Path, required=True)
    recover.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count = (
        len(snapshot(args.hub, args.dsh, args.output, tuple(args.extra_dsh_home))["files"])
        if args.action == "snapshot"
        else restore(args.snapshot, args.output)
    )
    print(json.dumps({"action": args.action, "files": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
