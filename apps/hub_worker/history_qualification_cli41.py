"""Explicit administrative qualification commands; no HTTP, fit or migration.

Prepare is a bounded preview. Publish prepares and saves within the same
process and fixed total budget; an opaque preparation is never loaded from a
user file. Runtime serving remains a separate server-private grant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from time import monotonic

from apps.hub_api.history_product_bootstrap import (
    CONFIG_ENV as HISTORY_CONFIG_ENV,
)
from apps.hub_api.history_product_bootstrap import (
    HistoryProductPrivateConfig,
)
from apps.hub_api.history_product_bootstrap41 import configured_history_product41
from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand, _service

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryQualificationCliCommittedReadbackError(ValueError):
    committed = True


def parse_command(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "publish", "read", "audit"))
    parser.add_argument("--database-path", required=True, type=Path)
    parser.add_argument("--data-directory", required=True, type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--model-id", action="append", dest="model_ids")
    parser.add_argument("--qualification-id")
    parser.add_argument("--budget-seconds", type=float, default=180.0)
    command = parser.parse_args(argv)
    if (
        not command.database_path.is_absolute()
        or not command.database_path.is_file()
        or not command.data_directory.is_absolute()
        or not command.data_directory.is_dir()
    ):
        parser.error("history41_cli_absolute_existing_paths_required")
    if not 1 <= command.budget_seconds <= 180:
        parser.error("history41_cli_budget_1_to_180_required")
    if command.action in ("prepare", "publish"):
        if (
            not command.request_id
            or len(command.request_id) > 128
            or not command.model_ids
            or len(command.model_ids) > 2
            or len(set(command.model_ids)) != len(command.model_ids)
            or any(
                re.fullmatch(r"hfit2_[a-f0-9]{48}", model) is None for model in command.model_ids
            )
            or command.qualification_id is not None
        ):
            parser.error("history41_cli_explicit_publication_identity_required")
    elif (
        re.fullmatch(r"hqual3_[a-f0-9]{48}", command.qualification_id or "") is None
        or command.model_ids is not None
        or command.request_id is not None
    ):
        parser.error("history41_cli_exact_qualification_read_required")
    return command


def _operator_owner():
    configured = os.getenv(HISTORY_CONFIG_ENV)
    if not configured or not Path(configured).is_absolute():
        raise ValueError("history41_trusted_parent_config_required")
    with Path(configured).resolve(strict=True).open("rb") as handle:
        body = handle.read(32769)
    if len(body) > 32768:
        raise ValueError("history41_parent_config_size_exceeded")
    # The actual factory checks all parent resources and grants again. Never
    # print the parent private fields or accept an owner from command arguments.
    return HistoryProductPrivateConfig.model_validate_json(body).owner_identity


def execute_command(command):
    started = monotonic()
    deadline = started + command.budget_seconds

    def check():
        if monotonic() >= deadline:
            raise ValueError("history41_cli_total_budget_reached")
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
            raise ValueError("history41_cli_loaded_source_changed")

    owner = _operator_owner()
    previous = os.environ.get("DECISION_HUB_DATA_DIR")
    os.environ["DECISION_HUB_DATA_DIR"] = str(command.data_directory.resolve(strict=True))
    try:
        base = HistoryObservedCliCommand(
            "status",
            command.database_path,
            command.data_directory,
            owner,
            command.request_id or "qualification_explicit_read",
        )
        with _service(base) as commands:
            boot = configured_history_product41(commands.database)
            if boot is None or boot.authority.owner_identity != owner:
                raise ValueError("history41_trusted_qualification_config_required")
            service = boot.qualifications
            check()
            if command.action in ("prepare", "publish"):
                prepared = service.prepare(
                    model_ids=tuple(command.model_ids),
                    request_id=command.request_id,
                    budget_seconds=min(180.0, deadline - monotonic()),
                    checkpoint=check,
                )
                check()
                if command.action == "publish":
                    values = service.publish(
                        prepared,
                        budget_seconds=min(180.0, deadline - monotonic()),
                        checkpoint=check,
                    )
                    result = {"qualifications": [item.model_dump(mode="json") for item in values]}
                else:
                    result = dict(
                        request_id=prepared.request_id,
                        bundle_ref=prepared.bundle_ref,
                        bundle_hash=prepared.bundle_hash,
                        bundle_bytes=prepared.bundle_bytes,
                        manifest_hash=prepared.manifest_hash,
                        verified_at=prepared.verified_at,
                        model_ids=command.model_ids,
                        confirmation_state="not_published",
                    )
            elif command.action == "read":
                with service.commands._read_session() as session:
                    value, event, bundle, model = service.read(
                        session,
                        command.qualification_id,
                        checkpoint=check,
                    )
                result = dict(
                    qualification=value.model_dump(mode="json"),
                    acceptance=event.model_dump(mode="json"),
                    bundle_hash=value.bundle_hash,
                    model_id=model.model_id,
                )
            else:
                result = service.audit(
                    command.qualification_id,
                    budget_seconds=min(180.0, deadline - monotonic()),
                    checkpoint=check,
                )
            try:
                check()
            except Exception as error:
                if command.action == "publish":
                    raise HistoryQualificationCliCommittedReadbackError(
                        "history41_cli_committed_readback_unconfirmed"
                    ) from error
                raise
    finally:
        if previous is None:
            os.environ.pop("DECISION_HUB_DATA_DIR", None)
        else:
            os.environ["DECISION_HUB_DATA_DIR"] = previous
    return dict(
        operation=command.action,
        outcome="published_confirmed"
        if command.action == "publish"
        else "read_verified"
        if command.action in ("read", "audit")
        else "prepared_not_published",
        committed=command.action == "publish",
        read_only=command.action in ("read", "audit"),
        automatic_retry=False,
        financial_qualified=False,
        historical_pit_verified=False,
        operator_owner_claim=owner,
        owner_authenticated=False,
        authority_scope="local_process_with_pinned_server_configuration",
        elapsed_seconds=monotonic() - started,
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
                    outcome="committed_readback_unconfirmed"
                    if isinstance(error, HistoryQualificationCliCommittedReadbackError)
                    else "unconfirmed",
                    committed=getattr(error, "committed", None),
                    error_code=code
                    if re.fullmatch(r"(?:history|history41)_[a-z0-9_]{1,120}", code)
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
