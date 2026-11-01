"""Run real PowerShell watchdogs in isolated roots; no product/model/task launch."""

import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 required")


def q(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", body],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "product-processes.ps1",
                 "ensure-dev-running.ps1", "install-dev-watchdog.ps1", "stop-dev.ps1",
                 "development-watchdog.ps1"):
        source = ROOT / "infra/dsh" / name
        if name == "stop-dev.ps1":
            source = Path(os.environ.get("WATCHDOG_STOP_SOURCE", str(source)))
        if source.exists():
            shutil.copyfile(source, controls / name)
    (controls / "run-dev.ps1").write_text("throw 'must_not_launch_product'", encoding="utf-8")
    runtime = tmp_path / "data/development/one/runtime"
    runtime.mkdir(parents=True)
    (runtime / "supervisor.json").write_text(json.dumps({
        "pid": 701, "started_ticks": "100", "state": "running", "launch_id": "old",
        "development_root": str(runtime.parent),
    }), encoding="utf-8")
    (runtime / "processes.json").write_text(json.dumps([{
        "pid": 702, "started_ticks": "200", "children": [
            {"pid": 703, "started_ticks": "300"}], "health_probe": {"port": 18047},
    }]), encoding="utf-8")
    return tmp_path


def invoke(root: Path, mode: str = "absent", arguments: str = "") -> dict[str, object]:
    # Only OS boundaries are replaced: status/lifetime/state/mutex are real.
    body = (
        "$ErrorActionPreference='Stop'; "
        f"$global:WatchdogTestMode={q(mode)}; $global:WatchdogTestRoot={q(root)}; "
        "function Get-Process { [CmdletBinding()]param($Id) "
        "if($global:WatchdogTestMode -eq 'unknown'){throw 'denied'}; "
        "if($global:WatchdogTestMode -eq 'orphan' -and $Id -eq 703){return "
        "[pscustomobject]@{StartTime=[datetime]::new(300,[DateTimeKind]::Utc)}}; "
        "if($global:WatchdogTestMode -eq 'reused'){return "
        "[pscustomobject]@{StartTime=[datetime]::new(999,[DateTimeKind]::Utc)}}; "
        "$PSCmdlet.ThrowTerminatingError([Management.Automation.ErrorRecord]::new("
        "[Exception]::new('absent'),'NoProcessFoundForGivenId','ObjectNotFound',$Id)) }; "
        "function Get-CimInstance { }; "
        "function Get-NetTCPConnection { param($State,$ErrorAction) "
        "if($global:WatchdogTestMode -eq 'ports_unknown'){throw 'denied'}; "
        "if($global:WatchdogTestMode -eq 'conflict'){"
        "[pscustomobject]@{LocalPort=5173;OwningProcess=999}} }; "
        "function Stop-Process { throw 'must_never_kill' }; "
        "function Start-Process { param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,"
        "$WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError,$ErrorAction) "
        "if($WindowStyle -ne 'Hidden'){throw 'visible_launch'}; "
        "if($ArgumentList -notmatch 'run-dev.ps1'){throw 'unofficial_launch'}; "
        "Add-Content -LiteralPath (Join-Path $global:WatchdogTestRoot 'launches.txt') "
        "-Value $ArgumentList; "
        "throw 'controlled_start_failure' }; "
        f"& {q(root / 'infra/dsh/ensure-dev-running.ps1')} -DevelopmentRoot "
        f"'data/development/one' {arguments}"
    )
    result = ps(body)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize("mode,want", [
    ("absent", "launch_failed"), ("reused", "launch_failed"),
    ("unknown", "blocked_status"), ("orphan", "blocked_status"),
    ("conflict", "blocked_ports"), ("ports_unknown", "blocked_status"),
])
def test_only_proven_absence_without_conflicts_can_launch(
    sandbox: Path, mode: str, want: str,
) -> None:
    result = invoke(sandbox, mode)
    assert result["action"] == want
    assert (sandbox / "launches.txt").exists() is (mode in {"absent", "reused"})


def test_failure_budget_is_persistent_and_pause_requires_explicit_resume(sandbox: Path) -> None:
    state_path = sandbox / "data/development/one/runtime/watchdog-state.json"
    state = {"schema": "development-watchdog-state.v1", "attempts": 1,
             "launch_identity": None, "last_error": None,
             "next_attempt_at": "2099-01-01T00:00:00Z"}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (state_path.parent / "watchdog-launch-intent.json").write_text(json.dumps({
        "schema": "development-watchdog-intent.v1", "id": "a" * 32,
        "root": str(state_path.parent.parent), "phase": "handed_off", "launch_id": "old",
        "launch_identity": {"pid": 704, "started_ticks": "400"},
        "supervisor_identity": {"pid": 701, "started_ticks": "100"},
    }), encoding="utf-8")
    assert invoke(sandbox)["action"] == "backoff"
    state.update(attempts=3, next_attempt_at="2000-01-01T00:00:00Z")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert invoke(sandbox)["action"] == "exhausted"
    assert invoke(sandbox, arguments="-Pause")["action"] == "paused"
    assert invoke(sandbox)["action"] == "paused"
    assert invoke(sandbox, arguments="-Resume")["attempts"] == 1
    assert len((sandbox / "launches.txt").read_text().splitlines()) == 1
    # A creation exception is now an unresolved durable intent, not retry permission.
    assert invoke(sandbox, arguments="-Resume")["action"] == "blocked_intent"


def test_lock_prevents_a_second_watchdog_from_starting(sandbox: Path) -> None:
    lock_path = sandbox / "data/development/one/runtime/watchdog.lock"
    result = ps(
        f"$s=[IO.File]::Open({q(lock_path)},'OpenOrCreate','ReadWrite','None'); "
        f"try {{ & {q(sandbox / 'infra/dsh/ensure-dev-running.ps1')} "
        "-DevelopmentRoot 'data/development/one' } finally {$s.Dispose()}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["action"] == "busy"
    assert not (sandbox / "launches.txt").exists()


@pytest.mark.parametrize("offset,want", [(20000, "reused"), (9999, "unknown"), (0, "unknown")])
def test_cim_can_only_disprove_inaccessible_birth_identity(offset: int, want: str) -> None:
    # Catch: denied StartTime masks a provably reused service PID; CIM never grants ownership.
    result = ps(
        "$ErrorActionPreference='Stop'; "
        f". {q(ROOT / 'infra/dsh/development-runtime.ps1')}; "
        "function Get-Process { throw 'access_denied' }; "
        "function Get-CimInstance { param($ClassName,$Filter,$Property,$ErrorAction) "
        f"[pscustomobject]@{{CreationDate=[datetime]::new({100000 + offset},"
        "[DateTimeKind]::Utc)} }; "
        "Get-DevelopmentLifetime ([pscustomobject]@{pid=701;started_ticks='100000'}) "
        "| ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == want


def test_task_definition_defaults_to_current_user_limited_preview(sandbox: Path) -> None:
    result = ps(
        "function Register-ScheduledTask { throw 'must_not_register' }; "
        f"& {q(sandbox / 'infra/dsh/install-dev-watchdog.ps1')} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["installed"] is False
    assert value["principal"]["run_level"] == "LeastPrivilege"
    assert value["principal"]["logon_type"] == "InteractiveToken"
    assert value["principal"]["user_id"]
    assert value["triggers"] == ["logon", "periodic_1_minute"]
    assert "ensure-dev-running.ps1" in value["arguments"]
    assert "-WindowStyle Hidden" in value["arguments"]
    assert value["multiple_instances"] == "IgnoreNew"
    task = ET.fromstring(value["xml"])
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert task.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=namespace) == "PT0S"
    assert not (sandbox / "data/development/one/runtime/watchdog-state.json").exists()


def test_verified_running_instance_is_preserved(sandbox: Path) -> None:
    runtime = sandbox / "data/development/one/runtime"
    (runtime / "processes.json").write_text("[]", encoding="utf-8")
    before = (runtime / "supervisor.json").read_bytes()
    result = ps(
        "$ErrorActionPreference='Stop'; "
        f"$global:WatchdogTestRoot={q(sandbox)}; "
        "function Get-Process { "
        "[pscustomobject]@{StartTime=[datetime]::new(100,[DateTimeKind]::Utc)} }; "
        "function Get-CimInstance { [pscustomobject]@{CommandLine=("
        "'pwsh -File \"' + (Join-Path $global:WatchdogTestRoot 'infra/dsh/run-product.ps1') + "
        "'\" -DevelopmentMode -DevelopmentRoot data/development/one')} }; "
        "function Get-NetTCPConnection { }; "
        "function Start-Process { throw 'must_not_restart_online' }; "
        f"& {q(sandbox / 'infra/dsh/ensure-dev-running.ps1')} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["action"] == "already_running"
    assert (runtime / "supervisor.json").read_bytes() == before
    assert json.loads((runtime / "watchdog-state.json").read_text())["attempts"] == 0


def test_official_stop_pauses_even_an_already_absent_instance(sandbox: Path) -> None:
    runtime = sandbox / "data/development/one/runtime"
    # Use a pristine absent instance so stop never touches a native product process.
    (runtime / "supervisor.json").unlink()
    result = ps(
        "function Get-NetTCPConnection { }; function Stop-Process { throw 'must_not_kill' }; "
        f"& {q(sandbox / 'infra/dsh/stop-dev.ps1')} -DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (runtime / "watchdog.pause").exists()
    assert invoke(sandbox)["action"] == "paused"
    assert not (sandbox / "launches.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden native dummy")
def test_two_native_checks_start_one_dummy_and_preserve_pending_identity(sandbox: Path) -> None:
    controls = sandbox / "infra/dsh"
    # Real Start-Process executes only this short-lived isolated dummy.
    (controls / "run-dev.ps1").write_text(
        "param([string]$DevelopmentRoot,[string]$WatchdogIntentId)\n"
        ". (Join-Path $PSScriptRoot 'development-runtime.ps1')\n"
        ". (Join-Path $PSScriptRoot 'development-watchdog.ps1')\n"
        "$repo=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path\n"
        "$layout=Resolve-DevelopmentLayout $repo $DevelopmentRoot\n"
        "$names=Get-DevelopmentWatchdogEventNames $layout $WatchdogIntentId\n"
        "$ack=[Threading.EventWaitHandle]::OpenExisting($names.ready); [void]$ack.Set()\n"
        "$ack.Dispose()\n"
        "Add-Content (Join-Path $PSScriptRoot '../../launches.txt') 'native-dummy'\n"
        "$deadline=[datetime]::UtcNow.AddSeconds(20)\n"
        "while([datetime]::UtcNow -lt $deadline -and -not (Test-Path "
        "(Join-Path $PSScriptRoot '../../release-dummy'))) {Start-Sleep -Milliseconds 20}\n",
        encoding="utf-8",
    )
    command = (
        "function Get-NetTCPConnection { }; "
        f"& {q(controls / 'ensure-dev-running.ps1')} -DevelopmentRoot 'data/development/one'"
    )
    try:
        with ExitStack() as stack:
            # File handles let wait() observe only the checker. Windows can
            # otherwise keep Python's capture pipes open in the launched dummy.
            children = []
            for index in range(2):
                out = stack.enter_context((sandbox / f"check{index}.out").open("w"))
                err = stack.enter_context((sandbox / f"check{index}.err").open("w"))
                children.append(subprocess.Popen(
                    [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", command],
                    stdout=out, stderr=err,
                ))
            responses = []
            for index, child in enumerate(children):
                child.wait(timeout=15)
                stdout = (sandbox / f"check{index}.out").read_text(encoding="utf-8-sig")
                stderr = (sandbox / f"check{index}.err").read_text(encoding="utf-8-sig")
                assert child.returncode == 0, stdout + stderr
                responses.append(json.loads(stdout)["action"])
            assert sorted(responses) in (["busy", "launched"], ["launch_pending", "launched"])
            deadline = time.monotonic() + 3
            while not (sandbox / "launches.txt").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (sandbox / "launches.txt").read_text().splitlines() == ["native-dummy"]
            value = ps(command)
            assert value.returncode == 0, value.stdout + value.stderr
            assert json.loads(value.stdout)["action"] == "launch_pending"
    finally:
        (sandbox / "release-dummy").touch()
        time.sleep(0.2)  # Bounded dummy exits promptly; no product service stays behind.


def test_install_uses_reviewed_xml_only_after_explicit_switch(sandbox: Path) -> None:
    result = ps(
        "$global:RegisteredTask=$null; "
        "function Register-ScheduledTask { param($TaskName,$Xml,$ErrorAction) "
        "$global:RegisteredTask=[xml]$Xml }; "
        f"$value=& {q(sandbox / 'infra/dsh/install-dev-watchdog.ps1')} "
        "-DevelopmentRoot 'data/development/one' -Install; "
        "@{value=($value | ConvertFrom-Json); registered=($null -ne $global:RegisteredTask); "
        "run_level=$global:RegisteredTask.Task.Principals.Principal.RunLevel; "
        "logon_type=$global:RegisteredTask.Task.Principals.Principal.LogonType} "
        "| ConvertTo-Json -Depth 6"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["registered"] is True
    assert value["value"]["installed"] is True
    assert value["run_level"] == "LeastPrivilege"
    assert value["logon_type"] == "InteractiveToken"


@pytest.mark.skipif(os.name != "nt", reason="Windows task XML parser")
def test_task_xml_is_accepted_by_native_in_memory_parser_without_registration(
    sandbox: Path,
) -> None:
    result = ps(
        "$ErrorActionPreference='Stop'; "
        "function Register-ScheduledTask { throw 'must_not_register' }; "
        f"$value=& {q(sandbox / 'infra/dsh/install-dev-watchdog.ps1')} "
        "-DevelopmentRoot 'data/development/one' | ConvertFrom-Json; "
        "$scheduler=New-Object -ComObject Schedule.Service; $scheduler.Connect(); "
        "$definition=$scheduler.NewTask(0); $definition.XmlText=$value.xml; "
        "@{run_level=$definition.Principal.RunLevel; "
        "time_limit=$definition.Settings.ExecutionTimeLimit; "
        "actions=$definition.Actions.Count;triggers=$definition.Triggers.Count} "
        "| ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "run_level": 0, "time_limit": "PT0S", "actions": 1, "triggers": 2,
    }


@pytest.mark.parametrize("ready,gap,owned,launch,want", [
    ("ready", 60, True, "old", 0), ("failed", 60, True, "old", 2),
    ("ready", 300, True, "old", 2), ("ready", 60, False, "old", 2),
    ("unknown", 60, True, "old", 2), ("ready", 60, True, "different", 2),
])
def test_only_continuously_verified_ready_instance_resets_failure_budget(
    sandbox: Path, ready: str, gap: int, owned: bool, launch: str, want: int,
) -> None:
    runtime = sandbox / "data/development/one/runtime"
    (runtime / "processes.json").write_text(json.dumps([{
        "pid": 702, "started_ticks": "200", "children": [],
        "development_owner_ticks": "100", "development_root": str(runtime.parent),
        "health_probe": {"port": 18047},
    }]), encoding="utf-8")
    (runtime / "watchdog-launch-intent.json").write_text(json.dumps({
        "schema": "development-watchdog-intent.v1", "id": "a" * 32,
        "root": str(runtime.parent), "phase": "handed_off", "launch_id": "old",
        "launch_identity": {"pid": 704, "started_ticks": "400"},
        "supervisor_identity": {"pid": 701, "started_ticks": "100"},
    }), encoding="utf-8")
    result = ps(
        "$ErrorActionPreference='Stop'; "
        f"$global:WatchdogTestRoot={q(sandbox)}; "
        f"$runtime={q(runtime)}; $now=[datetime]::UtcNow; "
        "@{schema='development-watchdog-state.v1';attempts=2;next_attempt_at=$null;"
        "launch_identity=$null;last_error='old';stable_since=$now.AddSeconds(-700).ToString('o');"
        f"last_healthy_at=$now.AddSeconds(-{gap}).ToString('o');"
        f"stable_launch_id={q(launch)};stable_owner_ticks='100'}} "
        "| ConvertTo-Json | Set-Content (Join-Path $runtime 'watchdog-state.json'); "
        "function Get-Process { param($Id,$ErrorAction) "
        "$ticks=if($Id -eq 701){100}else{200}; "
        "[pscustomobject]@{StartTime=[datetime]::new($ticks,[DateTimeKind]::Utc)} }; "
        "function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) "
        "if($Filter -eq 'ProcessId=701'){[pscustomobject]@{CommandLine=('pwsh -File \"' + "
        "(Join-Path $global:WatchdogTestRoot 'infra/dsh/run-product.ps1') + "
        "'\" -DevelopmentMode -DevelopmentRoot data/development/one')}} "
        "elseif($Filter){[pscustomobject]@{ProcessId=702;ParentProcessId=701}} }; "
        "function Get-NetTCPConnection { [pscustomobject]@{LocalPort=18047;"
        f"OwningProcess={702 if owned else 999}}} }}; "
        "function Invoke-RestMethod { param($Uri,[switch]$NoProxy,$TimeoutSec,$ErrorAction) "
        "if(-not $NoProxy -or $TimeoutSec -gt 3){throw 'unsafe_health_request'}; "
        "if($Uri -ne 'http://127.0.0.1:18047/health/ready'){throw 'wrong_health'}; "
        f"[pscustomobject]@{{status={q(ready)}}} }}; "
        "function Start-Process { throw 'must_not_restart_online' }; "
        f"& {q(sandbox / 'infra/dsh/ensure-dev-running.ps1')} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["action"] == "already_running"
    state = json.loads((runtime / "watchdog-state.json").read_text(encoding="utf-8-sig"))
    assert state["attempts"] == want
    audit = (runtime / "watchdog-audit.jsonl").read_text(encoding="utf-8-sig")
    assert ('"action":"stable_reset"' in audit) is (want == 0)
