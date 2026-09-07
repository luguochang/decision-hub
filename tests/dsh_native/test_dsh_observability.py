from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_observability_lock_is_exact_and_aligned_with_pinned_dsh() -> None:
    lock = json.loads(
        (ROOT / "infra" / "dsh" / "observability" / "plugin.lock.json").read_text(
            encoding="utf-8"
        )
    )
    upstream = json.loads((ROOT / "infra" / "dsh" / "upstream.lock.json").read_text())

    assert lock["package"] == "@loongsuite/dsh-plugin"
    assert lock["version"] == "0.1.2"
    assert lock["license"] == "Apache-2.0"
    assert lock["compatibility_status"].startswith("exact_canary_passed")
    assert lock["dsh_source_version"] == upstream["source_version"]
    assert lock["dsh_source_commit"] == upstream["commit"]
    assert lock["integrity"].startswith("sha512-")


def test_observability_is_opt_in_and_uses_official_profile_command() -> None:
    launcher = (ROOT / "infra" / "dsh" / "run-web.sh").read_text(encoding="utf-8")
    canary = (
        ROOT / "infra" / "dsh" / "observability" / "run-canary.sh"
    ).read_text(encoding="utf-8")

    assert 'DSH_OBSERVABILITY_ENABLED:-0' in launcher
    assert "plugin --profile web add --save-exact" in launcher
    assert "@loongsuite/dsh-plugin" in launcher
    assert "captureContent=false" in (
        ROOT / "infra" / "dsh" / "observability" / "README.md"
    ).read_text(encoding="utf-8")
    assert "DSH_OBSERVABILITY_ENABLED=1" in canary
    assert "--exporter-down" in canary
