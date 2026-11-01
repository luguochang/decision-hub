"""Pending launch faults and native stop/handoff serialization; no real product."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 required")


def q(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run(root: Path, name: str, body: str) -> tuple[int, str, str]:
    # File outputs avoid Windows inherited capture-pipe handles delaying wait().
    with (root / f"{name}.out").open("w") as out, (root / f"{name}.err").open("w") as err:
        child = subprocess.Popen(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", body],
            stdout=out, stderr=err,
        )
        code = child.wait(timeout=25)
    return (code, (root / f"{name}.out").read_text(encoding="utf-8-sig"),
            (root / f"{name}.err").read_text(encoding="utf-8-sig"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "product-processes.ps1", "ensure-dev-running.ps1",
                 "stop-dev.ps1", "run-dev.ps1", "development-watchdog.ps1"):
        source = ROOT / "infra/dsh" / name
        if source.exists():
            shutil.copyfile(source, controls / name)
    (tmp_path / "data/development/one/runtime").mkdir(parents=True)
    return tmp_path


def test_stop_cannot_report_absence_while_pending_wrapper_can_continue(root: Path) -> None:
    controls = root / "infra/dsh"
    (controls / "run-dev.ps1").write_text(
        "param([string]$DevelopmentRoot,[string]$WatchdogIntentId)\n"
        "$deadline=[datetime]::UtcNow.AddSeconds(20)\n"
        "while([datetime]::UtcNow -lt $deadline -and -not (Test-Path "
        "(Join-Path $PSScriptRoot '../../release'))) {Start-Sleep -Milliseconds 20}\n"
        "if(Test-Path (Join-Path $PSScriptRoot '../../release')) {"
        "Set-Content (Join-Path $PSScriptRoot '../../continued') yes}\n", encoding="utf-8",
    )
    try:
        code, out, err = run(root, "ensure", "function Get-NetTCPConnection { }; & "
                             + q(controls / "ensure-dev-running.ps1")
                             + " -DevelopmentRoot data/development/one")
        assert code == 0, out + err
        code, out, err = run(root, "stop", "function Get-NetTCPConnection { }; & "
                             + q(controls / "stop-dev.ps1")
                             + " -DevelopmentRoot data/development/one -GraceSeconds 0")
        assert code != 0, "stop must not confirm absence of a live pending launcher: " + out
        assert "development_stop_unconfirmed" in err
        assert (root / "data/development/one/runtime/watchdog.pause").exists()
    finally:
        (root / "release").touch()
        time.sleep(0.1)


@pytest.mark.parametrize("fault", ["birth", "state_write"])
def test_post_creation_failure_persists_unknown_intent_across_resume(
    root: Path, fault: str,
) -> None:
    controls = root / "infra/dsh"
    body = (
        "$ErrorActionPreference='Stop'; "
        f"$global:TestRoot={q(root)}; $global:Fault={q(fault)}; $global:Started=$false; "
        "function Get-NetTCPConnection { }; function Get-CimInstance { }; "
        "function Get-Process { throw 'identity_denied' }; "
        "function Start-Process { param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,"
        "$WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError,$ErrorAction) "
        "$global:Started=$true; Add-Content (Join-Path $global:TestRoot 'launches') 'created'; "
        "$birth=if($global:Fault -eq 'birth'){$null}else{[datetime]::UtcNow}; "
        "[pscustomobject]@{Id=424242;StartTime=$birth} }; "
        "function Set-Content { [CmdletBinding()]param($LiteralPath,$Value,$Encoding) "
        "if($global:Started -and $global:Fault -eq 'state_write' -and "
        "[IO.Path]::GetFileName($LiteralPath) -like 'watchdog-state.json*'){throw 'disk_denied'}; "
        "Microsoft.PowerShell.Management\\Set-Content @PSBoundParameters }; "
        f"& {q(controls / 'ensure-dev-running.ps1')} -DevelopmentRoot data/development/one"
    )
    run(root, "first", body)
    state_path = root / "data/development/one/runtime/watchdog-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    state["next_attempt_at"] = "2000-01-01T00:00:00Z"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    run(root, "second", body + " -Resume")
    assert (root / "launches").read_text().splitlines() == ["created"]
    intent = root / "data/development/one/runtime/watchdog-launch-intent.json"
    assert intent.exists(), "Creation must have a durable unresolved fact before Start-Process"


@pytest.mark.parametrize("payload", ["{", "{}", '{"schema":"wrong"}'])
def test_malformed_intent_is_never_erased_by_resume(root: Path, payload: str) -> None:
    runtime = root / "data/development/one/runtime"
    (runtime / "watchdog-launch-intent.json").write_text(payload, encoding="utf-8")
    code, out, err = run(root, "bad-intent", "function Get-NetTCPConnection { }; "
                        "function Start-Process { throw 'must_not_create' }; & "
                        + q(root / "infra/dsh/ensure-dev-running.ps1")
                        + " -DevelopmentRoot data/development/one -Resume")
    assert code != 0 or '"action":"blocked_intent"' in out, out + err
    assert (runtime / "watchdog-launch-intent.json").read_text() == payload


def product_dummy(root: Path, mode: str) -> None:
    controls = root / "infra/dsh"
    # Keep real run-dev argument forwarding and all handoff/stop helpers. Only
    # product setup/services are replaced by this bounded native script.
    (controls / "run-product.ps1").write_text(
        "param([switch]$DevelopmentMode,[string]$DevelopmentRoot,[string]$WatchdogIntentId,"
        "[int]$ApiPort,[int]$DshPort,[int]$McpPort,[int]$AcquisitionPort,[int]$DeskPort,"
        "[string]$ResearchModel)\n$ErrorActionPreference='Stop'\n"
        ". (Join-Path $PSScriptRoot 'development-runtime.ps1')\n"
        ". (Join-Path $PSScriptRoot 'development-watchdog.ps1')\n"
        ". (Join-Path $PSScriptRoot 'product-processes.ps1')\n"
        "$repo=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path\n"
        "$layout=Resolve-DevelopmentLayout $repo $DevelopmentRoot\n"
        "$deadline=[datetime]::UtcNow.AddSeconds(20)\n"
        + (
            "$names=Get-DevelopmentWatchdogEventNames $layout $WatchdogIntentId\n"
            "$g=[Threading.EventWaitHandle]::OpenExisting($names.gate)\n"
            "$r=[Threading.EventWaitHandle]::OpenExisting($names.ready); [void]$r.Set()\n"
            "Set-Content (Join-Path $repo 'before-claim') yes\n"
            "while([datetime]::UtcNow -lt $deadline -and -not (Test-Path "
            "(Join-Path $repo 'release-claim'))){Start-Sleep -Milliseconds 20}\n"
            if mode == "pause_first" else ""
        )
        + "$handoff=Enter-DevelopmentWatchdogLaunch $layout $WatchdogIntentId\n"
        "Set-Content (Join-Path $repo 'claimed') yes\n"
        "while([datetime]::UtcNow -lt $deadline -and -not (Test-Path "
        "(Join-Path $repo 'release-handoff'))){Start-Sleep -Milliseconds 20}\n"
        "$supervisor=[pscustomobject]@{pid=$PID;started_ticks=(Get-ProductProcessStamp $PID);"
        "state='running';launch_id='native-handoff';development_root=$layout.root}\n"
        "Write-ProductJson (Join-Path $layout.runtime 'processes.json') @()\n"
        "Write-ProductJson (Join-Path $layout.runtime 'supervisor.json') $supervisor\n"
        "Complete-DevelopmentWatchdogLaunch $layout $handoff $supervisor\n"
        "while([datetime]::UtcNow -lt $deadline -and -not (Test-Path "
        "(Join-Path $layout.runtime 'stop.request'))){Start-Sleep -Milliseconds 20}\n",
        encoding="utf-8",
    )


def wait_file(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists(), str(path)


@pytest.mark.parametrize("mode", ["claim_first", "pause_first"])
def test_native_run_dev_handoff_and_stop_are_serialized(root: Path, mode: str) -> None:
    product_dummy(root, mode)
    controls = root / "infra/dsh"
    runtime = root / "data/development/one/runtime"
    try:
        code, out, err = run(root, "start", "function Get-NetTCPConnection { }; & "
                             + q(controls / "ensure-dev-running.ps1")
                             + " -DevelopmentRoot data/development/one")
        assert code == 0, out + err
        wait_file(root / ("claimed" if mode == "claim_first" else "before-claim"))
        code, out, err = run(root, "stop-initializing", "function Get-NetTCPConnection { }; & "
                             + q(controls / "stop-dev.ps1")
                             + " -DevelopmentRoot data/development/one -GraceSeconds 1")
        assert code != 0 and "development_stop_unconfirmed" in err, out + err
        if mode == "pause_first":
            assert (runtime / "watchdog.pause").exists()
            (root / "release-claim").touch()
            time.sleep(0.4)
            assert not (runtime / "supervisor.json").exists()
            assert not (root / "claimed").exists()
        else:
            (root / "release-handoff").touch()
            wait_file(runtime / "supervisor.json")
            code, out, err = run(root, "stop-owned", "function Get-NetTCPConnection { }; & "
                                 + q(controls / "stop-dev.ps1")
                                 + " -DevelopmentRoot data/development/one -GraceSeconds 2")
            assert code == 0, out + err
            receipt = json.loads((runtime / "stop-receipt.json").read_text())
            assert receipt["state"] == "stopped"
            assert not (root / "continued").exists()
    finally:
        (root / "release-claim").touch()
        (root / "release-handoff").touch()
        (runtime / "stop.request").touch()
        time.sleep(0.1)


def test_intent_parameter_never_grants_non_development_launch(root: Path) -> None:
    code, out, err = run(root, "non-dev", "& " + q(ROOT / "infra/dsh/run-product.ps1")
                        + " -WatchdogIntentId 0123456789abcdef0123456789abcdef")
    assert code != 0 and "development_parameters_require_explicit_mode" in err, out + err


def test_stop_refuses_legacy_live_pending_identity_without_new_intent(root: Path) -> None:
    runtime = root / "data/development/one/runtime"
    (runtime / "watchdog-state.json").write_text(json.dumps({
        "schema": "development-watchdog-state.v1", "attempts": 1,
        "launch_identity": {"pid": 424242, "started_ticks": "100"},
    }), encoding="utf-8")
    code, out, err = run(root, "legacy-stop", "function Get-NetTCPConnection { }; "
                        "function Get-Process { [pscustomobject]@{StartTime="
                        "[datetime]::new(100,[DateTimeKind]::Utc)} }; & "
                        + q(root / "infra/dsh/stop-dev.ps1")
                        + " -DevelopmentRoot data/development/one")
    assert code != 0 and "development_stop_unconfirmed" in err, out + err
    assert (runtime / "watchdog.pause").exists()


@pytest.mark.parametrize("resume", [False, True])
def test_legacy_missing_birth_is_unknown_even_after_resume(root: Path, resume: bool) -> None:
    runtime = root / "data/development/one/runtime"
    state_path = runtime / "watchdog-state.json"
    state_path.write_text(json.dumps({
        "schema": "development-watchdog-state.v1", "attempts": 1,
        "launch_identity": None, "next_attempt_at": "2000-01-01T00:00:00Z",
        "last_error": "unknown_old_launch",
    }), encoding="utf-8")
    before = state_path.read_bytes()
    code, out, err = run(root, "legacy-null", "function Get-NetTCPConnection { }; "
                        f"$global:TestRoot={q(root)}; function Start-Process {{ "
                        "Add-Content (Join-Path $global:TestRoot 'legacy-launched') yes; "
                        "throw 'must_not_create' }; & "
                        + q(root / "infra/dsh/ensure-dev-running.ps1")
                        + " -DevelopmentRoot data/development/one" + (" -Resume" if resume else ""))
    assert code == 0 and '"action":"blocked_intent"' in out, out + err
    assert not (root / "legacy-launched").exists()
    assert state_path.read_bytes() == before
