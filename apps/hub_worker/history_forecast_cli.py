"""Explicit local immutable history forecast commands; no migration, HTTP or fit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand, _service
from packages.kernel.decision_hub_kernel.application.history_observed_forecast import (
    HistoryForecastCommittedReadbackError,
    HistoryForecastLocalCommands,
)
from packages.kernel.decision_hub_kernel.ports.history_observed_forecast import (
    HistoryForecastIdentityV1,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, spot_scope

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class HistoryForecastCliCommand:
    action: Literal["record", "read"]
    database_path: Path
    data_directory: Path
    identity: HistoryForecastIdentityV1


def _clock(raw: str) -> datetime:
    try:
        return aware_utc(datetime.fromisoformat(raw))
    except ValueError as error:
        raise argparse.ArgumentTypeError("history_forecast_cli_aware_UTC_required") from error


def parse_command(argv: list[str] | None = None) -> HistoryForecastCliCommand:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "read"))
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--model-hash", required=True)
    parser.add_argument("--model-known-at", type=_clock, required=True)
    parser.add_argument("--cutoff-at", type=_clock, required=True)
    parser.add_argument("--threshold", choices=("0.02", "0.05"), required=True)
    values = parser.parse_args(argv)
    scopes = {scope.scope_id: scope for scope in (spot_scope("BTC-USDT"), spot_scope("ETH-USDT"))}
    if values.scope_id not in scopes:
        parser.error("history_forecast_cli_exact_spot_scope_required")
    identity = HistoryForecastIdentityV1(
        operator_owner_claim=values.owner_id,
        request_id=values.request_id,
        registration_id=values.registration_id,
        scope=scopes[values.scope_id],
        expected_model_hash=values.model_hash,
        expected_model_known_at=values.model_known_at,
        cutoff_at=values.cutoff_at,
        threshold=values.threshold,
    )
    return HistoryForecastCliCommand(
        values.action,
        values.database_path,
        values.data_directory,
        identity,
    )


def execute_command(command: HistoryForecastCliCommand) -> dict[str, object]:
    # The existing builder checks absolute existing paths/mode=rw and never
    # initializes a schema. The independent forecast ORM is explicitly imported
    # by its service, without changing the registered persistence/db.py.
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
        raise ValueError("history_forecast_cli_loaded_source_changed")
    identity = command.identity
    composition = HistoryObservedCliCommand(
        "status",
        command.database_path,
        command.data_directory,
        identity.operator_owner_claim,
        identity.request_id,
        identity.registration_id,
    )
    with _service(composition) as history:
        commands = HistoryForecastLocalCommands(history)
        value = commands.record(identity) if command.action == "record" else commands.read(identity)
    return {
        "schema_version": "history-forecast-local-command-receipt.v1",
        "operation": command.action,
        "committed": True,
        "read_only": command.action == "read",
        "forecast_id": value.forecast_id,
        "payload_hash": value.payload_hash,
        "registration_id": identity.registration_id,
        "scope_id": identity.scope.scope_id,
        "request_id": identity.request_id,
        "operator_owner_claim": identity.operator_owner_claim,
        "owner_authenticated": False,
        "authority_scope": value.authority_scope,
        "model_hash": value.forecast.model_hash,
        "model_known_at": value.forecast.model_known_at.isoformat(),
        "cutoff_at": value.forecast.cutoff_at.isoformat(),
        "threshold": value.forecast.threshold,
        "produced_at": value.forecast.produced_at.isoformat(),
        "recorded_at": value.recorded_at.isoformat(),
        "consumer_implementation_files": value.consumer_implementation_files,
        "cli_source_sha256": LOADED_SOURCE_SHA256,
        "training_mode": value.training_mode,
        "historical_pit_verified": False,
        "promotion_allowed": False,
        "financial_qualified": False,
        "originals_verified": True,
    }


def main(argv: list[str] | None = None) -> int:
    command = parse_command(argv)
    try:
        receipt = execute_command(command)
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, ValueError)
            and re.fullmatch(
                r"history_[a-z0-9_]{1,110}",
                str(error),
            )
            else type(error).__name__
        )
        print(
            json.dumps(
                {
                    "operation": command.action,
                    "outcome": "committed_readback_unconfirmed"
                    if isinstance(
                        error,
                        HistoryForecastCommittedReadbackError,
                    )
                    else "unconfirmed",
                    "error_code": code,
                    "automatic_retry": False,
                    "committed": True
                    if isinstance(error, HistoryForecastCommittedReadbackError)
                    else None,
                    "financial_qualified": False,
                }
            )
        )
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
