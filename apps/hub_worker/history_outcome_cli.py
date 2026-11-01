"""Explicit outcome commands using pinned server grants; no implicit producer."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from apps.hub_api.history_outcome_bootstrap import _pinned_body, configured_history_outcome
from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand, _service


def parse_command(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("status", "register", "reconcile-one", "observe-one", "score", "read")
    )
    parser.add_argument("--database-path", required=True, type=Path)
    parser.add_argument("--data-directory", required=True, type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--protocol-id")
    parser.add_argument("--opportunity-id")
    parser.add_argument("--original-id")
    parser.add_argument("--offset", type=int, default=0)
    command = parser.parse_args(argv)
    if not command.database_path.is_absolute() or not command.data_directory.is_absolute():
        parser.error("history_outcome_cli_absolute_existing_paths_required")
    if not command.database_path.is_file() or not command.data_directory.is_dir():
        parser.error("history_outcome_cli_absolute_existing_paths_required")
    if command.action in ("register", "score") and (
        not command.request_id or len(command.request_id) > 128
    ):
        parser.error("history_outcome_cli_explicit_request_required")
    if (
        command.action != "register"
        and re.fullmatch(r"heval2_[a-f0-9]{48}", command.protocol_id or "") is None
    ):
        parser.error("history_outcome_cli_exact_protocol_required")
    if command.opportunity_id is not None and (
        command.action not in ("reconcile-one", "observe-one")
        or re.fullmatch(r"hopportunity2_[a-f0-9]{48}", command.opportunity_id) is None
    ):
        parser.error("history_outcome_cli_opportunity_identity_invalid")
    if (
        command.action == "read"
        and re.fullmatch(r"h(?:score|observation)2_[a-f0-9]{48}", command.original_id or "") is None
    ):
        parser.error("history_outcome_cli_exact_original_required")
    if not 0 <= command.offset <= 47:
        parser.error("history_outcome_cli_page_invalid")
    return command


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
            command.request_id or "outcome_explicit_read",
        )
        with _service(base) as commands:
            boot = configured_history_outcome(commands.database)
            if boot is None:
                raise ValueError("history_outcome_private_config_required")
            service = boot.service
            if command.action != "register" and command.protocol_id not in boot.protocol_ids:
                raise ValueError("history_outcome_protocol_not_granted")
            if command.action == "register":
                value = service.register(command.request_id)
                with service.commands._read_session() as session:
                    registration = service.fast_status(
                        session, value.protocol_id, offset=0, limit=1
                    )
                result = dict(
                    protocol_id=value.protocol_id,
                    protocol_hash=value.payload_hash,
                    first_hour=value.first_hour,
                    last_hour=value.last_hour,
                    requires_separate_protocol_grant=value.protocol_id not in boot.protocol_ids,
                    registration_state=registration["state"],
                    independently_confirmed_registered_at=registration[
                        "independently_confirmed_registered_at"
                    ],
                )
            elif command.action in ("reconcile-one", "observe-one"):
                from packages.kernel.decision_hub_kernel.application import history_outcome_jobs_v2

                result = history_outcome_jobs_v2.HistoryOutcomeJobsV2(service).process_one(
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
        outcome="read_verified"
        if command.action == "read"
        else "metadata_only"
        if command.action == "status"
        else "registered_confirmation_unconfirmed"
        if command.action == "register" and result["independently_confirmed_registered_at"] is None
        else "completed",
        read_only=command.action in ("read", "status"),
        committed=False if command.action in ("read", "status") else result.get("committed", True),
        automatic_retry=False,
        financial_qualified=False,
        historical_pit_verified=False,
        operator_owner_claim=owner,
        owner_authenticated=False,
        authority_scope="local_process_with_pinned_server_configuration",
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
                    error_code=code
                    if re.fullmatch(r"history_[a-z0-9_]{1,120}", code)
                    else type(error).__name__,
                    automatic_retry=False,
                    financial_qualified=False,
                )
            )
        )
        return 1
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
