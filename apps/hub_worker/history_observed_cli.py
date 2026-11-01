"""Explicit local history-at-fit commands; never migrate, collect or auto-fit.

This local process uses the operator's filesystem access. The supplied owner is
a claim/lease label, not an authenticated or persisted owner principal. Existing
protocol/source/original and preparation/writer gates remain authoritative.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.history_observed_commands import (
        HistoryObservedCommandService,
    )
    from packages.kernel.decision_hub_kernel.ports.history_observed_training import (
        HistoryObservedDatasetManifestV1,
        HistoryObservedTrainingProtocolV1,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import MarketPathScope

Action = Literal["register", "activate", "status", "freeze", "fit"]
JsonObject = dict[str, object]


@dataclass(frozen=True)
class HistoryObservedCliCommand:
    action: Action
    database_path: Path
    data_directory: Path
    owner_id: str
    request_id: str
    registration_id: str | None = None
    scope_id: str | None = None


def _identity(value: str, maximum: int) -> str:
    # Preserve the registered request text; no stripping or generated replacement.
    if not value or len(value) > maximum:
        raise argparse.ArgumentTypeError("history_observed_cli_identity_invalid")
    return value


def parse_command(argv: Sequence[str] | None = None) -> HistoryObservedCliCommand:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("register", "activate", "status", "freeze", "fit"):
        command = actions.add_parser(action)
        command.add_argument("--database-path", type=Path, required=True)
        command.add_argument("--data-directory", type=Path, required=True)
        command.add_argument(
            "--owner-id", type=lambda value: _identity(value, 128), required=True,
        )
        command.add_argument(
            "--request-id", type=lambda value: _identity(value, 128), required=True,
        )
        if action != "register":
            command.add_argument(
                "--registration-id", type=lambda value: _identity(value, 80),
                required=True,
            )
        if action in {"freeze", "fit"}:
            command.add_argument(
                "--scope-id", type=lambda value: _identity(value, 256), required=True,
            )
    values = parser.parse_args(argv)
    return HistoryObservedCliCommand(
        cast(Action, values.action), values.database_path, values.data_directory,
        values.owner_id, values.request_id,
        getattr(values, "registration_id", None), getattr(values, "scope_id", None),
    )


@contextmanager
def _service(command: HistoryObservedCliCommand) -> Iterator[HistoryObservedCommandService]:
    # Explicit existing paths and mode=rw prevent a typo from creating a new DB.
    # No initialize/create_all; schema installation belongs to the release owner.
    if not command.database_path.is_absolute() or not command.data_directory.is_absolute():
        raise ValueError("history_observed_cli_absolute_paths_required")
    database_path = command.database_path.resolve(strict=True)
    data_directory = command.data_directory.resolve(strict=True)
    if not database_path.is_file() or not data_directory.is_dir():
        raise ValueError("history_observed_cli_existing_paths_required")
    if not (data_directory / "db").is_dir():
        raise ValueError("history_observed_cli_existing_database_directory_required")
    from sqlalchemy import URL

    from apps.hub_worker.composition import build_history_observed_worker
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    database = None
    try:
        previous = os.environ.get("DECISION_HUB_DATA_DIR")
        os.environ["DECISION_HUB_DATA_DIR"] = str(data_directory)
        try:
            url = URL.create(
                "sqlite+pysqlite", database=database_path.as_uri(),
                query={"mode": "rw", "uri": "true"},
            )
            database = Database(url.render_as_string(hide_password=False))
            commands = build_history_observed_worker(database, owner_id=command.owner_id)
        finally:
            if previous is None:
                os.environ.pop("DECISION_HUB_DATA_DIR", None)
            else:
                os.environ["DECISION_HUB_DATA_DIR"] = previous
        yield commands
    finally:
        if database is not None:
            database.engine.dispose()


def _registration(
    commands: HistoryObservedCommandService, command: HistoryObservedCliCommand,
) -> tuple[HistoryObservedTrainingProtocolV1, MarketPathScope | None]:
    if command.registration_id is None:
        raise ValueError("history_observed_cli_registration_required")
    # The same sealed transaction helper supplies physical RO and cleanup.
    with commands._read_session() as session:  # pyright: ignore[reportPrivateUsage]
        protocol = commands.ledger.protocol(session, command.registration_id)
        if protocol.request_id != command.request_id:
            raise ValueError("history_observed_cli_registered_request_mismatch")
        if command.action not in {"freeze", "fit"}:
            return protocol, None
        scopes = [scope for scope in protocol.scopes if scope.scope_id == command.scope_id]
        if len(scopes) != 1:
            raise ValueError("history_observed_cli_registered_scope_mismatch")
        return protocol, scopes[0]


def _freeze(
    commands: HistoryObservedCommandService, protocol: HistoryObservedTrainingProtocolV1,
    scope: MarketPathScope,
) -> HistoryObservedDatasetManifestV1:
    from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc

    # Saved values go through full original/readset preparation, never metadata.
    with commands._read_session() as session:  # pyright: ignore[reportPrivateUsage]
        try:
            prepared = commands.ledger.prepare_saved(session, protocol.registration_id, scope)
        except ValueError as error:
            if str(error) != "history_observed_dataset_missing":
                raise
            prepared = None
    if prepared is not None:
        with commands._write_session() as session:  # pyright: ignore[reportPrivateUsage]
            value = commands.ledger.freeze(session, prepared)
        return value
    known_by = min(
        aware_utc(commands.ledger.clock()),
        protocol.registered_at + timedelta(seconds=protocol.acquisition_deadline_seconds),
    )
    return commands.freeze(protocol.registration_id, scope, known_by)


def execute_command(
    commands: HistoryObservedCommandService, command: HistoryObservedCliCommand,
) -> JsonObject:
    """Return only actual committed identities or explicitly read-only metadata."""
    if commands.acquisition.owner != command.owner_id:
        raise ValueError("history_observed_cli_owner_composition_mismatch")
    receipt: JsonObject = {
        "schema_version": "history-observed-local-command-receipt.v1",
        "operation": command.action, "request_id": command.request_id,
        "request_id_scope": "registration_request",
        "operator_owner_claim": command.owner_id,
        "owner_authenticated": False, "owner_persisted": False,
        "authority_scope": "local_process_filesystem_access",
        "training_mode": "history_observed_at_fit", "historical_pit_verified": False,
        "promotion_allowed": False, "financial_qualified": False,
        "ready_for_real_fit": False,
    }
    if command.action == "register":
        protocol = commands.register(command.request_id)
        receipt.update({
            "registration_id": protocol.registration_id,
            "protocol_hash": protocol.payload_hash, "registered_at": protocol.registered_at,
            "deadline_at": protocol.registered_at + timedelta(
                seconds=protocol.acquisition_deadline_seconds,
            ),
            "scope_ids": [scope.scope_id for scope in protocol.scopes],
            "source_admission": protocol.source_admission, "outcome": "registered_or_existing",
            "committed": True,
        })
        return receipt
    protocol, scope = _registration(commands, command)
    receipt.update({
        "registration_id": protocol.registration_id, "protocol_hash": protocol.payload_hash,
        "registered_at": protocol.registered_at,
    })
    if command.action == "activate":
        changed = commands.activate(protocol.registration_id)
        receipt.update({"outcome": "held_jobs_transition_committed", "committed": True,
                        "changed_held_jobs": changed, "collection_started": False})
    elif command.action == "status":
        status = commands.status(protocol.registration_id)
        receipt.update({
            "outcome": "registered_metadata_read", "committed": False,
            "as_of": status.as_of, "deadline_at": status.deadline_at,
            "state_counts": dict(status.state_counts), "attempts": status.attempts,
            "proof_scope": status.proof_scope,
            "dataset_originals_verified": status.dataset_originals_verified,
            "model_originals_verified": status.model_originals_verified,
            "stored": [{"kind": row.kind, "scope_id": row.scope_id,
                        "payload_hash": row.payload_hash, "known_at": row.known_at,
                        "recorded_at": row.recorded_at} for row in status.stored],
        })
    elif command.action == "freeze" and scope is not None:
        dataset = _freeze(commands, protocol, scope)
        receipt.update({
            "outcome": "dataset_original_committed", "committed": True,
            "scope_id": dataset.scope.scope_id, "dataset_hash": dataset.payload_hash,
            "data_information_asof": dataset.data_information_asof,
            "derived_at": dataset.derived_at, "sealed_at": dataset.sealed_at,
            "source_admission": "not_evaluated",
        })
    elif command.action == "fit" and scope is not None:
        model = commands.fit(protocol.registration_id, scope)
        receipt.update({
            "outcome": "model_original_committed", "committed": True,
            "scope_id": model.fit.admission.scope.scope_id, "model_hash": model.payload_hash,
            "dataset_hash": model.fit.admission.dataset_hash,
            "fit_started_at": model.fit.fit_started_at,
            "fit_completed_at": model.fit.fit_completed_at, "model_known_at": model.model_known_at,
            "source_admission": model.fit.admission.source_admission,
            "execution_eligible_for_prospective_use": model.execution_eligible_for_prospective_use,
        })
    else:
        raise ValueError("history_observed_cli_operation_invalid")
    return receipt


def _json_default(value: object) -> str:
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.isoformat()
    raise TypeError("history_observed_cli_json_type_invalid")


def run(argv: Sequence[str] | None = None) -> int:
    command = parse_command(argv)
    try:
        with _service(command) as commands:
            receipt = execute_command(commands, command)
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) else ""
        if not re.fullmatch(r"(?:history_observed|market_path)_[a-zA-Z0-9_]{1,100}", code):
            code = "history_observed_cli_operation_unconfirmed"
        # Do not print SQL/parameters, paths, raw bodies, signing material or URLs.
        print(json.dumps({
            "schema_version": "history-observed-local-command-receipt.v1",
            "operation": command.action, "request_id": command.request_id,
            "request_id_scope": "registration_request",
            "operator_owner_claim": command.owner_id,
            "owner_authenticated": False, "owner_persisted": False,
            "outcome": "unconfirmed", "error_code": code,
            "committed": None if command.action != "status" else False,
            "automatic_retry": False, "ready_for_real_fit": False,
            "financial_qualified": False,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(receipt, default=_json_default, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
