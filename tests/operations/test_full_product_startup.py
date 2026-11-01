"""Check startup decisions with isolated files and fail-closed I/O substitutes."""

import re
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from packages.query_views.operations.service import EXPECTED_SERVICES
from tools import full_product_acceptance as startup

SERVICE_IDS = {service_id for service_id, _role in EXPECTED_SERVICES}


def forbidden(*_args, **_kwargs):
    raise AssertionError("Unexpected real process, network, or blocking call")


@pytest.fixture(autouse=True)
def isolated_io(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(
        startup,
        "requests",
        SimpleNamespace(get=forbidden, RequestException=requests.RequestException),
    )
    monkeypatch.setattr(startup, "socket", SimpleNamespace(create_connection=forbidden))
    monkeypatch.setattr(
        startup,
        "subprocess",
        SimpleNamespace(
            run=forbidden,
            Popen=forbidden,
            DEVNULL=-3,
            STDOUT=-2,
            CREATE_NO_WINDOW=0x08000000,
        ),
    )
    monkeypatch.setattr(startup, "time", SimpleNamespace(monotonic=forbidden, sleep=forbidden))


def readiness(monkeypatch, services=None, *, failed_url=None, dsh_flag=None, mcp_error=False):
    if services is None:
        services = {service: "online" for service in SERVICE_IDS}
    dsh = dict.fromkeys(
        ("ready", "version_compatible", "session_controller", "client_plugin", "hub_reachable"),
        True,
    )
    if dsh_flag:
        dsh[dsh_flag] = False
    operations = {
        "runtime": {"runtime_id": "dsh-web"},
        "services": [{"service_id": name, "status": status} for name, status in services.items()],
    }
    urls = {
        startup.API + "/health/ready": {},
        startup.DSH + "/decision-hub/v1/readiness": dsh,
        "http://127.0.0.1:8038/health/ready": {},
        startup.API + "/v1/operations": operations,
    }

    def get(url, **_kwargs):
        assert url in urls
        return SimpleNamespace(
            status_code=503 if url == failed_url else 200, json=lambda: urls[url]
        )

    monkeypatch.setattr(startup.requests, "get", Mock(side_effect=get))
    connector = (
        Mock(side_effect=OSError("mock MCP unavailable"))
        if mcp_error
        else Mock(return_value=nullcontext())
    )
    monkeypatch.setattr(startup.socket, "create_connection", connector)


@pytest.mark.parametrize("role", sorted(SERVICE_IDS))
def test_readiness_requires_every_canonical_service_online(monkeypatch, role):
    services = {service: "online" for service in SERVICE_IDS if service != role}
    readiness(monkeypatch, services)
    assert startup.product_ready() is False


@pytest.mark.parametrize("role", ["hub-historical-worker", "hub-evolution-worker"])
@pytest.mark.parametrize("status", ["stale", "offline"])
def test_inactive_background_role_cannot_be_reported_as_ready(monkeypatch, role, status):
    services = {service: "online" for service in SERVICE_IDS}
    services[role] = status
    readiness(monkeypatch, services)
    assert startup.product_ready() is False


def test_all_seven_services_still_require_dsh_acquisition_and_mcp(monkeypatch):
    readiness(monkeypatch)
    assert startup.product_ready() is True
    startup.socket.create_connection.assert_called_once_with(("127.0.0.1", 8037), timeout=3)
    dsh_call = startup.requests.get.call_args_list[1]
    assert dsh_call.kwargs["headers"] == {"X-Decision-Hub-Host-Key": "decision-hub-local-host-key"}
    readiness(monkeypatch, dsh_flag="hub_reachable")
    assert startup.product_ready() is False
    readiness(monkeypatch, failed_url="http://127.0.0.1:8038/health/ready")
    assert startup.product_ready() is False
    readiness(monkeypatch, mcp_error=True)
    assert startup.product_ready() is False


def startup_mocks(monkeypatch, *, ready_after=False, launcher_exit=None, timeout=False):
    monkeypatch.setattr(startup, "product_ready", Mock(side_effect=[False, ready_after]))
    stop = Mock(return_value=SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr(startup.subprocess, "run", stop)
    seen = []

    def spawn(command, **kwargs):
        assert command[-1].endswith("run-product.ps1")
        log = kwargs["stdout"]
        seen.append(log.name)
        log.write("isolated launcher marker\n")
        return SimpleNamespace(poll=lambda: launcher_exit, returncode=launcher_exit)

    launch = Mock(side_effect=spawn)
    monkeypatch.setattr(startup.subprocess, "Popen", launch)
    ticks = [0, 241] if timeout else [0, 0]
    monkeypatch.setattr(startup.time, "monotonic", Mock(side_effect=ticks))
    monkeypatch.setattr(startup.time, "sleep", forbidden)
    return stop, launch, seen


def test_fresh_startups_preserve_old_failure_log_and_use_distinct_utc_logs(monkeypatch, capsys):
    startup.RUNTIME.mkdir(parents=True)
    legacy = startup.RUNTIME / "acceptance-launcher.log"
    legacy.write_bytes(b"previous crash\r\n")
    logs = []
    for _ in range(2):
        stop, launch, seen = startup_mocks(monkeypatch, ready_after=True)
        assert startup.ensure_product() == "launched"
        assert stop.call_count == launch.call_count == 1
        assert stop.call_args.args[0][-1].endswith("stop-product.ps1")
        assert launch.call_args.kwargs["creationflags"] == 0x08000000
        logs.extend(seen)
    assert legacy.read_bytes() == b"previous crash\r\n"
    assert len(set(logs)) == 2
    output = capsys.readouterr().out
    for name in logs:
        assert re.fullmatch(
            r"acceptance-launcher-\d{8}T\d{6}\.\d{6}Z-[0-9a-f]+\.log",
            startup.RUNTIME.joinpath(name).name,
        )
        assert startup.RUNTIME.joinpath(name).read_text() == "isolated launcher marker\n"
        assert name in output


@pytest.mark.parametrize("timeout", [False, True])
def test_failed_launcher_retains_old_log_and_identifies_its_new_log(monkeypatch, timeout):
    startup.RUNTIME.mkdir(parents=True)
    old = startup.RUNTIME / "acceptance-launcher.log"
    old.write_text("old failed startup", encoding="utf-8")
    _stop, _launch, seen = startup_mocks(
        monkeypatch, launcher_exit=None if timeout else 9, timeout=timeout
    )
    with pytest.raises(RuntimeError) as error:
        startup.ensure_product()
    assert old.read_text() == "old failed startup"
    assert len(seen) == 1
    assert seen[0] in str(error.value)
    assert startup.RUNTIME.joinpath(seen[0]).is_file()


def test_ready_instance_is_reused_without_any_process_or_launcher_log(monkeypatch):
    monkeypatch.setattr(startup, "product_ready", lambda: True)
    assert startup.ensure_product() == "reused"
    assert not startup.RUNTIME.exists()


def test_managed_but_degraded_roles_preserve_the_existing_product_and_logs(monkeypatch):
    startup.RUNTIME.mkdir(parents=True)
    (startup.RUNTIME / "supervisor.json").write_text('{"state":"running"}', encoding="utf-8")
    old = startup.RUNTIME / "acceptance-launcher.log"
    old.write_bytes(b"managed prior failure\r\n")
    monkeypatch.setattr(startup, "product_ready", lambda: False)
    identity = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(startup.subprocess, "run", identity)
    monkeypatch.setattr(startup.time, "monotonic", Mock(side_effect=[0, 0, 61]))
    monkeypatch.setattr(startup.time, "sleep", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="Managed product is degraded"):
        startup.ensure_product()
    assert identity.call_count == 1
    assert "Test-ProductProcessIdentity" in identity.call_args.args[0][-1]
    assert old.read_bytes() == b"managed prior failure\r\n"
    assert list(startup.RUNTIME.glob("acceptance-launcher-*.log")) == []
