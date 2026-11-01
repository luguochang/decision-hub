"""Validate source-local development before any database or publisher writes."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_PREFIX = "DECISION_HUB_"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)
_PRIVATE = re.compile(r"DECISION_HUB_(?:HARNESS_.*(?:CONFIG|SHA256)|HISTORY_.*(?:CONFIG|SHA256))\Z")
_RELEASE_FLAGS = (
    "SPOT_RESEARCH_ENABLED", "ACTION_PRODUCT_ENABLED", "ACTION_REVIEW_ENABLED",
    "ACTION_EXECUTION_ENABLED", "RESEARCH_LEARNING_ENABLED",
    "DURABLE_SKILL_CALLBACKS_ENABLED", "RESEARCH_SKILLS_ENABLED",
)


@dataclass(frozen=True)
class DevelopmentRuntime:
    root: Path
    data_dir: Path
    checkpoint_path: Path
    publication_dir: Path


def _path(value: str) -> Path:
    if not value or any(
        part in {".", ".."} or part.endswith((".", " "))
        for part in re.split(r"[\\/]", value) if part
    ):
        raise ValueError("development_path_invalid")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("development_path_invalid")
    return path


def _plain_path(path: Path) -> None:
    for current in (*reversed(path.parents), path):
        try:
            observed = current.lstat()
        except FileNotFoundError:
            continue
        if (current.is_symlink() or getattr(observed, "st_file_attributes", 0) & 0x400
                or stat.S_ISREG(observed.st_mode) and observed.st_nlink > 1):
            raise ValueError("development_path_linked")
        if current != path and not stat.S_ISDIR(observed.st_mode):
            raise ValueError("development_path_invalid")


def _plain_tree(path: Path) -> None:
    _plain_path(path)
    # DSH profiles have SDK dependency junctions. Only data/runtime are writable
    # by these Python services; the launcher separately authenticates the home.
    for directory, directories, files in os.walk(path, followlinks=False):
        for name in (*directories, *files):
            _plain_path(Path(directory) / name)


def _checked_paths(
    repository: Path, root: Path, environment: Mapping[str, str],
) -> DevelopmentRuntime:
    data = root / "data"
    checkpoint = data / "checkpoints/decision_graph.sqlite3"
    publication = repository.parent / ".decision-hub-development" / root.name / "publication"
    expected = {
        "DATA_DIR": data, "DSH_HOME": root / "dsh-home",
        "CHECKPOINT_PATH": checkpoint, "PUBLICATION_DATA_DIR": publication,
        "DOCUMENT_BUNDLE_ROOT": data / "document-bundles",
        "SOURCE_ACQUISITION_ROOT": data / "source-acquisition",
    }
    for name, path in expected.items():
        if _path(environment.get(_PREFIX + name, "")) != path:
            raise ValueError("development_path_mismatch")
        _plain_path(path)
        if path.exists() and not (path.is_file() if name == "CHECKPOINT_PATH" else path.is_dir()):
            raise ValueError("development_path_invalid")
    for path in (root, data, root / "runtime"):
        _plain_path(path)
        if path.exists() and not path.is_dir():
            raise ValueError("development_path_invalid")
    _plain_tree(data)
    _plain_tree(root / "runtime")
    return DevelopmentRuntime(root, data, checkpoint, publication)


def development_runtime_from_environment(
    repository_root: Path, *, environment: Mapping[str, str] | None = None,
) -> DevelopmentRuntime | None:
    selected = os.environ if environment is None else environment
    mode = selected.get(_PREFIX + "DEVELOPMENT_MODE", "0")
    if mode not in {"0", "1"}:
        raise ValueError("development_mode_invalid")
    if mode == "0":
        return None
    repository = repository_root.absolute()
    root = _path(selected.get(_PREFIX + "DEVELOPMENT_ROOT", ""))
    if (root.parent != repository / "data/development" or _NAME.fullmatch(root.name) is None
            or _DEVICE.fullmatch(root.name) is not None):
        raise ValueError("development_root_invalid")
    if any(selected.get(_PREFIX + name, "") for name in (
        "RETAINED_RECIPE_RECEIPT", "RETAINED_RECIPE_RECEIPT_SHA256",
    )) or any(value and _PRIVATE.fullmatch(name) for name, value in selected.items()):
        raise ValueError("development_private_policy_forbidden")
    if any(selected.get(_PREFIX + name, "0") != "0" for name in _RELEASE_FLAGS):
        raise ValueError("development_release_features_forbidden")
    settings = selected.get(_PREFIX + "SETTINGS_PRIVATE_CONFIG", "")
    settings_sha = selected.get(_PREFIX + "SETTINGS_PRIVATE_SHA256", "")
    if settings or settings_sha:
        if (_path(settings) != root / "runtime/private-settings/owner-launch.json"
                or re.fullmatch(r"[a-f0-9]{64}", settings_sha) is None):
            raise ValueError("development_private_policy_forbidden")
        _plain_path(Path(settings))
    return _checked_paths(repository, root, selected)
