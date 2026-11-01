"""DEV lifecycle behavior; controlled native boundaries, no product/model launch."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("LIFECYCLE_SOURCE_ROOT", str(Path(__file__).resolve().parents[2])))
PWSH = os.environ.get(
    "LIFECYCLE_PWSH",
    shutil.which("pwsh")
    or str(
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe"
    ),
)
pytestmark = pytest.mark.skipif(not Path(PWSH).is_file(), reason="PowerShell 7 required")


def q(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def run(body: str) -> dict[str, object]:
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; . "
            + q(ROOT / "infra/dsh/development-runtime.ps1")
            + "; "
            + body,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize(
    "mode,want",
    [
        ("absent", "absent"),
        ("reused", "reused"),
        ("unknown", "unknown"),
        ("alive", "alive"),
    ],
)
def test_lifetime_never_turns_access_failure_into_absence(mode: str, want: str) -> None:
    # Break caught: swallowing Get-Process/StartTime failures as proof of death.
    result = run(
        "$entry=[pscustomobject]@{pid=701;started_ticks='100'}; "
        f"$script:mode={q(mode)}; "
        "function Get-Process { param($Id,$ErrorAction) "
        "if($script:mode -eq 'absent'){ return }; "
        "if($script:mode -eq 'unknown'){throw 'denied'}; "
        "$ticks=if($script:mode -eq 'reused'){200}else{100}; "
        "[pscustomobject]@{StartTime=[datetime]::new($ticks,[DateTimeKind]::Utc)} }; "
        "Get-DevelopmentLifetime -Entry $entry | ConvertTo-Json -Compress"
    )
    assert result["state"] == want


@pytest.mark.parametrize(
    "mode,want",
    [
        ("absent", "absent"),
        ("unknown", "unknown"),
        ("orphan", "residual"),
        ("reused", "absent"),
    ],
)
def test_status_observes_stale_running_without_rewriting_evidence(
    tmp_path: Path,
    mode: str,
    want: str,
) -> None:
    # Break caught: trusting running, ignoring live children or rewriting stale snapshots.
    runtime = tmp_path / "data/development/one/runtime"
    runtime.mkdir(parents=True)
    supervisor = {
        "pid": 701,
        "started_ticks": "100",
        "state": "running",
        "launch_id": "test-launch",
        "development_root": str(runtime.parent),
    }
    entries = [
        {
            "pid": 702,
            "started_ticks": "200",
            "development_root": str(runtime.parent),
            "development_owner_ticks": "100",
            "children": [],
        }
    ]
    (runtime / "supervisor.json").write_text(json.dumps(supervisor), encoding="utf-8")
    (runtime / "processes.json").write_text(json.dumps(entries), encoding="utf-8")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in runtime.iterdir()}
    result = run(
        f"$script:mode={q(mode)}; "
        "function Get-Process { param($Id,$ErrorAction) "
        "if($script:mode -eq 'unknown'){throw 'denied'}; "
        "if($script:mode -eq 'reused'){return "
        "[pscustomobject]@{StartTime=[datetime]::new(999,[DateTimeKind]::Utc)}}; "
        "if($script:mode -eq 'orphan' -and $Id -eq 702){ "
        "return [pscustomobject]@{StartTime=[datetime]::new(200,[DateTimeKind]::Utc)}} }; "
        "function Get-NetTCPConnection { param($State,$ErrorAction) @() }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {q(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Get-DevelopmentStatus -RepositoryRoot {q(tmp_path)} -Layout $layout "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    assert result["recorded_state"] == "running"
    assert result["observed_state"] == want
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in runtime.iterdir()}


def test_terminal_receipt_requires_verified_absence(tmp_path: Path) -> None:
    # Break caught: issuing a stopped receipt despite inaccessible residual identity.
    result = run(
        f"$runtime={q(tmp_path)}; "
        "$supervisor=[pscustomobject]@{pid=701;started_ticks='100';launch_id='test'}; "
        "function Get-Process { throw 'denied' }; "
        "$errorCode=$null; try { Write-DevelopmentStoppedReceipt -RuntimeDirectory $runtime "
        "-Supervisor $supervisor -Targets @($supervisor) } catch { "
        "$errorCode=$_.Exception.Message }; "
        "@{error=$errorCode;receipt=(Test-Path (Join-Path $runtime 'stop-receipt.json'))} "
        "| ConvertTo-Json -Compress"
    )
    assert result == {"error": "development_stop_unconfirmed_state_preserved", "receipt": False}


@pytest.mark.parametrize("live,want", [(True, "running"), (False, "exiting")])
def test_finally_requires_child_absence_and_does_not_claim_supervisor_exit(
    tmp_path: Path,
    live: bool,
    want: str,
) -> None:
    result = run(
        ". " + q(ROOT / "infra/dsh/product-processes.ps1") + "; "
        f"$runtime={q(tmp_path)}; "
        "$supervisor=[pscustomobject]@{pid=701;started_ticks='100';"
        "launch_id='test';state='running'}; "
        "$child=[pscustomobject]@{pid=702;started_ticks='200';children=@()}; "
        f"$script:live={'$true' if live else '$false'}; "
        "function Get-Process { if($script:live){throw 'denied'} }; "
        "$failed=$false; try { Write-DevelopmentCleanupReceipt -RuntimeDirectory $runtime "
        "-Supervisor $supervisor -Entries @($child) } catch { $failed=$true }; "
        "@{failed=$failed;state=$supervisor.state;stopped=(Test-Path (Join-Path $runtime "
        "'stop-receipt.json'))} "
        "| ConvertTo-Json -Compress"
    )
    assert result == {"failed": live, "state": want, "stopped": False}


@pytest.mark.parametrize("graceful", [True, False])
def test_owned_native_dummy_stops_and_only_then_receipts(tmp_path: Path, graceful: bool) -> None:
    # Break caught: forced stop before grace, or success without verified final absence.
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "product-processes.ps1", "stop-dev.ps1",
                 "development-watchdog.ps1"):
        shutil.copyfile(ROOT / "infra/dsh" / name, controls / name)
    launcher = controls / "run-product.ps1"
    launcher.write_text(
        "param([switch]$DevelopmentMode,[string]$DevelopmentRoot)\n"
        "$ErrorActionPreference='Stop'\n"
        ". (Join-Path $PSScriptRoot 'development-runtime.ps1')\n"
        ". (Join-Path $PSScriptRoot 'product-processes.ps1')\n"
        "$repo=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path\n"
        "$layout=Resolve-DevelopmentLayout -RepositoryRoot $repo -DevelopmentRoot "
        "$DevelopmentRoot\n"
        "New-Item -ItemType Directory -Force -Path $layout.runtime | Out-Null\n"
        "$entry=[pscustomobject]@{pid=$PID;started_ticks=(Get-ProductProcessStamp $PID);"
        "state='running';launch_id='dummy';development_root=$layout.root}\n"
        "Write-ProductJson (Join-Path $layout.runtime 'processes.json') @()\n"
        "Write-ProductJson (Join-Path $layout.runtime 'supervisor.json') $entry\n"
        + (
            "while(-not (Test-Path (Join-Path $layout.runtime 'stop.request'))) "
            "{Start-Sleep -Milliseconds 50}\n"
            "Set-Content (Join-Path $layout.runtime 'graceful-seen') yes\n"
            if graceful
            else "Start-Sleep -Seconds 20\n"
        ),
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(launcher),
            "-DevelopmentMode",
            "-DevelopmentRoot",
            "data/development/one",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    runtime = tmp_path / "data/development/one/runtime"
    try:
        deadline = time.monotonic() + 8
        while not (runtime / "supervisor.json").exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.05)
        assert (runtime / "supervisor.json").exists()
        result = subprocess.run(
            [
                PWSH,
                "-NoProfile",
                "-File",
                str(controls / "stop-dev.ps1"),
                "-DevelopmentRoot",
                "data/development/one",
                "-GraceSeconds",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        child.wait(timeout=5)
        assert (runtime / "graceful-seen").exists() is graceful
        receipt = json.loads((runtime / "stop-receipt.json").read_text(encoding="utf-8-sig"))
        assert receipt["state"] == "stopped"
        assert receipt["launch_id"] == "dummy"
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize("denied,want", [(True, "unknown"), (False, "absent")])
def test_foreign_listener_is_never_ownership_or_a_stop_target(
    tmp_path: Path,
    denied: bool,
    want: str,
) -> None:
    runtime = tmp_path / "data/development/one/runtime"
    runtime.mkdir(parents=True)
    (runtime / "supervisor.json").write_text(
        json.dumps(
            {
                "pid": 701,
                "started_ticks": "100",
                "state": "running",
                "development_root": str(runtime.parent),
            }
        ),
        encoding="utf-8",
    )
    (runtime / "processes.json").write_text(
        json.dumps(
            [
                {
                    "pid": 702,
                    "started_ticks": "200",
                    "children": [],
                    "health_probe": {"port": 18047},
                }
            ]
        ),
        encoding="utf-8",
    )
    result = run(
        "function Get-Process { }; "
        f"$script:denied={'$true' if denied else '$false'}; "
        "function Get-NetTCPConnection { if($script:denied){throw 'denied'}; "
        "[pscustomobject]@{LocalPort=18047;OwningProcess=999} }; "
        "function Stop-Process { throw 'must_not_stop' }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {q(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Get-DevelopmentStatus -RepositoryRoot {q(tmp_path)} -Layout $layout "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    assert result["observed_state"] == want
    if not denied:
        assert result["listeners"] == [{"port": 18047, "pid": 999, "ownership": "unverified"}]


def test_force_failure_preserves_running_and_has_no_stopped_receipt(tmp_path: Path) -> None:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "product-processes.ps1", "stop-dev.ps1",
                 "development-watchdog.ps1"):
        shutil.copyfile(ROOT / "infra/dsh" / name, controls / name)
    runtime = tmp_path / "data/development/one/runtime"
    runtime.mkdir(parents=True)
    saved = json.dumps(
        {
            "pid": 701,
            "started_ticks": "100",
            "state": "running",
            "launch_id": "blocked",
            "development_root": str(runtime.parent),
        }
    )
    (runtime / "supervisor.json").write_text(saved, encoding="utf-8")
    (runtime / "processes.json").write_text("[]", encoding="utf-8")
    result = run(
        f"$repository={q(tmp_path)}; "
        "function Get-Process { "
        "$process=[pscustomobject]@{StartTime=[datetime]::new(100,[DateTimeKind]::Utc)}; "
        "$process | Add-Member ScriptMethod Kill {throw 'denied'}; $process }; "
        "function Get-CimInstance { [pscustomobject]@{CommandLine=('pwsh -File '+ "
        "(Join-Path $repository 'infra/dsh/run-product.ps1') + "
        "' -DevelopmentMode -DevelopmentRoot data/development/one')} }; "
        "$code=$null; try { & (Join-Path $repository 'infra/dsh/stop-dev.ps1') "
        "-DevelopmentRoot data/development/one -GraceSeconds 0 } catch "
        "{$code=$_.Exception.Message}; "
        "@{error=$code} | ConvertTo-Json -Compress"
    )
    assert result == {"error": "development_stop_unconfirmed_state_preserved"}
    assert (runtime / "supervisor.json").read_text(encoding="utf-8") == saved
    assert not (runtime / "stop-receipt.json").exists()


@pytest.mark.parametrize("root", ["data/product-local", "data/core-live-20260913", "data/dsh-live"])
def test_status_rejects_formal_roots_without_writing(tmp_path: Path, root: str) -> None:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "product-processes.ps1", "status-dev.ps1"):
        shutil.copyfile(ROOT / "infra/dsh" / name, controls / name)
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(controls / "status-dev.ps1"), "-DevelopmentRoot", root],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "development_root_invalid" in result.stderr
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "ticks,denied,excluded", [(50, False, True), (50, True, False), (300, False, False)]
)
def test_terminal_inventory_excludes_only_proven_foreign_child(
    tmp_path: Path,
    ticks: int,
    denied: bool,
    excluded: bool,
) -> None:
    # Break caught: allowing unknown/orphan leaves to disappear with a dead root,
    # or allowing an untrusted older foreign child to deny stopping the own tree.
    result = run(
        ". " + q(ROOT / "infra/dsh/product-processes.ps1") + "; "
        f"$runtime={q(tmp_path)}; $script:denied={'$true' if denied else '$false'}; "
        "$supervisor=[pscustomobject]@{pid=701;started_ticks='100';"
        "state='running';launch_id='test'}; "
        "$entry=[pscustomobject]@{pid=702;started_ticks='200';children=@("
        f"[pscustomobject]@{{pid=703;started_ticks='{ticks}'}})}}; "
        "function Get-Process { param($Id,$ErrorAction) if($Id -ne 703){return}; "
        "if($script:denied){throw 'denied'}; "
        f"[pscustomobject]@{{StartTime=[datetime]::new({ticks},[DateTimeKind]::Utc)}} }}; "
        "$targets=@(Get-DevelopmentTerminalInventory -Supervisor $supervisor -Entries @($entry)); "
        "$errorCode=$null; try {Write-DevelopmentStoppedReceipt -RuntimeDirectory $runtime "
        "-Supervisor $supervisor -Targets $targets} catch {$errorCode=$_.Exception.Message}; "
        "@{error=$errorCode;receipt=(Test-Path (Join-Path $runtime 'stop-receipt.json'))} "
        "| ConvertTo-Json -Compress"
    )
    assert result == {
        "error": None if excluded else "development_stop_unconfirmed_state_preserved",
        "receipt": excluded,
    }


def test_new_launch_identity_cannot_be_overwritten_by_stale_stop(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.json"
    saved = json.dumps({"pid": 702, "started_ticks": "200", "launch_id": "new"})
    path.write_text(saved, encoding="utf-8")
    result = run(
        ". " + q(ROOT / "infra/dsh/product-processes.ps1") + "; "
        "$old=[pscustomobject]@{pid=701;started_ticks='100';launch_id='old'}; "
        "function Get-Process { }; "
        f"$code=$null; try {{ Write-DevelopmentStoppedReceipt -RuntimeDirectory {q(tmp_path)} "
        "-Supervisor $old -Targets @($old) } catch {$code=$_.Exception.Message}; "
        "@{error=$code} | ConvertTo-Json -Compress"
    )
    assert result == {"error": "development_launch_changed_state_preserved"}
    assert path.read_text(encoding="utf-8") == saved
    assert not (tmp_path / "stop-receipt.json").exists()


def test_dead_supervisor_missing_checkpoint_cannot_receive_stopped_receipt(tmp_path: Path) -> None:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "product-processes.ps1", "stop-dev.ps1",
                 "development-watchdog.ps1"):
        shutil.copyfile(ROOT / "infra/dsh" / name, controls / name)
    runtime = tmp_path / "data/development/one/runtime"
    runtime.mkdir(parents=True)
    saved = json.dumps({"pid": 701, "started_ticks": "100", "state": "running"})
    (runtime / "supervisor.json").write_text(saved, encoding="utf-8")
    result = run(
        "function Get-Process {}; "
        f"$code=$null; try {{ & {q(controls / 'stop-dev.ps1')} "
        "-DevelopmentRoot data/development/one } catch {$code=$_.Exception.Message}; "
        "@{error=$code} | ConvertTo-Json -Compress"
    )
    assert result == {"error": "development_stop_unconfirmed_state_preserved"}
    assert (runtime / "supervisor.json").read_text(encoding="utf-8") == saved
    assert not (runtime / "stop-receipt.json").exists()
