"""One durable prospective slot per asset/hour on the existing risk lane.

This module provisions no identity, model, dataset or market request. A private
server bootstrap supplies the owner and model grants. Cancellation is joined
before returning, so an abandoned thread cannot overlap the next lane tick.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import monotonic

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.hub_api.history_product_bootstrap import configured_history_product
from apps.hub_api.history_product_routes import HistoryProductBootstrap
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    forecast_id,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import (
    HistoryObservedArchive,
    encoded,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    LOADED_SOURCE_SHA256 as RECORDS_SHA,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord as Slot,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE_FILES = (
    "apps/hub_worker/history_hourly_v2.py",
    "packages/kernel/decision_hub_kernel/persistence/history_hourly_v2_records.py",
    "apps/hub_api/history_product_bootstrap.py",
    "apps/hub_api/history_product_routes.py",
    "apps/hub_api/history_owner_authority.py",
)
SCOPES = tuple(spot_scope(asset + "-USDT").scope_id for asset in ("BTC", "ETH"))
TERMINAL = frozenset(
    {"blocked", "committed_verified", "committed_readback_unconfirmed", "unconfirmed"}
)


@dataclass(frozen=True)
class HistoryHourlyResultV2:
    slot_id: str
    scope_id: str
    status: str
    reason_code: str | None
    forecast_id: str | None
    automatic_retry: bool = False


class HistoryHourlyDeadline(ValueError):
    pass


class HistoryHourlyCommitUnconfirmed(ValueError):
    """No production follows an uncertain slot reservation/receipt commit."""

    automatic_retry = False


class HistoryHourlyV2:
    def __init__(self, bootstrap: HistoryProductBootstrap):
        if type(bootstrap) is not HistoryProductBootstrap:
            raise ValueError("history_hourly_actual_private_bootstrap_required")
        self.bootstrap = bootstrap
        self.originals = bootstrap.originals
        self.commands = self.originals.commands
        self.owner = bootstrap.authority.owner_identity
        self.forecasts = bootstrap.forecasts
        self.grants = () if self.forecasts is None else tuple(sorted(self.forecasts.grants.items()))
        self.identity = (self.owner, self.grants, bootstrap.allow_producer)
        self.archive = HistoryObservedArchive(self.originals.data_directory / "history-hourly-v2")
        self._busy = False
        self.last_fence_reason: str | None = None

    def _fence_path(self, scope):
        return self.archive.root / "fences" / (canonical_hash([self.owner, scope]) + ".json")

    def _fence(self, receipt):
        """Crash-safe fail-stop before COMMIT; no secret or market payload is stored."""
        path = self._fence_path(receipt["scope_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        body = encoded(
            {
                "slot_id": receipt["slot_id"],
                "owner_claim": self.owner,
                "scope_id": receipt["scope_id"],
                "request_id": receipt["request_id"],
                "cutoff_at": receipt["cutoff_at"],
                "forecast_id": receipt["forecast_id"],
                "reservation_hash": canonical_hash(receipt),
            }
        )
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self.last_fence_reason = "history_hourly_unconfirmed_fence_requires_reconciliation"
            return False
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def _release_fence(self, row):
        path = self._fence_path(row.scope_id)
        if not path.is_file():
            raise ValueError("history_hourly_reservation_fence_missing")
        body = path.read_bytes()
        if len(body) > 4096:
            raise ValueError("history_hourly_reservation_fence_oversize")
        proof = json.loads(body)
        if (proof["slot_id"], proof["owner_claim"], proof["scope_id"]) != (
            row.slot_id,
            self.owner,
            row.scope_id,
        ):
            raise ValueError("history_hourly_reservation_fence_changed")
        # This exact task-owned file is removed only after a new connection
        # has confirmed blocked-before-write or committed-and-full-read state.
        path.unlink()

    def _sources(self):
        if (
            self.bootstrap.originals is not self.originals
            or self.bootstrap.forecasts is not self.forecasts
            or self.originals.commands is not self.commands
            or (
                self.bootstrap.authority.owner_identity,
                () if self.forecasts is None else tuple(sorted(self.forecasts.grants.items())),
                self.bootstrap.allow_producer,
            )
            != self.identity
        ):
            raise ValueError("history_hourly_private_binding_changed")
        base = (
            self.originals.require_sources()
            if self.forecasts is None
            else self.forecasts._sources()
        )
        own = tuple(
            (name, hashlib.sha256((self.originals.source_root / name).read_bytes()).hexdigest())
            for name in SOURCE_FILES
        )
        if own[0][1] != LOADED_SOURCE_SHA256 or own[1][1] != RECORDS_SHA:
            raise ValueError("history_hourly_loaded_source_changed")
        return tuple(dict((*base, *own)).items())

    @staticmethod
    def _check(cancel, deadline):
        if cancel.is_set():
            raise HistoryHourlyDeadline("history_hourly_cancelled_before_write")
        if monotonic() >= deadline:
            raise HistoryHourlyDeadline("history_hourly_deadline_reached")

    def _read_slot(self, session, identifier):
        row = session.get(Slot, identifier, populate_existing=True)
        if row is None:
            return None
        receipt = json.loads(row.receipt_json)
        if (
            canonical_hash(receipt) != row.receipt_hash
            or (
                receipt["slot_id"],
                receipt["owner_claim"],
                receipt["scope_id"],
                receipt["hour_at"],
                receipt["cutoff_at"],
                receipt["status"],
                receipt["forecast_id"],
                receipt["reason_code"],
                receipt["request_id"],
                receipt["model_id"],
                receipt["started_at"],
                receipt["deadline_at"],
                receipt["completed_at"],
                receipt["source_original_ref"],
                receipt["source_original_hash"],
                receipt["fixture_mode"],
            )
            != (
                row.slot_id,
                row.owner_claim,
                row.scope_id,
                as_utc(row.hour_at).isoformat(),
                as_utc(row.cutoff_at).isoformat(),
                row.status,
                row.forecast_id,
                row.reason_code,
                row.request_id,
                row.model_id,
                as_utc(row.started_at).isoformat(),
                as_utc(row.deadline_at).isoformat(),
                None if row.completed_at is None else as_utc(row.completed_at).isoformat(),
                row.source_original_ref,
                row.source_original_hash,
                row.fixture_mode,
            )
            or row.owner_claim != self.owner
            or row.fixture_mode is not self.originals.fixture_mode
        ):
            raise ValueError("history_hourly_saved_receipt_changed")
        source = json.loads(self.archive.read(row.source_original_ref, row.source_original_hash))
        if tuple((item["path"], item["sha256"]) for item in source["files"]) != tuple(
            tuple(item) for item in receipt["sources"]
        ) or any(
            hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
            != item["sha256"]
            for item in source["files"]
        ):
            raise ValueError("history_hourly_saved_source_original_changed")
        return row, receipt

    @staticmethod
    def _result(row):
        return HistoryHourlyResultV2(
            row.slot_id, row.scope_id, row.status, row.reason_code, row.forecast_id
        )

    def _finish(self, identifier, status, reason, forecast=None):
        if status not in TERMINAL:
            raise ValueError("history_hourly_terminal_status_required")
        self._sources()
        try:
            with self.commands._write_session() as session:
                pair = self._read_slot(session, identifier)
                if pair is None:
                    raise ValueError("history_hourly_reservation_missing")
                row, receipt = pair
                if row.status != "reserved":
                    return self._result(row)
                now = self.originals.now()
                receipt = {
                    **receipt,
                    "status": status,
                    "reason_code": reason,
                    "forecast_id": forecast,
                    "completed_at": now.isoformat(),
                }
                row.status, row.reason_code, row.forecast_id = status, reason, forecast
                row.completed_at = now
                row.receipt_json = encoded(receipt).decode()
                row.receipt_hash = canonical_hash(receipt)
                self._sources()
                session.flush()
        except Exception as error:
            raise HistoryHourlyCommitUnconfirmed(
                "history_hourly_receipt_commit_unconfirmed"
            ) from error
        with self.commands._read_session() as session:
            row, _ = self._read_slot(session, identifier)
            result = self._result(row)
        if result.status in {"blocked", "committed_verified"}:
            self._release_fence(row)
        return result

    def process_one(self, *, cancel=None, budget_seconds=90.0):
        """Worker-thread entry; frozen T and all preparation/writes stay on this thread."""
        if type(budget_seconds) not in (int, float) or not 1 <= budget_seconds <= 90:
            raise ValueError("history_hourly_budget_1_to_90_seconds_required")
        cancel = cancel or threading.Event()
        deadline = monotonic() + budget_seconds
        self._check(cancel, deadline)
        sources = self._sources()
        now = self.originals.now()
        cutoff = now.replace(second=0, microsecond=0)
        hour = cutoff.replace(minute=0)
        source = {
            "schema_version": "history-hourly-source.v2",
            "files": [
                dict(
                    path=name,
                    sha256=digest,
                    bytes_base64=base64.b64encode(
                        (self.originals.source_root / name).read_bytes()
                    ).decode(),
                )
                for name, digest in sources
            ],
        }
        source_ref, source_hash = self.archive.reference(source)
        chosen = None
        # There are only two candidate keys; no all-history job or raw scan.
        with self.commands._read_session() as session:
            for scope in SCOPES:
                if self._fence_path(scope).exists():
                    self.last_fence_reason = (
                        "history_hourly_unconfirmed_fence_requires_reconciliation"
                    )
                    continue
                identifier = "hhour2_" + canonical_hash([self.owner, scope, hour.isoformat()])[:48]
                if self._read_slot(session, identifier) is None:
                    chosen = (scope, identifier)
                    break
        if chosen is None:
            return None
        scope, identifier = chosen
        model = dict(self.grants).get(scope)
        request = "auto_" + canonical_hash([model, scope, cutoff.isoformat()])[:48]
        expected_forecast = None if model is None else forecast_id(self.owner, request)
        receipt = dict(
            schema_version="history-hourly-receipt.v2",
            slot_id=identifier,
            owner_claim=self.owner,
            scope_id=scope,
            hour_at=hour.isoformat(),
            cutoff_at=cutoff.isoformat(),
            started_at=now.isoformat(),
            deadline_at=(now + timedelta(seconds=budget_seconds)).isoformat(),
            model_id=model,
            request_id=request,
            forecast_id=expected_forecast,
            status="reserved",
            reason_code=None,
            completed_at=None,
            automatic_retry=False,
            fixture_mode=self.originals.fixture_mode,
            sources=sources,
            source_original_ref=source_ref,
            source_original_hash=source_hash,
            financial_qualified=False,
            historical_pit_verified=False,
        )
        self._check(cancel, deadline)
        if not self._fence(receipt):
            return None
        try:
            with self.commands._write_session() as session:
                if self._read_slot(session, identifier) is not None:
                    return None
                if (
                    self.archive.put(source) != (source_ref, source_hash)
                    or self._sources() != sources
                ):
                    raise ValueError("history_hourly_source_original_changed")
                session.add(
                    Slot(
                        slot_id=identifier,
                        owner_claim=self.owner,
                        scope_id=scope,
                        hour_at=hour,
                        cutoff_at=cutoff,
                        started_at=now,
                        deadline_at=now + timedelta(seconds=budget_seconds),
                        model_id=model,
                        request_id=request,
                        forecast_id=expected_forecast,
                        status="reserved",
                        reason_code=None,
                        completed_at=None,
                        receipt_json=encoded(receipt).decode(),
                        receipt_hash=canonical_hash(receipt),
                        source_original_ref=source_ref,
                        source_original_hash=source_hash,
                        fixture_mode=self.originals.fixture_mode,
                    )
                )
                session.flush()
                self._check(cancel, deadline)
        except IntegrityError:
            # A concurrent owner/hour reservation is occupied; never replay.
            return None
        except Exception as error:
            raise HistoryHourlyCommitUnconfirmed(
                "history_hourly_reservation_commit_unconfirmed"
            ) from error
        # New connection confirms our durable reservation before any production.
        with self.commands._read_session() as session:
            row, actual = self._read_slot(session, identifier)
            if actual != json.loads(encoded(receipt)):
                raise ValueError("history_hourly_reservation_readback_changed")
            prior = session.scalar(
                select(Slot.slot_id)
                .where(
                    Slot.owner_claim == self.owner,
                    Slot.scope_id == scope,
                    Slot.slot_id != identifier,
                    Slot.status.in_(("reserved", "unconfirmed", "committed_readback_unconfirmed")),
                )
                .limit(1)
            )
        if prior is not None:
            return self._finish(identifier, "blocked", "history_hourly_prior_commit_unconfirmed")
        if model is None:
            return self._finish(
                identifier, "blocked", "history_hourly_trusted_model_not_configured"
            )
        if not self.bootstrap.allow_producer:
            return self._finish(identifier, "blocked", "history_hourly_production_not_granted")
        try:
            self._check(cancel, deadline)
            first = None
            with self.commands._read_session() as session:
                if session.get(Forecast, expected_forecast) is not None:
                    first = self.forecasts.read(session, expected_forecast)
                    if (first.scope_id, first.model_id, first.cutoff_at) != (scope, model, cutoff):
                        raise ValueError("history_hourly_forecast_firstsave_conflict")
                    self._check(cancel, deadline)
                else:
                    prepared = self.forecasts.prepare(
                        session, scope_id=scope, request_id=request, cutoff_at=cutoff
                    )
            self._check(cancel, deadline)
        except Exception as error:
            # These are preparation/read gates; no forecast write was attempted.
            reason = (
                str(error)
                if isinstance(error, ValueError)
                else "history_hourly_prepare_unavailable"
            )
            return self._finish(identifier, "blocked", reason[:128])
        if first is not None:
            return self._finish(identifier, "committed_verified", None, first.forecast_id)
        try:
            with self.commands._write_session() as session:
                value = self.forecasts.record(session, prepared)
                # Expiration/cancellation before COMMIT rolls back the forecast.
                self._check(cancel, deadline)
        except HistoryHourlyDeadline as error:
            return self._finish(identifier, "blocked", str(error), expected_forecast)
        except Exception:
            # COMMIT may have reached SQLite. No second write or new-hour replay.
            return self._finish(
                identifier,
                "unconfirmed",
                "history_hourly_forecast_commit_unconfirmed",
                expected_forecast,
            )
        try:
            with self.commands._read_session() as session:
                first = self.forecasts.read(session, value.forecast_id)
            if first != value:
                raise ValueError("history_hourly_forecast_readback_changed")
        except Exception:
            return self._finish(
                identifier,
                "committed_readback_unconfirmed",
                "history_hourly_forecast_readback_unconfirmed",
                value.forecast_id,
            )
        return self._finish(identifier, "committed_verified", None, first.forecast_id)

    async def tick(self, budget_seconds=90.0):
        if self._busy:
            return 0
        self._busy = True
        cancel = threading.Event()
        task = asyncio.create_task(
            asyncio.to_thread(self.process_one, cancel=cancel, budget_seconds=budget_seconds)
        )
        try:
            result = await asyncio.shield(task)
            return int(result is not None)
        except asyncio.CancelledError:
            cancel.set()
            # asyncio cancellation does not kill a thread. Join before the lane
            # can start another tick or the process exits its managed scheduler.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancel.set()
                except Exception:
                    # Preserve the scheduler's cancellation even if the
                    # joined worker fails while saving its final audit.
                    break
            if not task.cancelled():
                task.exception()
            raise
        finally:
            self._busy = False


def install_history_hourly_on_risk_lane(realtime, database, *, bootstrap=None):
    """Call once after build_realtime_worker; absent private config stays closed."""
    bootstrap = configured_history_product(database) if bootstrap is None else bootstrap
    if bootstrap is None:
        return None
    scheduler = realtime.scheduler
    if getattr(scheduler, "_history_hourly_v2", None) is not None:
        raise ValueError("history_hourly_risk_lane_already_bound")
    hourly = HistoryHourlyV2(bootstrap)
    original = scheduler.risk_enhancement_tick

    async def combined():
        started = monotonic()
        original_count = 0 if original is None else await original()
        remaining = min(90.0, 115.0 - (monotonic() - started))
        if remaining < 1:
            return original_count
        return original_count + await hourly.tick(remaining)

    scheduler.risk_enhancement_tick = combined
    scheduler._history_hourly_v2 = hourly
    return hourly
