"""Three bounded private controls: composition, signed API, existing risk lane.

Model headers and producer results are explicitly synthetic boundaries. These
controls do not authenticate a model/qualification, generate probabilities or
establish financial delivery or live-worker readiness.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response

from apps.hub_api.history_owner_authority import HISTORY_COOKIE
from apps.hub_api.history_product_bootstrap import CONFIG_ENV as PARENT_ENV
from apps.hub_api.history_product_bootstrap41 import (
    CONFIG_ENV,
    CONFIG_HASH_ENV,
    HistoryQualificationPrivateConfig41,
    _policy41,
    configured_history_product41,
)
from apps.hub_api.history_product_routes import HistoryFastStatus
from apps.hub_api.history_product_routes41 import history_product_router41
from apps.hub_worker.history_hourly41 import HistoryHourly41, install_history_hourly41_on_risk_lane
from apps.hub_worker.history_qualification_cli41 import parse_command
from packages.contracts_py.decision_hub_contracts.generated.history_product_status_v2 import (
    HistoryOwnerSessionPairV2,
    HistoryProductStatusV2,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastCommittedReadbackError,
)
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as Model,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryDatasetV2OriginalRecord as Original,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord as Slot,
)

OWNER = "wiring41_private_owner"
ORIGIN = "http://127.0.0.1:8898"
TOKEN = "wiring41_private_pairing_token_00001"
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"
MODEL = "hfit2_" + "a" * 48
QUALIFICATION = "hqual3_" + "b" * 48


def setup(tmp_path, monkeypatch, *, serving=True, allow=False):
    def reject(*_args, **_kwargs):
        raise AssertionError("private wiring41 control attempted HTTP")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    data = tmp_path / "data"
    (data / "db").mkdir(parents=True)
    database = Database("sqlite+pysqlite:///" + (data / "db" / "private.sqlite3").as_posix())
    Base.metadata.create_all(
        database.engine,
        tables=[Model.__table__, Original.__table__, Forecast.__table__, Slot.__table__],
    )
    now = datetime.now(UTC)
    with database.factory() as session:
        session.add(
            Original(
                original_id="hds2_dataset_" + "c" * 48,
                kind="dataset",
                owner_claim=OWNER,
                request_id="synthetic_header_only",
                plan_id="hds2_plan_" + "d" * 48,
                payload_json=json.dumps({"provenance": {"fixture_mode": False}}),
                payload_hash="0" * 64,
                original_ref="not_a_real_original",
                original_hash="0" * 64,
                recorded_at=now,
            )
        )
        session.add(
            Model(
                model_id=MODEL,
                owner_claim=OWNER,
                request_id="synthetic_header_only",
                scope_id=SCOPE,
                plan_id="hds2_plan_" + "d" * 48,
                dataset_id="hds2_dataset_" + "c" * 48,
                payload_json="{}",
                payload_hash="0" * 64,
                original_ref="not_a_real_original",
                original_hash="0" * 64,
                known_at=now,
                recorded_at=now,
            )
        )
        session.commit()
    parent = tmp_path / "history-private.json"
    parent.write_text(
        json.dumps(
            dict(
                owner_identity=OWNER,
                origin=ORIGIN,
                signing_key_hex="31" * 32,
                pairing_sha256_hex=hashlib.sha256(TOKEN.encode()).hexdigest(),
                model_grants={SCOPE: MODEL},
                allow_producer=allow,
            )
        ),
        encoding="utf-8",
    )
    policy = tmp_path / "qualification-private.json"
    policy.write_text(
        json.dumps(
            dict(
                publication_grants={SCOPE: MODEL},
                serving_grants={SCOPE: QUALIFICATION} if serving else {},
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(PARENT_ENV, str(parent))
    monkeypatch.setenv(PARENT_ENV + "_SHA256", hashlib.sha256(parent.read_bytes()).hexdigest())
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(data))
    monkeypatch.setenv(CONFIG_ENV, str(policy))
    monkeypatch.setenv(CONFIG_HASH_ENV, hashlib.sha256(policy.read_bytes()).hexdigest())
    return database, configured_history_product41(database), policy


def request(cookie=None, csrf=None, *, origin=ORIGIN):
    headers = [(b"origin", origin.encode())]
    if cookie is not None:
        headers.append((b"cookie", f"{HISTORY_COOKIE}={cookie}".encode()))
    if csrf is not None:
        headers.append((b"x-history-csrf", csrf.encode()))
    return Request(
        {
            "type": "http",
            "headers": headers,
            "method": "POST",
            "path": "/v1/history/assets/BTC/refresh",
            "scheme": "http",
        }
    )


def endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def test_actual_private_factory_shape_and_no_implicit_execution(tmp_path, monkeypatch):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    assert configured_history_product41(None) is None
    for bad in (
        {"publication_grants": {}, "serving_grants": {}, "extra": 1},
        {"publication_grants": {"BTC": MODEL}, "serving_grants": {}},
        {"publication_grants": {}, "serving_grants": {SCOPE: MODEL}},
    ):
        with pytest.raises(ValueError):
            HistoryQualificationPrivateConfig41.model_validate_json(json.dumps(bad))
    database, boot, path = setup(tmp_path, monkeypatch)
    try:
        assert boot.qualifications.forecasts is boot.legacy.forecasts
        assert boot.forecasts.originals is boot.originals
        assert boot.forecasts.owner_claim == OWNER and not boot.originals.fixture_mode
        assert dict(boot.serving_grants) == {SCOPE: QUALIFICATION}
        with database.factory() as session:
            assert session.scalar(select(Forecast.forecast_id)) is None
        # Configured headers are not qualification authentication: no bundle,
        # feature read, write or producer call occurs in this construction.
        args = [
            "prepare",
            "--database-path",
            str(database.engine.url.database),
            "--data-directory",
            str(boot.originals.data_directory),
            "--request-id",
            "private_prepare",
            "--model-id",
            MODEL,
        ]
        assert parse_command(args).model_ids == [MODEL]
        with pytest.raises(SystemExit):
            parse_command(args + ["--qualification-id", QUALIFICATION])
        monkeypatch.setenv(CONFIG_HASH_ENV, "0" * 64)
        with pytest.raises(ValueError, match="hash_mismatch"):
            _policy41()
        monkeypatch.setenv(CONFIG_HASH_ENV, hashlib.sha256(path.read_bytes()).hexdigest())
        body = path.read_bytes()
        path.write_bytes(body + b" ")
        with pytest.raises(ValueError, match="pinned_policy_changed"):
            boot.__post_init__()
        path.write_bytes(body)
    finally:
        database.engine.dispose()


def test_actual_signed_owner_refresh_one_call_and_unknown_semantics(tmp_path, monkeypatch):
    database, boot, _ = setup(tmp_path, monkeypatch, allow=True)
    try:
        router = history_product_router41(boot)
        refresh = endpoint(router, "/v1/history/assets/{asset}/refresh")
        pair = endpoint(router, "/v1/history/owner-session")
        paired = pair(HistoryOwnerSessionPairV2(pairing_token=TOKEN), request(), Response())
        signed = boot.authority.pair(TOKEN, origin=ORIGIN)
        assert paired.csrf_token and signed.csrf_token
        # Validate the exact wire status and ensure this route never invokes
        # a full model/qualification/forecast reader. The metadata fixture is
        # explicit; synthetic model headers above are not valid model bodies.
        payload = HistoryProductStatusV2.model_validate_json(
            (
                Path(__file__).resolve().parents[2] / "apps/decision-desk/src/product/__fixtures__/"
                "history-product-status-v2.json"
            ).read_text(encoding="utf-8")
        )
        monkeypatch.setattr(HistoryFastStatus, "status", lambda *_args, **_kwargs: payload)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("fast status invoked a full reader or archive write")

        monkeypatch.setattr(boot.qualifications, "read", forbidden)
        monkeypatch.setattr(boot.forecasts, "read", forbidden)
        monkeypatch.setattr(
            boot.originals.models if hasattr(boot.originals, "models") else boot.forecasts.models,
            "read",
            forbidden,
        )
        monkeypatch.setattr(boot.forecasts.archive, "put", forbidden)
        status = endpoint(router, "/v1/history/status")(request(signed.cookie_value), 0, 30, 0, 30)
        assert not status.data.current_originals_verified and status.forecast.probabilities is None
        assert status.actions.can_refresh and not status.actions.can_forecast
        with pytest.raises(HTTPException) as rejected:
            refresh("BTC", request())
        assert rejected.value.status_code == 403
        with pytest.raises(HTTPException) as rejected:
            refresh("BTC", request(signed.cookie_value, signed.csrf_token, origin=ORIGIN + "0"))
        assert rejected.value.status_code == 403
        calls = []

        def unknown(scope):
            calls.append(scope)
            raise ValueError("history41_private_unknown_commit")

        # Boundary stub only; actual route/authority/strict reply and executor
        # instance types remain real. No successful forecast is fabricated.
        monkeypatch.setattr(boot.forecasts, "producer_once", unknown)
        result = refresh("BTC", request(signed.cookie_value, signed.csrf_token))
        assert calls == [SCOPE] and result.committed is None
        assert result.outcome == "unconfirmed" and not result.frequencies
        assert not result.automatic_retry

        def known(scope):
            calls.append(scope)
            error = HistoryDatasetForecastCommittedReadbackError("history41_private_known_commit")
            error.forecast_id = "hforecast2_" + "e" * 48
            error.cutoff_at = datetime.now(UTC).replace(second=0, microsecond=0)
            raise error

        monkeypatch.setattr(boot.forecasts, "producer_once", known)
        result = refresh("BTC", request(signed.cookie_value, signed.csrf_token))
        assert len(calls) == 2 and result.committed is True
        assert result.outcome == "committed_readback_unconfirmed"
        assert not result.frequencies and not result.automatic_retry
        with pytest.raises(HTTPException) as rejected:
            refresh("ETH", request(signed.cookie_value, signed.csrf_token))
        assert rejected.value.status_code == 403 and len(calls) == 2
    finally:
        database.engine.dispose()


def test_existing_risk_lane_callback_dual_fences_and_cancel_join(tmp_path, monkeypatch):
    database, boot, _ = setup(tmp_path, monkeypatch, serving=False)
    try:
        tick = HistoryHourly41(boot)
        # Existing 39 unknown fences protect both fixed scopes across versions.
        from apps.hub_worker.history_hourly41 import SCOPES

        legacy_fences = [boot.legacy.forecasts.writer._fence_path(scope) for scope in SCOPES]
        for legacy_fence in legacy_fences:
            legacy_fence.parent.mkdir(parents=True, exist_ok=True)
            legacy_fence.write_text("{}", encoding="utf-8")
        assert tick.process_one() is None
        for legacy_fence in legacy_fences:
            legacy_fence.unlink()
        current_fences = [boot.forecasts.writer._fence_path(scope) for scope in SCOPES]
        for current_fence in current_fences:
            current_fence.parent.mkdir(parents=True, exist_ok=True)
            current_fence.write_text("{}", encoding="utf-8")
        assert tick.process_one() is None
        for current_fence in current_fences:
            current_fence.unlink()
        # No serving grant never authenticates a model, yet the real single
        # slot audit is retained without producing a forecast or resetting it.
        result = tick.process_one()
        assert result.status == "blocked" and tick.process_one().status == "blocked"
        assert tick.process_one() is None
        with boot.originals.commands._read_session() as session:
            assert session.scalar(select(Forecast.forecast_id)) is None
        started, finished = threading.Event(), threading.Event()

        def slow(*, cancel, budget_seconds):
            started.set()
            assert cancel.wait(1)
            time.sleep(0.015)
            finished.set()
            return None

        monkeypatch.setattr(tick, "process_one", slow)

        async def cancel_check():
            task = asyncio.create_task(tick.tick(1))
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set() and not tick._busy

        asyncio.run(cancel_check())
        order = []

        async def original():
            order.append("original")
            return 4

        scheduler = SimpleNamespace(risk_enhancement_tick=original)
        installed = install_history_hourly41_on_risk_lane(
            SimpleNamespace(scheduler=scheduler), database, bootstrap=boot
        )

        async def once(budget):
            assert 1 <= budget <= 90
            order.append("qualified_tick")
            return 1

        monkeypatch.setattr(installed, "tick", once)
        assert asyncio.run(scheduler.risk_enhancement_tick()) == 5
        assert order == ["original", "qualified_tick"]
        with pytest.raises(ValueError, match="already_bound"):
            install_history_hourly41_on_risk_lane(
                SimpleNamespace(scheduler=scheduler), database, bootstrap=boot
            )
    finally:
        database.engine.dispose()


def test_cli41_exact_audit_error_code_preserved_without_arbitrary_exception_text(
    monkeypatch, capsys
):
    import apps.hub_worker.history_qualification_cli41 as cli

    monkeypatch.setattr(cli, "parse_command", lambda _argv: SimpleNamespace(action="publish"))

    def execute_code(code):
        def failure(_command):
            raise ValueError(code)

        monkeypatch.setattr(cli, "execute_command", failure)
        assert cli.main([]) == 1
        return json.loads(capsys.readouterr().out)

    for code in ("history41_cli_total_budget_reached", "history_qualification_not_confirmed"):
        result = execute_code(code)
        assert result["error_code"] == code
        assert result["committed"] is None and not result["automatic_retry"]
    result = execute_code("private arbitrary exception path and credentials must remain hidden")
    assert result["error_code"] == "ValueError" and "private" not in json.dumps(result)
