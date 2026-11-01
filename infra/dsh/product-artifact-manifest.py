"""Read and hash the current Vite manifest closure, public copies and Host lib.

Historical dist assets stay on disk. This reader neither builds nor deletes;
the standard receipt producer and its fixed guard authenticate the resulting
map, manifest and source bytes. A manifest is the current trusted build output,
not independent proof of arbitrary outputs deleted before receipt production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

UI = "apps/decision-desk/"
DIST = UI + "dist/"
PUBLIC = UI + "public/"
HOST = "extensions/dsh/decision-hub/lib/"
MANIFEST = DIST + ".vite/manifest.json"
EMITTED = DIST + ".vite/product-artifacts.json"


def _fail(code: str) -> None:
    raise ValueError("product_artifact_" + code)


def _name(value: object) -> str:
    if (not isinstance(value, str) or not value
            or re.search(r'[\\\x00-\x1f<>:"|?*]', value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        _fail("path_invalid")
    return value


def _linked(path: Path, status: object) -> bool:
    return path.is_symlink() or bool(getattr(status, "st_file_attributes", 0) & 0x400)


def _path(root: Path, relative: str, *, directory: bool = False) -> Path:
    current = root
    parts = _name(relative).split("/")
    for index, part in enumerate(parts):
        current = current / part
        status = current.lstat()
        if _linked(current, status):
            _fail("linked_path")
        want_directory = index < len(parts) - 1 or directory
        if (stat.S_ISDIR(status.st_mode) if want_directory else stat.S_ISREG(status.st_mode)) is False:
            _fail("file_type_invalid")
    return current


def _files(root: Path, directory: str) -> list[str]:
    base = _path(root, directory.rstrip("/"), directory=True)
    result: list[str] = []
    pending = [base]
    while pending:
        parent = pending.pop()
        for item in parent.iterdir():
            status = item.lstat()
            if _linked(item, status):
                _fail("linked_path")
            if stat.S_ISDIR(status.st_mode):
                pending.append(item)
            elif stat.S_ISREG(status.st_mode):
                result.append(_name(item.relative_to(root).as_posix()))
            else:
                _fail("file_type_invalid")
    return sorted(result)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _strings(entry: dict[str, object], field: str) -> list[str]:
    value = entry.get(field, [])
    # Vite may repeat the same lazy chunk for separate import sites. Every
    # reference is still checked; the final artifact map deduplicates paths.
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail("manifest_list_invalid")
    return value


class _HTMLReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: set[str] = set()
        self.modules: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for attribute in ("src", "href"):
            value = values.get(attribute)
            if not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            # Root-relative and document-relative output URLs have one canonical
            # dist path. Decode once so encoded traversal cannot escape checks.
            relative = _name(unquote(parsed.path.lstrip("/")))
            self.references.add(relative)
            if tag == "script" and values.get("type") == "module" and attribute == "src":
                self.modules.add(relative)


def artifact_map(root: Path, expected_public: dict[str, str]) -> dict[str, str]:
    root = Path(root).absolute()
    root_status = root.lstat()
    if _linked(root, root_status) or not stat.S_ISDIR(root_status.st_mode):
        _fail("root_invalid")
    _path(root, DIST.rstrip("/"), directory=True)
    manifest_path = _path(root, MANIFEST)
    manifest_raw = manifest_path.read_bytes()
    emitted_raw = _path(root, EMITTED).read_bytes()
    emitted = json.loads(emitted_raw, object_pairs_hook=_unique_object)
    if (not isinstance(emitted, dict) or set(emitted) != {"schema_version", "files"}
            or emitted["schema_version"] != "product-emitted-artifacts.v1"
            or not isinstance(emitted["files"], dict) or not emitted["files"]):
        _fail("emitted_inventory_invalid")
    emitted_files = emitted["files"]
    for name, sha in emitted_files.items():
        _name(name)
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
            _fail("emitted_inventory_invalid")
    if (".vite/manifest.json" not in emitted_files or "index.html" not in emitted_files
            or ".vite/product-artifacts.json" in emitted_files):
        _fail("emitted_inventory_invalid")
    manifest = json.loads(manifest_raw, object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or not manifest:
        _fail("manifest_invalid")
    outputs: set[str] = set()
    entries: set[str] = set()
    for key, entry in manifest.items():
        _name(key)
        if not isinstance(entry, dict):
            _fail("manifest_entry_invalid")
        file = _name(entry.get("file"))
        outputs.add(file)
        for flag in ("isEntry", "isDynamicEntry"):
            if flag in entry and not isinstance(entry[flag], bool):
                _fail("manifest_entry_invalid")
        if entry.get("isEntry"):
            entries.add(file)
        for field in ("imports", "dynamicImports"):
            for imported in _strings(entry, field):
                if _name(imported) not in manifest:
                    _fail("manifest_import_missing")
        for field in ("css", "assets"):
            outputs.update(_name(item) for item in _strings(entry, field))
    if not entries:
        _fail("manifest_entry_missing")
    if not outputs <= set(emitted_files):
        _fail("manifest_output_not_emitted")
    public_names = _files(root, PUBLIC)
    if (not isinstance(expected_public, dict) or set(expected_public) != set(public_names)
            or any(not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)
                   for sha in expected_public.values())):
        _fail("public_source_inventory_changed")
    public_outputs = {name.removeprefix(PUBLIC) for name in public_names}
    # Public cannot shadow generated output or the manifest authenticated below.
    if public_outputs & (set(emitted_files) | {".vite/product-artifacts.json"}):
        _fail("public_output_collision")
    html_path = _path(root, DIST + "index.html")
    html_raw = html_path.read_bytes()
    parser = _HTMLReferences()
    parser.feed(html_raw.decode("utf-8"))
    if not parser.modules or not parser.modules <= entries or not entries <= parser.references:
        _fail("html_module_unbound")
    if not parser.references <= outputs | public_outputs:
        _fail("html_dependency_unbound")
    names = {EMITTED}
    names.update(DIST + item for item in set(emitted_files) | public_outputs)
    names.update(_files(root, HOST))
    if not any(name.startswith(HOST) for name in names):
        _fail("host_empty")
    if len({name.casefold() for name in names}) != len(names):
        _fail("path_case_collision")
    result = {name: hashlib.sha256(_path(root, name).read_bytes()).hexdigest()
              for name in sorted(names)}
    if (hashlib.sha256(manifest_raw).hexdigest() != result[MANIFEST]
            or hashlib.sha256(html_raw).hexdigest() != result[DIST + "index.html"]
            or hashlib.sha256(emitted_raw).hexdigest() != result[EMITTED]):
        _fail("output_changed")
    if any(result[DIST + name] != sha for name, sha in emitted_files.items()):
        _fail("emitted_output_changed")
    for name in public_names:
        if (hashlib.sha256(_path(root, name).read_bytes()).hexdigest() != expected_public[name]
                or result[DIST + name.removeprefix(PUBLIC)] != expected_public[name]):
            _fail("public_output_changed")
    # Re-read selected files and current source inventories before handing off.
    # The unchanged standard guard rechecks these hashes at publication/reuse.
    if public_names != _files(root, PUBLIC) or set(name for name in names if name.startswith(HOST)) != set(_files(root, HOST)):
        _fail("output_inventory_changed")
    if any(hashlib.sha256(_path(root, name).read_bytes()).hexdigest() != sha
           for name, sha in result.items()):
        _fail("output_changed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args()
    expected_public = json.loads(sys.stdin.read(), object_pairs_hook=_unique_object)
    print(json.dumps(artifact_map(args.repository_root, expected_public), sort_keys=True))


if __name__ == "__main__":
    main()
