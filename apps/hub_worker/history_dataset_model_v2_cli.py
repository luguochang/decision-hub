"""Explicit V2 mathematical preparation/fit/read; no HTTP or implicit migration."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand, _service
from packages.kernel.decision_hub_kernel.application.history_dataset_model_v2 import (
    HistoryDatasetModelV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)


class HistoryModelCommittedReadbackError(ValueError):
    pass


def parse_command(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare-fit", "fit", "read"))
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--dataset-id")
    parser.add_argument("--scope-id")
    parser.add_argument("--model-id")
    values = parser.parse_args(argv)
    if (
        values.action == "read"
        and not values.model_id
        or values.action != "read"
        and (not values.dataset_id or not values.scope_id)
    ):
        parser.error("history_v2_model_explicit_identity_required")
    return values


def execute_command(command):
    composition = HistoryObservedCliCommand(
        "status",
        command.database_path,
        command.data_directory,
        command.owner_id,
        command.request_id,
    )
    with _service(composition) as history:
        originals = HistoryDatasetV2Service(history, command.data_directory)
        models = HistoryDatasetModelV2Service(originals)
        if command.action == "prepare-fit":
            with history._read_session() as session:
                result = models.readiness(
                    session,
                    dataset_id=command.dataset_id,
                    owner_claim=command.owner_id,
                    scope_id=command.scope_id,
                )
            return {
                "operation": command.action,
                "committed": False,
                "read_only": True,
                "readiness": result,
                "financial_qualified": False,
                "historical_pit_verified": False,
            }
        if command.action == "fit":
            with history._read_session() as session:
                prepared = models.prepare(
                    session,
                    dataset_id=command.dataset_id,
                    owner_claim=command.owner_id,
                    request_id=command.request_id,
                    scope_id=command.scope_id,
                )
            with history._write_session() as session:
                value = models.record(session, prepared)
        else:
            with history._read_session() as session:
                value = models.read(session, command.model_id, command.owner_id)
                if value.request_id != command.request_id:
                    raise ValueError("history_v2_model_request_identity_invalid")
        try:
            with history._read_session() as session:
                loaded = models.read(session, value.model_id, command.owner_id)
            if loaded != value:
                raise ValueError("history_v2_model_committed_readback_changed")
        except Exception as error:
            if command.action == "fit":
                raise HistoryModelCommittedReadbackError(
                    "history_v2_model_committed_readback_unconfirmed"
                ) from error
            raise
    return {
        "operation": command.action,
        "committed": True,
        "read_only": command.action == "read",
        "model_id": value.model_id,
        "model_hash": value.payload_hash,
        "model_known_at": value.model_known_at,
        "dataset_id": value.dataset_id,
        "dataset_hash": value.dataset_hash,
        "scope_id": value.scope_id,
        "baseline_n": value.baseline.n,
        "conditional_low_n": value.conditional_low.n,
        "conditional_high_n": value.conditional_high.n,
        "financial_qualified": False,
        "historical_pit_verified": False,
        "owner_authenticated": False,
        "automatic_retry": False,
    }


def main(argv=None):
    command = parse_command(argv)
    try:
        receipt = execute_command(command)
    except Exception as error:
        raw = str(error)
        print(
            json.dumps(
                {
                    "operation": command.action,
                    "outcome": "committed_readback_unconfirmed"
                    if isinstance(error, HistoryModelCommittedReadbackError)
                    else "unconfirmed",
                    "error_code": raw
                    if re.fullmatch(r"history_[a-z0-9_]{1,110}", raw)
                    else type(error).__name__,
                    "committed": True
                    if isinstance(error, HistoryModelCommittedReadbackError)
                    else None,
                    "automatic_retry": False,
                    "financial_qualified": False,
                }
            )
        )
        return 1
    print(json.dumps(receipt, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
