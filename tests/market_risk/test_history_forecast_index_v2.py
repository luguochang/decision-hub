"""Private signed metadata/contract controls; no live HTTP, originals or fit."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import event
from starlette.requests import Request

from apps.hub_api.history_forecast_index_routes import (
    HistoryForecastIndexV2Reader,
    history_forecast_index_router,
)
from apps.hub_api.history_owner_authority import HISTORY_COOKIE
from packages.contracts_py.decision_hub_contracts.generated.history_forecast_index_v2 import (
    HistoryForecastIndexV2,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import forecast_id
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from tests.market_risk.test_history_hourly_v2 import SCOPES, setup

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "apps/decision-desk/src/product/__fixtures__/history-forecast-index-control39.json"


def test_canonical_and_python_actual_frontend_fixture():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "contracts/schemas/history_forecast_index_v2.schema.yaml").read_text(
            encoding="utf-8"
        )
    )
    checker = FormatChecker()

    @checker.checks("date-time")
    def aware_date(value):
        from datetime import datetime

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None
        except (ValueError, TypeError):
            return False

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=checker).validate(data)
    value = HistoryForecastIndexV2.model_validate_json(json.dumps(data))
    assert value.scopes[0].probabilities is value.scopes[1].probabilities is None
    assert not value.current_originals_verified and not value.financial_qualified
    with pytest.raises(ValueError):
        value.financial_qualified = True

    def reverse(value):
        value["scopes"].reverse()

    def probabilities(value):
        value["scopes"][0]["probabilities"] = {"baseline_long": "0.2"}

    def identity(value):
        value["scopes"][0]["forecast_id"] = "hforecast2_bad"

    def finance(value):
        value["financial_qualified"] = True

    def state(value):
        value["scopes"][0]["state"] = "not_generated"

    def clock(value):
        value["scopes"][0]["recorded_at"] = "2026-10-09T15:36:59Z"

    def extra(value):
        value["unexpected"] = "no"

    def microseconds(value):
        value["observed_at"] = "2026-10-09T15:39:00.000001Z"
        value["scopes"][1]["recorded_at"] = "2026-10-09T15:39:00.000002Z"

    def nonminute(value):
        value["scopes"][0]["cutoff_at"] = "2026-10-09T15:37:00.000001Z"

    for mutate in (
        reverse,
        probabilities,
        identity,
        finance,
        state,
        clock,
        extra,
        microseconds,
        nonminute,
    ):
        invalid = copy.deepcopy(data)
        mutate(invalid)
        with pytest.raises(ValueError):
            HistoryForecastIndexV2.model_validate_json(json.dumps(invalid))


def test_actual_private_signed_metadata_two_bounded_headers(tmp_path, monkeypatch):
    db, commands, clock, boot = setup(tmp_path, monkeypatch, granted=True)
    owner = boot.authority.owner_identity
    model = boot.forecasts.grants[SCOPES[0]]
    cutoff = clock[0].replace(second=0, microsecond=0)
    reader = HistoryForecastIndexV2Reader(boot)
    assert [item.state for item in reader.read().scopes] == ["not_generated", "not_granted"]
    ids = []
    with commands._write_session() as session:
        for request, scope, grant, candidate_owner, recorded in (
            ("first", SCOPES[0], model, owner, cutoff + timedelta(seconds=1)),
            ("latest", SCOPES[0], model, owner, cutoff + timedelta(seconds=2)),
            ("prior-model", SCOPES[0], "hfit2_" + "a" * 48, owner, cutoff + timedelta(seconds=4)),
            ("other-scope", SCOPES[1], model, owner, cutoff + timedelta(seconds=4)),
            ("other-owner", SCOPES[0], model, "other_private_owner", cutoff + timedelta(seconds=4)),
        ):
            identity = forecast_id(candidate_owner, request)
            ids.append(identity)
            session.add(
                Forecast(
                    forecast_id=identity,
                    owner_claim=candidate_owner,
                    request_id=request,
                    scope_id=scope,
                    model_id=grant,
                    model_hash="b" * 64,
                    cutoff_at=cutoff,
                    produced_at=cutoff,
                    recorded_at=recorded,
                    payload_json="PRIVATE INVALID BODY MUST NOT BE PARSED",
                    payload_hash="c" * 64,
                    original_ref="PRIVATE MISSING ORIGINAL MUST NOT BE READ",
                    original_hash="d" * 64,
                )
            )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("metadata index attempted originals/producer/fit")

    for instance, method in (
        (boot.forecasts, "read"),
        (boot.forecasts, "producer_once"),
        (boot.forecasts.models, "read"),
        (boot.originals, "read_original"),
        (boot.originals.archive, "read"),
        (boot.originals.raw, "read"),
        (boot.forecasts.archive, "read"),
        (commands.ledger.builder.originals, "load"),
    ):
        monkeypatch.setattr(instance, method, forbidden)
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", observe)
    original_read = commands._read_session
    changes = []

    @contextmanager
    def checked_read():
        with original_read() as session:
            native = session.connection().connection.driver_connection
            before = native.total_changes
            yield session
            changes.append(native.total_changes - before)

    monkeypatch.setattr(commands, "_read_session", checked_read)
    router = history_forecast_index_router(boot)
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/v1/history/forecast-index"
    )

    def request(headers):
        return Request(
            {
                "type": "http",
                "headers": [
                    (key.lower().encode(), value.encode()) for key, value in headers.items()
                ],
            }
        )

    with pytest.raises(HTTPException) as denied:
        endpoint(request({"X-Owner-Id": owner}))
    assert denied.value.status_code == 403 and statements == []
    paired = boot.authority.pair("private-hour-control-token-000001", origin=boot.authority.origin)
    result = endpoint(
        request(
            {"Cookie": HISTORY_COOKIE + "=" + paired.cookie_value, "Origin": boot.authority.origin}
        )
    )
    assert result.scopes[0].forecast_id == ids[1]
    assert result.scopes[0].state == "verification_required"
    assert result.scopes[1].state == "not_granted"
    assert result.scopes[1].model_id is result.scopes[1].forecast_id is None
    assert not result.current_originals_verified and not result.financial_qualified
    assert result.scopes[0].probabilities is None and result.observed_at == clock[0]
    assert len(statements) == 1 and "LIMIT" in statements[0].upper()
    assert "payload_json" not in statements[0] and "original_ref" not in statements[0]
    assert changes == [0]
    with pytest.raises(HTTPException) as foreign:
        endpoint(
            request(
                {
                    "Cookie": HISTORY_COOKIE + "=" + paired.cookie_value,
                    "Origin": "http://127.0.0.1:1",
                }
            )
        )
    assert foreign.value.status_code == 403
    absent = history_forecast_index_router()
    with pytest.raises(HTTPException) as closed:
        absent.routes[0].endpoint(request({}))
    assert closed.value.status_code == 503
    event.remove(db.engine, "before_cursor_execute", observe)
    (tmp_path / "forecast-index-private-response.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    db.engine.dispose()
