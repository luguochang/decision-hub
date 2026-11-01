"""Explicit fixed90 history originals and one bounded missing-page operation.

Never migrates, starts a collector loop, resets an attempt, or trains a model.
The supplied owner is a local filesystem claim, not an authenticated principal.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from apps.hub_worker.history_observed_cli import HistoryObservedCliCommand, _service
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_missing_acquisition_v2 import (
    HistoryMissingAcquisitionV2,
)
from packages.kernel.decision_hub_kernel.application.history_missing_resume_v2 import (
    HistoryMissingResumeV2,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2EpochRecord as Epoch,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2JobRecord as Job,
)
from packages.kernel.decision_hub_kernel.ports.history_dataset_v2 import history_v2_id
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc
from packages.provider_adapters.market.history_missing_transport_v2 import HistoryMissingTransportV2

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _clock(raw: str) -> datetime:
    try:
        return aware_utc(datetime.fromisoformat(raw))
    except ValueError as error:
        raise argparse.ArgumentTypeError("history_v2_explicit_UTC_clock_required") from error


def parse_command(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in (
        "prepare-plan",
        "record-plan",
        "register-epoch",
        "collect-one",
        "resume",
        "status",
        "freeze",
        "read",
    ):
        part = actions.add_parser(action)
        part.add_argument("--database-path", type=Path, required=True)
        part.add_argument("--data-directory", type=Path, required=True)
        part.add_argument("--owner-id", required=True)
        part.add_argument("--request-id", required=True)
        if action in {"prepare-plan", "record-plan"}:
            part.add_argument("--census-path", type=Path, required=True)
            part.add_argument("--census-hash", required=True)
        if action in {"register-epoch", "freeze", "resume"}:
            part.add_argument("--plan-id", required=True)
        if action == "register-epoch":
            part.add_argument("--budget", type=int, required=True)
            part.add_argument("--per-scope-budget", type=int, required=True)
            part.add_argument("--deadline-at", type=_clock, required=True)
        if action in {"collect-one", "status", "resume"}:
            part.add_argument("--epoch-id", required=True)
        if action == "resume":
            part.add_argument("--epoch-request-id", required=True)
            part.add_argument("--expected-epoch-hash", required=True)
        if action == "collect-one":
            part.add_argument("--job-id")
        if action == "freeze":
            part.add_argument("--epoch-id", action="append", default=[])
        if action == "read":
            part.add_argument("--original-id", required=True)
    value = parser.parse_args(argv)
    if (
        not value.owner_id.strip()
        or not value.request_id.strip()
        or len(value.owner_id) > 128
        or len(value.request_id) > 128
    ):
        parser.error("history_v2_explicit_identity_invalid")
    if value.action in {"prepare-plan", "record-plan"} and (
        not value.census_path.is_absolute() or not re.fullmatch(r"[a-f0-9]{64}", value.census_hash)
    ):
        parser.error("history_v2_census_absolute_path_and_SHA_required")
    return value


def _summary(value) -> dict:
    provenance = value.provenance
    return {
        "original_id": value.original_id,
        "kind": value.kind,
        "plan_id": value.plan_id,
        "payload_hash": value.payload_hash,
        "known_asof": value.known_asof.isoformat(),
        "produced_at": value.produced_at.isoformat(),
        "recorded_at": value.recorded_at.isoformat(),
        "source_files": value.source_files,
        "fixed_cutoff_count": len(value.cutoffs),
        "first_cutoff": value.cutoffs[0].isoformat(),
        "last_cutoff": value.cutoffs[-1].isoformat(),
        "missing_intervals": provenance.get("missing_intervals"),
        "window_counts": dict(Counter(item["status"] for item in provenance.get("windows", []))),
        "base_registration_ids": provenance.get("base_registration_ids"),
        "budget": provenance.get("budget"),
        "per_scope_budget": provenance.get("per_scope_budget"),
        "deadline_at": provenance.get("deadline_at"),
    }


def execute_command(command: argparse.Namespace) -> dict:
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
        raise ValueError("history_v2_cli_loaded_source_changed")
    composition = HistoryObservedCliCommand(
        "status",
        command.database_path,
        command.data_directory,
        command.owner_id,
        command.request_id,
    )
    receipt = {
        "schema_version": "history-dataset-v2-local-command-receipt.v1",
        "operation": command.action,
        "operator_owner_claim": command.owner_id,
        "request_id": command.request_id,
        "owner_authenticated": False,
        "authority_scope": "local_process_filesystem_access",
        "financial_qualified": False,
        "historical_pit_verified": False,
        "promotion_allowed": False,
        "ready_for_real_fit": False,
        "automatic_retry": False,
        "cli_source_sha256": LOADED_SOURCE_SHA256,
    }
    with _service(composition) as history:
        originals = HistoryDatasetV2Service(history, command.data_directory)
        if command.action in {"prepare-plan", "record-plan"}:
            with history._read_session() as session:
                prepared = originals.prepare_plan(
                    session,
                    census_path=command.census_path,
                    census_hash=command.census_hash,
                    owner_claim=command.owner_id,
                    request_id=command.request_id,
                )
            value = prepared.value
            if command.action == "record-plan":
                with history._write_session() as session:
                    value = originals.record_original(session, prepared)
            else:
                receipt.update({"committed": False, "read_only": True, "originals_verified": True})
                receipt.update(_summary(value))
                return receipt
        elif command.action in {"register-epoch", "collect-one", "resume"}:
            transport = HistoryMissingTransportV2(originals.raw)
            collector = HistoryMissingAcquisitionV2(history, originals, transport)
            if command.action == "register-epoch":
                value = collector.register_epoch(
                    owner_claim=command.owner_id,
                    request_id=command.request_id,
                    plan_id=command.plan_id,
                    budget=command.budget,
                    per_scope_budget=command.per_scope_budget,
                    deadline_at=command.deadline_at,
                )
            elif command.action == "resume":
                result = HistoryMissingResumeV2(collector).resume(
                    owner_claim=command.owner_id,
                    request_id=command.request_id,
                    plan_id=command.plan_id,
                    epoch_id=command.epoch_id,
                    expected_epoch_hash=command.expected_epoch_hash,
                    expected_epoch_request_id=command.epoch_request_id,
                )
                receipt.update(
                    {
                        "committed": True,
                        "read_only": False,
                        "result": result,
                        "http_attempt_sent": False,
                        "outcome": result["outcome"],
                    }
                )
                return receipt
            else:
                with history._read_session() as session:
                    epoch = originals.read_original(session, command.epoch_id, command.owner_id)
                    if epoch.kind != "epoch" or epoch.request_id != command.request_id:
                        raise ValueError("history_v2_epoch_request_identity_invalid")
                result = asyncio.run(
                    collector.collect_one(
                        epoch_id=command.epoch_id,
                        owner_claim=command.owner_id,
                        job_id=command.job_id,
                    )
                )
                receipt.update(
                    {
                        "committed": True,
                        "read_only": False,
                        "result": result,
                        "http_attempt_sent": result is not None,
                        "outcome": "audited_attempt" if result is not None else "no_attempt",
                        "one_http_attempt_maximum": 1,
                        "dataset_originals_verified": False,
                    }
                )
                return receipt
        elif command.action == "freeze":
            saved_id = history_v2_id("dataset", command.owner_id, command.request_id)
            with history._read_session() as session:
                try:
                    value = originals.read_original(session, saved_id, command.owner_id)
                except ValueError as error:
                    if str(error) != "history_v2_original_missing_or_owner_conflict":
                        raise
                    value = None
                if value is not None:
                    if value.plan_id != command.plan_id or tuple(
                        item[0] for item in value.provenance["epoch_originals"]
                    ) != tuple(command.epoch_id):
                        raise ValueError("history_v2_request_conflict")
                else:
                    prepared = originals.prepare_freeze(
                        session,
                        owner_claim=command.owner_id,
                        request_id=command.request_id,
                        plan_id=command.plan_id,
                        epoch_ids=tuple(command.epoch_id),
                    )
            if value is None:
                with history._write_session() as session:
                    value = originals.record_original(session, prepared)
        elif command.action == "status":
            with history._read_session() as session:
                value = originals.read_original(session, command.epoch_id, command.owner_id)
                if value.kind != "epoch" or value.request_id != command.request_id:
                    raise ValueError("history_v2_epoch_request_identity_invalid")
                epoch = session.get(Epoch, command.epoch_id)
                jobs = list(session.scalars(select(Job).where(Job.epoch_id == command.epoch_id)))
                if epoch is None or len(jobs) > 1200:
                    raise ValueError("history_v2_epoch_state_missing_or_unbounded")
                receipt.update(
                    {
                        "state": epoch.state,
                        "attempts": epoch.attempts,
                        "consecutive_transport_errors": epoch.consecutive_transport_errors,
                        "error_code": epoch.error_code,
                        "jobs": [
                            {
                                key: getattr(job, key)
                                for key in (
                                    "job_id",
                                    "scope_id",
                                    "cursor",
                                    "state",
                                    "attempts",
                                    "page_count",
                                    "max_pages",
                                    "generation",
                                    "error_code",
                                )
                            }
                            for job in jobs
                        ],
                    }
                )
        else:
            with history._read_session() as session:
                value = originals.read_original(session, command.original_id, command.owner_id)
                if value.request_id != command.request_id:
                    raise ValueError("history_v2_request_identity_invalid")
        # Independent connection readback after the known successful COMMIT.
        # An exception/unknown COMMIT escapes before this point and is not replayed.
        with history._read_session() as session:
            loaded = originals.read_original(session, value.original_id, command.owner_id)
        if loaded != value:
            raise ValueError("history_v2_committed_readback_unconfirmed")
        receipt.update(_summary(loaded))
        receipt.update(
            {
                "committed": True,
                "read_only": command.action in {"read", "status"},
                "originals_verified": True,
            }
        )
    return receipt


def main(argv: list[str] | None = None) -> int:
    command = parse_command(argv)
    try:
        receipt = execute_command(command)
    except Exception as error:
        raw = str(error)
        code = raw if re.fullmatch(r"history_[a-z0-9_]{1,110}", raw) else type(error).__name__
        print(
            json.dumps(
                {
                    "operation": command.action,
                    "outcome": "unconfirmed",
                    "error_code": code,
                    "committed": None,
                    "automatic_retry": False,
                    "financial_qualified": False,
                }
            )
        )
        return 1
    print(json.dumps(receipt, sort_keys=True, default=lambda value: value.isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
