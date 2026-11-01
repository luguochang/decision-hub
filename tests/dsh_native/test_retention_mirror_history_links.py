"""Temporary retained originals reuse storage without sharing mutable sources."""
import errno
import hashlib
import os
from pathlib import Path
import runpy

import pytest


@pytest.fixture
def publisher():
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "infra/dsh/prepare-product-retention.py"))
    if "publish_mirror_original" in module:
        return module["publish_mirror_original"]
    # Exercise the existing real byte-copy publisher for the RED baseline.
    return lambda target, source, expected, name: module["publish"](target, module["read"](source))


def original(tmp_path, name, body=b"authenticated old source"):
    source = tmp_path / "installed" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(body)
    return source, tmp_path / "original-view" / name, hashlib.sha256(body).hexdigest()


def test_history_names_reuse_the_authenticated_file_storage(tmp_path, publisher):
    for prefix in (
        "packages/kernel/research_recipe_versions/old/root/",
        "packages/kernel/market_component_versions/old/",
        "packages/kernel/research_skill_versions/old/root/",
        "packages/kernel/decision_hub_kernel/application/context_selector_versions/old/",
    ):
        name = prefix + "original.py"
        source, target, expected = original(tmp_path, name)
        publisher(target, source, expected, name)
        assert os.path.samefile(source, target)
        assert source.stat().st_nlink >= 2
        assert target.read_bytes() == b"authenticated old source"


def test_wrong_original_hash_is_rejected_before_any_destination_exists(tmp_path, publisher):
    name = "packages/kernel/research_recipe_versions/old/root/original.py"
    source, target, expected = original(tmp_path, name)
    source.write_bytes(b"unvalidated current source")
    with pytest.raises(ValueError, match="retention_original_source_changed"):
        publisher(target, source, expected, name)
    assert not target.exists()


def test_atomic_replacement_of_original_leaves_retained_old_bytes(tmp_path, publisher):
    name = "packages/kernel/research_recipe_versions/old/root/original.py"
    source, target, expected = original(tmp_path, name)
    publisher(target, source, expected, name)
    replacement = source.with_name("new-epoch.py")
    replacement.write_bytes(b"new epoch source")
    os.replace(replacement, source)
    assert source.read_bytes() == b"new epoch source"
    assert target.read_bytes() == b"authenticated old source"
    assert not os.path.samefile(source, target)


def test_mutable_sources_and_cross_volume_history_use_independent_copies(tmp_path, publisher, monkeypatch):
    name = "packages/kernel/decision_hub_kernel/application/current.py"
    source, target, expected = original(tmp_path, name)
    publisher(target, source, expected, name)
    assert not os.path.samefile(source, target)
    source.write_bytes(b"in place mutable edit")
    assert target.read_bytes() == b"authenticated old source"

    name = "packages/kernel/research_recipe_versions/old/root/cross-volume.py"
    source, target, expected = original(tmp_path, name)
    real_link = os.link

    def cross_volume_only_for_the_original(candidate, destination):
        if Path(candidate) == source or str(candidate).removeprefix("\\\\?\\") == str(source):
            raise OSError(errno.EXDEV, "different volume")
        return real_link(candidate, destination)

    monkeypatch.setattr(os, "link", cross_volume_only_for_the_original)
    publisher(target, source, expected, name)
    assert not os.path.samefile(source, target)
    assert target.read_bytes() == b"authenticated old source"
