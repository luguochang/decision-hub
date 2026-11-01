"""Windows DPAPI and controlled child tests; never contacts an SMTP server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLS = ROOT / "infra/dsh"
HELPER = CONTROLS / "development-email.ps1"
PWSH = shutil.which("pwsh")
FAKE = "dummy-test-only-secret-29"
pytestmark = pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="Windows DPAPI")


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", body],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=35, check=False,
    )


def helper(root: Path, body: str) -> subprocess.CompletedProcess[str]:
    assert HELPER.is_file(), "restart-safe DEV email helper is missing"
    return ps(
        f"$ErrorActionPreference='Stop'; . {quote(HELPER)}; "
        f"$repo={quote(root)}; $dev='data/development/email'; {body}"
    )


def configure(root: Path, extra: str = "") -> subprocess.CompletedProcess[str]:
    return helper(
        root, f"$password=ConvertTo-SecureString {quote(FAKE)} -AsPlainText -Force; "
        "Save-DevelopmentEmailSettings -RepositoryRoot $repo -DevelopmentRoot $dev "
        "-SmtpHost 'smtp.example.invalid' -Sender 'sender@example.invalid' "
        "-Recipient 'recipient@example.invalid' -Username 'test-user' -Password $password; "
        + extra,
    )


def target(root: Path) -> Path:
    return root / "data/development/email/runtime/email-settings.json"


def test_missing_config_preserves_defaults_without_writing(tmp_path: Path) -> None:
    result = helper(
        tmp_path, "$e=Get-DevelopmentEmailEnvironment -RepositoryRoot $repo "
        "-DevelopmentRoot $dev; @{count=$e.Count} | ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"count": 0}
    assert not (tmp_path / "data").exists()


def test_dpapi_roundtrip_private_ciphertext_and_safe_status(tmp_path: Path) -> None:
    result = configure(tmp_path)
    assert result.returncode == 0, result.stderr
    text = target(tmp_path).read_text(encoding="utf-8")
    assert FAKE not in text
    data = json.loads(text)
    assert data["password_ciphertext"] and data["starttls"] is True
    result = helper(
        tmp_path, "$e=Get-DevelopmentEmailEnvironment -RepositoryRoot $repo "
        "-DevelopmentRoot $dev; @{secret_matches=($e['DECISION_HUB_SMTP_PASSWORD'] "
        f"-ceq {quote(FAKE)}); keys=@($e.Keys | Sort-Object)}} | ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "secret_matches": True,
        "keys": sorted([
            "DECISION_HUB_NOTIFICATION_CHANNEL", "DECISION_HUB_SMTP_HOST",
            "DECISION_HUB_SMTP_PASSWORD", "DECISION_HUB_SMTP_PORT", "DECISION_HUB_SMTP_RECIPIENT",
            "DECISION_HUB_SMTP_SENDER", "DECISION_HUB_SMTP_STARTTLS", "DECISION_HUB_SMTP_USERNAME",
        ]),
    }
    result = helper(
        tmp_path, "Get-DevelopmentEmailStatus -RepositoryRoot $repo -DevelopmentRoot $dev "
        "| ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"configured": True, "channel": "email", "validated": True}
    assert FAKE not in result.stdout + result.stderr
    assert "example.invalid" not in result.stdout + result.stderr


@pytest.mark.parametrize("mutation", [
    "broken-json", "bad-cipher", "extra-key", "duplicate-key", "crlf", "no-tls", "ssl465",
    "array-schema", "array-channel",
])
def test_existing_invalid_configuration_fails_closed(tmp_path: Path, mutation: str) -> None:
    assert configure(tmp_path).returncode == 0
    path = target(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "broken-json":
        text = "{"
    elif mutation == "duplicate-key":
        text = path.read_text(encoding="utf-8").replace(
            '"channel":"email"', '"channel":"local","channel":"email"',
        )
    else:
        key, value = {
            "bad-cipher": ("password_ciphertext", "not-dpapi"),
            "extra-key": ("DECISION_HUB_MODEL", "injected-model"),
            "crlf": ("host", "smtp.example.invalid\r\nBcc: other@example.invalid"),
            "no-tls": ("starttls", False), "ssl465": ("port", 465),
            "array-schema": ("schema", ["development-email.v1"]),
            "array-channel": ("channel", ["email"]),
        }[mutation]
        data[key] = value
        text = json.dumps(data)
    path.write_text(text, encoding="utf-8")
    result = helper(
        tmp_path, "Get-DevelopmentEmailEnvironment -RepositoryRoot $repo -DevelopmentRoot $dev",
    )
    assert result.returncode != 0
    assert "development_email_configuration_invalid_or_undecryptable" in result.stderr
    assert FAKE not in result.stdout + result.stderr
    assert "Bcc:" not in result.stdout + result.stderr


def test_valid_dpapi_ciphertext_with_wrong_entropy_fails_closed(tmp_path: Path) -> None:
    assert configure(tmp_path).returncode == 0
    result = helper(
        tmp_path, f"$path={quote(target(tmp_path))}; "
        "$record=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -AsHashtable; "
        "$bytes=[Text.Encoding]::Unicode.GetBytes('fake-wrong-entropy'); "
        "$entropy=[Text.Encoding]::UTF8.GetBytes('different-context'); "
        "$encrypted=[Security.Cryptography.ProtectedData]::Protect($bytes,$entropy,"
        "[Security.Cryptography.DataProtectionScope]::CurrentUser); "
        "$record.password_ciphertext=[Convert]::ToHexString($encrypted); "
        "[IO.File]::WriteAllText($path,($record | ConvertTo-Json -Compress)); "
        "Get-DevelopmentEmailEnvironment -RepositoryRoot $repo -DevelopmentRoot $dev",
    )
    assert result.returncode != 0
    assert "development_email_configuration_invalid_or_undecryptable" in result.stderr


@pytest.mark.parametrize("field", ["SmtpHost", "Sender", "Recipient", "Username"])
def test_local_save_rejects_crlf_before_creating_configuration(tmp_path: Path, field: str) -> None:
    args = {
        "SmtpHost": "smtp.example.invalid", "Sender": "sender@example.invalid",
        "Recipient": "recipient@example.invalid", "Username": "test-user",
    }
    args[field] += "\r\nInjected: value"
    arguments = " ".join(f"-{key} {quote(value)}" for key, value in args.items())
    result = helper(
        tmp_path, "$password=ConvertTo-SecureString 'fake-only' -AsPlainText -Force; "
        "Save-DevelopmentEmailSettings -RepositoryRoot $repo -DevelopmentRoot $dev "
        f"-Password $password {arguments}",
    )
    assert result.returncode != 0
    assert not target(tmp_path).exists()


def test_oversized_record_rejected_before_configuration_is_created(tmp_path: Path) -> None:
    result = helper(
        tmp_path, "$password=ConvertTo-SecureString 'fake-only' -AsPlainText -Force; "
        "$long='x'*16000; Save-DevelopmentEmailSettings -RepositoryRoot $repo "
        "-DevelopmentRoot $dev -Password $password -SmtpHost 'smtp.example.invalid' "
        "-Username $long -Sender ($long+'@example.invalid') -Recipient ($long+'@example.invalid')",
    )
    assert result.returncode != 0
    assert not target(tmp_path).exists()


def test_disable_is_explicit_local_and_clears_ciphertext(tmp_path: Path) -> None:
    result = configure(
        tmp_path, "Save-DevelopmentEmailSettings -RepositoryRoot $repo -DevelopmentRoot $dev "
        "-Disable; Get-DevelopmentEmailEnvironment -RepositoryRoot $repo "
        "-DevelopmentRoot $dev | ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"DECISION_HUB_NOTIFICATION_CHANNEL": "local"}
    assert json.loads(target(tmp_path).read_text(encoding="utf-8")) == {
        "schema": "development-email.v1", "channel": "local",
    }
    assert not list(target(tmp_path).parent.glob(".email-*.tmp"))


@pytest.mark.parametrize("linked", ["hardlink", "junction"])
def test_linked_paths_rejected_before_touching_other_files(tmp_path: Path, linked: str) -> None:
    assert configure(tmp_path).returncode == 0
    path = target(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if linked == "hardlink":
        os.link(path, outside / "saved.json")
    else:
        moved = outside / "runtime"
        path.parent.rename(moved)
        result = ps(
            f"New-Item -ItemType Junction -Path {quote(path.parent)} "
            f"-Target {quote(moved)} | Out-Null",
        )
        assert result.returncode == 0, result.stderr
    before = path.read_bytes()
    result = helper(
        tmp_path, "Get-DevelopmentEmailEnvironment -RepositoryRoot $repo -DevelopmentRoot $dev",
    )
    assert result.returncode != 0
    result = configure(tmp_path)
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_insecure_acl_is_rejected_without_repair(tmp_path: Path) -> None:
    assert configure(tmp_path).returncode == 0
    result = helper(
        tmp_path, f"$path={quote(target(tmp_path))}; "
        "$acl=[Security.AccessControl.FileSecurity]::new($path,'Access'); "
        "$sid=[Security.Principal.SecurityIdentifier]::new('S-1-1-0'); "
        "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new("
        "$sid,'Read','Allow')); "
        "[IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($path),$acl); "
        "Get-DevelopmentEmailEnvironment -RepositoryRoot $repo -DevelopmentRoot $dev",
    )
    assert result.returncode != 0
    assert "development_email_private_acl_required" in result.stderr


def launcher_hook() -> str:
    return (
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{quote(CONTROLS / 'run-product.ps1')},[ref]$null,[ref]$null); "
        "$hook=$ast.Find({param($n) $n -is [Management.Automation.Language.IfStatementAst] "
        "-and $n.Extent.Text.Contains('$developmentEmailEnvironment =')},$true); "
        "if (-not $hook) {throw 'missing_official_hook'}; "
        f"$hookText=$hook.Extent.Text.Replace('$PSScriptRoot',{quote(quote(CONTROLS))}); "
        "$repoRoot=$repo; "
        "$developmentLayout=Resolve-DevelopmentLayout -RepositoryRoot $repo -DevelopmentRoot $dev; "
        "$DevelopmentRoot=$dev; $dataDir=$developmentLayout.data; "
    )


def test_official_child_reads_pilotsettings_without_llm_or_database_changes(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        "import json, os, time\nfrom packages.pilot_runtime.config import PilotSettings\n"
        "s=PilotSettings()\nprint(json.dumps(dict(channel=s.notification_channel,"
        "secret_present=bool(s.smtp_password and s.smtp_password.get_secret_value()),"
        "starttls=s.smtp_starttls,port=s.smtp_port,"
        "llm_unchanged=os.environ.get('OPENAI_API_KEY')=='llm-sentinel')),flush=True)\n"
        "time.sleep(2)\n", encoding="utf-8",
    )
    assert configure(tmp_path).returncode == 0
    result = helper(tmp_path, launcher_hook() + (
        "$DevelopmentMode=$true; $common=@{OPENAI_API_KEY='llm-sentinel'}; "
        "Invoke-Expression $hookText; "
        f"$common['PYTHONPATH']={quote(ROOT)}; "
        "$func=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $n.Name -eq 'Start-ProductProcess'},$true); Invoke-Expression $func.Extent.Text; "
        f". {quote(CONTROLS / 'product-processes.ps1')}; "
        "$launchSpecs=@{}; $started=[Collections.Generic.List[object]]::new(); "
        "$checkpointState=@{degraded=$false}; $supervisorEntry=@{started_ticks='1'}; "
        f"$logDir={quote(tmp_path)}; $stateFile=Join-Path $logDir 'state.json'; "
        "$env:DECISION_HUB_SMTP_PASSWORD='parent-sentinel'; "
        f"$process=Start-ProductProcess -Name 'probe' -FilePath {quote(sys.executable)} "
        f"-ArgumentList @(('\"'+{quote(child)}+'\"')) -Environment $common -Marker 'probe'; "
        "$process.WaitForExit(); if ($process.ExitCode -ne 0) {throw 'child_failed'}; "
        "if ($env:DECISION_HUB_SMTP_PASSWORD -cne 'parent-sentinel') "
        "{throw 'parent_not_restored'}; "
        "Get-Content (Join-Path $logDir 'probe.0.stdout.log') -Raw"
    ))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "channel": "email", "secret_present": True, "starttls": True,
        "port": 587, "llm_unchanged": True,
    }
    assert FAKE not in (tmp_path / "state.json").read_text(encoding="utf-8")
    assert FAKE not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.log"))
    assert not list(tmp_path.rglob("*.db"))


def test_formal_hook_does_not_read_dev_configuration(tmp_path: Path) -> None:
    assert configure(tmp_path).returncode == 0
    target(tmp_path).write_text("{", encoding="utf-8")
    result = helper(tmp_path, launcher_hook() + (
        "$DevelopmentMode=$false; $common=@{OPENAI_API_KEY='original'}; "
        "Invoke-Expression $hookText; $common | ConvertTo-Json -Compress"
    ))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"OPENAI_API_KEY": "original"}


def test_check_cli_redacts_fields_and_rejects_plaintext_password_argument(tmp_path: Path) -> None:
    # Execute the real CLI with a temporary copy whose repository is this test root.
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in ("development-runtime.ps1", "decision-settings-session.ps1",
                 "development-email.ps1", "configure-dev-email.ps1"):
        shutil.copyfile(CONTROLS / name, controls / name)
    assert configure(tmp_path).returncode == 0
    command = f"& {quote(controls / 'configure-dev-email.ps1')} -DevelopmentRoot $dev "
    result = helper(tmp_path, command + "-Check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"configured": True, "channel": "email", "validated": True}
    assert FAKE not in result.stdout + result.stderr
    assert "example.invalid" not in result.stdout + result.stderr
    rejected = helper(tmp_path, command + "-Password 'not-accepted'")
    assert rejected.returncode != 0
    target(tmp_path).write_text("{", encoding="utf-8")
    rejected = helper(tmp_path, command + "-Check")
    assert rejected.returncode != 0
    assert "development_email_configuration_failed" in rejected.stderr
