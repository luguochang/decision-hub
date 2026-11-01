"""Exercise private launcher generation without launching a service/browser."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_private_launch_helper_provisions_stable_owner_and_no_logged_secret(tmp_path, monkeypatch):
    helper = ROOT / "infra/dsh/decision-settings-session.ps1"
    assert helper.exists(), "normal launcher settings session helper is missing"
    script = tmp_path / "exercise.ps1"
    # Arguments travel as native argv; no credentials are written to the log.
    script.write_text('param($Helper, $Runtime)\n. $Helper\n$value = New-ProductSettingsSession -RuntimeDirectory $Runtime -Origin "http://127.0.0.1:8123" -OwnerId "owner"\n@{config_path=$value.ConfigPath;config_sha256=$value.ConfigSha256;entry_path=$value.EntryPath} | ConvertTo-Json -Compress\n', encoding="utf-8")
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(script), str(helper), str(tmp_path / "runtime")], capture_output=True, text=True, check=True)
    public = json.loads(result.stdout)
    config_path = Path(public["config_path"])
    config = json.loads(config_path.read_bytes())
    entry = Path(public["entry_path"]).read_text(encoding="utf-8")
    assert config["owner_identity"] == "owner"
    assert config["origin"] == "http://127.0.0.1:8123"
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == public["config_sha256"]
    assert "settings_pairing=" in entry
    token = entry.split("settings_pairing=", 1)[1].strip()
    assert hashlib.sha256(token.encode()).hexdigest() == config["pairing_sha256"]
    assert token not in result.stdout + result.stderr
    monkeypatch.setenv("DECISION_HUB_SETTINGS_PRIVATE_CONFIG", str(config_path))
    monkeypatch.setenv("DECISION_HUB_SETTINGS_PRIVATE_SHA256", public["config_sha256"])
    from apps.hub_api.decision_settings_routes import configured_settings_authority
    authority = configured_settings_authority()
    signed = authority.pair(token, origin=config["origin"])
    assert authority.resolve(signed.cookie_value, origin=config["origin"], mutation=True, csrf_token=signed.csrf_token).owner_identity == "owner"
    assert "settings_pairing=" not in str(public)
    # The entry and config have protected current-user ACLs, no inherited ACEs.
    acl_script = tmp_path / "acl.ps1"
    acl_script.write_text('param($Config,$Entry)\n@((Get-Acl -LiteralPath $Config).AreAccessRulesProtected, (Get-Acl -LiteralPath $Entry).AreAccessRulesProtected) | ConvertTo-Json -Compress\n', encoding="utf-8")
    protected = subprocess.run(["pwsh", "-NoProfile", "-File", str(acl_script), str(config_path), public["entry_path"]], capture_output=True, text=True, check=True)
    assert json.loads(protected.stdout) == [True, True]


def test_explicit_desktop_lifetime_preserves_old_default_cap():
    from apps.hub_api.local_owner_session import LocalOwnerSessionAuthority
    values = dict(signing_key=b"a" * 32, pairing_sha256=hashlib.sha256(b"x" * 32).digest(), owner_identity="owner", domain_pack_ref="crypto_macro.v1", origin="http://127.0.0.1:8123")
    with pytest.raises(ValueError, match="local_owner_private_configuration_invalid"):
        LocalOwnerSessionAuthority(**values, lifetime_seconds=28800)
    with pytest.raises(ValueError, match="local_owner_private_configuration_invalid"):
        LocalOwnerSessionAuthority(**values, lifetime_seconds=28801, maximum_lifetime_seconds=28800)
    clock = [0]
    desktop = LocalOwnerSessionAuthority(**values, lifetime_seconds=28800, maximum_lifetime_seconds=28800, clock=lambda: clock[0])
    session = desktop.pair("x" * 32, origin=values["origin"])
    clock[0] = 28799
    assert desktop.resolve(session.cookie_value, origin=values["origin"], mutation=False).owner_identity == "owner"
    clock[0] = 28800
    with pytest.raises(PermissionError, match="local_owner_session_rejected"):
        desktop.resolve(session.cookie_value, origin=values["origin"], mutation=False)
