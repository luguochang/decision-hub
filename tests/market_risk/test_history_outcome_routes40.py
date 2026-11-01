"""Signed API functions on real private service; no live HTTP or real model claim."""

import asyncio
import hashlib
import json
from datetime import timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event
from starlette.requests import Request

from apps.hub_api.history_outcome_bootstrap import (
    CONFIG_ENV,
    CONFIG_HASH_ENV,
    HistoryOutcomePrivateConfigV2,
    _pinned_body,
    configured_history_outcome,
)
from apps.hub_api.history_outcome_routes import OutcomeBootstrapV2, history_outcome_router
from apps.hub_api.history_owner_authority import HISTORY_COOKIE, HistoryOwnerAuthority
from apps.hub_api.history_product_routes import HistoryProductBootstrap
from apps.hub_worker.history_outcome_cli import parse_command
from tests.market_risk.test_history_outcome_service40 import fixture40


def test_actual_signed_fast_and_full_zero_forecast_reads_are_ro_and_grant_bound(
    tmp_path, monkeypatch
):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("signed-api-private")
    env.clock[0] = protocol.first_hour + timedelta(minutes=6)
    with env.service.commands._read_session() as session:
        packet = env.service.prepare_score(session, protocol.protocol_id, "signed-api-zero-pairs")
    saved = env.service.save(packet)[0]
    token, origin = "p" * 32, "http://127.0.0.1:8074"
    authority = HistoryOwnerAuthority(
        signing_key=b"s" * 32,
        pairing_sha256=hashlib.sha256(token.encode()).digest(),
        owner_identity=env.service.owner_claim,
        origin=origin,
    )
    cookie = authority.pair(token, origin=origin).cookie_value
    history = HistoryProductBootstrap(
        authority, env.service.originals, forecasts=env.service.forecasts
    )
    boot = OutcomeBootstrapV2(history, env.service, (protocol.protocol_id,))
    routes = history_outcome_router(boot).routes

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    def request(cookie_value=cookie):
        return Request(
            dict(
                type="http",
                method="GET",
                path="/v1/history/evaluations/",
                headers=[
                    (b"origin", origin.encode()),
                    (b"cookie", f"{HISTORY_COOKIE}={cookie_value}".encode()),
                ],
                query_string=b"",
            ),
            receive,
        )

    mutations, puts = [], []

    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip()
            .upper()
            .startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))
        ):
            mutations.append(statement)

    event.listen(env.db.engine, "before_cursor_execute", sql)

    def denied_put(_value):
        puts.append(1)
        raise AssertionError("GET must never archive.put")

    monkeypatch.setattr(env.service.archive, "put", denied_put)
    try:
        status = routes[0].endpoint(request(), protocol.protocol_id, 0, 30)
        assert len(status.opportunities) == 30 and not status.current_originals_verified
        reply = asyncio.run(routes[1].endpoint(request(), protocol.protocol_id, saved.score_id))
        assert reply.current_originals_verified and reply.score.paired_count == 0
        assert all(target.baseline_brier is None for target in reply.score.targets)
        for supplied, protocol_id in (
            ("forged", protocol.protocol_id),
            (cookie, "heval2_" + "e" * 48),
        ):
            with pytest.raises(HTTPException) as caught:
                routes[0].endpoint(request(supplied), protocol_id, 0, 30)
            assert caught.value.status_code == 403
        with pytest.raises(HTTPException) as caught:
            asyncio.run(routes[1].endpoint(request(), protocol.protocol_id, "hscore2_" + "e" * 48))
        assert caught.value.status_code == 404
        assert not puts and not mutations
    finally:
        event.remove(env.db.engine, "before_cursor_execute", sql)


def test_pinned_config_is_closed_strict_and_cli_parsing_never_initializes(tmp_path, monkeypatch):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    assert configured_history_outcome(None) is None
    value = dict(
        owner_identity="wire_only_owner",
        history_config_sha256="a" * 64,
        protocol_ids=["heval2_" + "b" * 48],
    )
    assert HistoryOutcomePrivateConfigV2.model_validate_json(
        json.dumps(value)
    ).protocol_ids == tuple(value["protocol_ids"])
    for invalid in (
        {**value, "owner_identity": ""},
        {**value, "extra": True},
        {**value, "protocol_ids": [*value["protocol_ids"], *value["protocol_ids"]]},
        {**value, "protocol_ids": ["hscore2_" + "b" * 48]},
    ):
        with pytest.raises(ValidationError):
            HistoryOutcomePrivateConfigV2.model_validate_json(json.dumps(invalid))
    path = tmp_path / "private-config.json"
    body = json.dumps(value).encode()
    path.write_bytes(body)
    monkeypatch.setenv(CONFIG_ENV, str(path))
    monkeypatch.setenv(CONFIG_HASH_ENV, hashlib.sha256(body).hexdigest())
    assert _pinned_body()[3].owner_identity == value["owner_identity"]
    path.write_bytes(body + b" ")
    with pytest.raises(ValueError, match="hash_mismatch"):
        _pinned_body()
    database = tmp_path / "parse-only.sqlite3"
    database.write_bytes(b"not a database; no init")
    argv = [
        "status",
        "--database-path",
        str(database),
        "--data-directory",
        str(tmp_path),
        "--protocol-id",
        value["protocol_ids"][0],
    ]
    assert parse_command(argv).action == "status" and database.read_bytes().startswith(
        b"not a database"
    )
    with pytest.raises(SystemExit):
        parse_command([*argv, "--offset", "48"])
