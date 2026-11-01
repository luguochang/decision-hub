"""Exact qualified outcome commands; no implicit registration, reset or forecast."""

import hashlib
import json
import os
import re
from pathlib import Path

from apps.hub_api.history_outcome_bootstrap import _pinned_body
from apps.hub_api.history_outcome_bootstrap41 import configured_history_outcome41
from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand, _service
from apps.hub_worker.history_outcome_cli import parse_command
from packages.kernel.decision_hub_kernel.application.history_outcome_jobs_v2 import (
    HistoryOutcomeJobsV2,
)
from packages.kernel.decision_hub_kernel.application.history_outcome_recovery_v2 import (
    HistoryOutcomeRecoveryV2,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def execute_command(command):
    pinned = _pinned_body()
    if pinned is None:
        raise ValueError("history_outcome_private_config_required")
    owner = pinned[3].owner_identity
    previous = os.environ.get("DECISION_HUB_DATA_DIR")
    os.environ["DECISION_HUB_DATA_DIR"] = str(command.data_directory.resolve(strict=True))
    try:
        base = HistoryObservedCliCommand(
            "status",
            command.database_path,
            command.data_directory,
            owner,
            command.request_id or "outcome41_explicit_read",
        )
        with _service(base) as commands:
            boot = configured_history_outcome41(commands.database)
            if boot is None:
                raise ValueError("history_outcome_private_config_required")
            service = boot.service
            if command.action != "register" and command.protocol_id not in boot.protocol_ids:
                raise ValueError("history_outcome_protocol_not_granted")
            if command.action == "register":
                value = service.register(command.request_id)
                with service.commands._read_session() as session:
                    metadata = service.fast_status(session, value.protocol_id, offset=0, limit=1)
                result = dict(
                    protocol_id=value.protocol_id,
                    protocol_hash=value.payload_hash,
                    first_hour=value.first_hour,
                    last_hour=value.last_hour,
                    independently_confirmed_registered_at=metadata[
                        "independently_confirmed_registered_at"
                    ],
                    requires_separate_protocol_grant=value.protocol_id not in boot.protocol_ids,
                )
            elif command.action in ("reconcile-one", "observe-one"):
                result = HistoryOutcomeRecoveryV2(HistoryOutcomeJobsV2(service)).process_one(
                    command.protocol_id,
                    "reconcile" if command.action == "reconcile-one" else "observe",
                    opportunity_id=command.opportunity_id,
                )
            elif command.action == "score":
                with service.commands._read_session() as session:
                    packet = service.prepare_score(session, command.protocol_id, command.request_id)
                value = service.save(packet)[0]
                result = dict(
                    score_id=value.score_id,
                    payload_hash=value.payload_hash,
                    paired_count=value.paired_count,
                    expected_total=48,
                )
            elif command.action == "status":
                with service.commands._read_session() as session:
                    result = service.fast_status(
                        session, command.protocol_id, offset=command.offset, limit=30
                    )
            else:
                with service.commands._read_session() as session:
                    method = (
                        service.read_score
                        if command.original_id.startswith("hscore2_")
                        else service.read_observation
                    )
                    value = method(session, command.original_id)
                    if value.protocol.identifier != command.protocol_id:
                        raise ValueError("history_outcome_cli_original_protocol_mismatch")
                    result = value.model_dump(mode="json")
    finally:
        if previous is None:
            os.environ.pop("DECISION_HUB_DATA_DIR", None)
        else:
            os.environ["DECISION_HUB_DATA_DIR"] = previous
    return dict(
        operation=command.action,
        outcome="metadata_only"
        if command.action == "status"
        else "read_verified"
        if command.action == "read"
        else "completed",
        read_only=command.action in ("status", "read"),
        committed=False if command.action in ("status", "read") else result.get("committed", True),
        automatic_retry=False,
        financial_qualified=False,
        historical_pit_verified=False,
        owner_authenticated=False,
        authority_scope="local_process_with_pinned_server_configuration",
        operator_owner_claim=owner,
        result=result,
    )


def main(argv=None):
    command = parse_command(argv)
    try:
        result = execute_command(command)
    except Exception as error:
        code = str(error)
        print(
            json.dumps(
                dict(
                    operation=command.action,
                    outcome="unconfirmed",
                    committed=getattr(error, "committed", None),
                    automatic_retry=False,
                    error_code=code
                    if re.fullmatch(r"(?:history|history41)_[a-z0-9_]{1,120}", code)
                    else type(error).__name__,
                    financial_qualified=False,
                )
            )
        )
        return 1
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
