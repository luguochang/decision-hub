from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_HELPER = ROOT / "infra" / "dsh" / "home-lock.sh"


def _acquire(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; dsh_acquire_home_lock "$2"',
            "dsh-home-lock-test",
            str(LOCK_HELPER),
            str(home),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_dsh_home_lock_rejects_a_second_live_owner(tmp_path: Path) -> None:
    home = tmp_path / "dsh-home"
    home.mkdir()
    owner = subprocess.Popen(
        [
            "bash",
            "-c",
            'source "$1"; dsh_acquire_home_lock "$2"; printf "ready\\n"; sleep 30',
            "dsh-home-lock-owner",
            str(LOCK_HELPER),
            str(home),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "ready"

        contender = _acquire(home)

        assert contender.returncode == 73
        assert "already owns DSH_WEB_HOME" in contender.stderr
        assert str(home) in contender.stderr
    finally:
        owner.terminate()
        owner.wait(timeout=5)


def test_dsh_home_lock_recovers_a_dead_owner(tmp_path: Path) -> None:
    home = tmp_path / "dsh-home"
    lock_dir = home / ".decision-hub-dsh-web.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner").write_text(
        f"pid=999999\nstarted=stale\nhome={home}\n",
        encoding="utf-8",
    )

    result = _acquire(home)

    assert result.returncode == 0, result.stderr
    metadata = (lock_dir / "owner").read_text(encoding="utf-8")
    assert "started=stale" not in metadata
    assert f"home={home}" in metadata
    assert not list(home.glob(".decision-hub-dsh-web.lock.stale.*"))


def test_dsh_home_lock_recovers_a_reused_pid_signature(tmp_path: Path) -> None:
    home = tmp_path / "dsh-home"
    lock_dir = home / ".decision-hub-dsh-web.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner").write_text(
        f"pid={os.getpid()}\nstarted=not-the-current-process\nhome={home}\n",
        encoding="utf-8",
    )

    result = _acquire(home)

    assert result.returncode == 0, result.stderr
    assert "started=not-the-current-process" not in (
        lock_dir / "owner"
    ).read_text(encoding="utf-8")
