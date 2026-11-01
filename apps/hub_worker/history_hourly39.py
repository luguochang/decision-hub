"""Existing durable hourly slots/fences using the fresh RO/short writer executor."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from datetime import timedelta
from pathlib import Path
from time import monotonic

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.hub_api.history_product_bootstrap39 import configured_history_product39
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastCommittedReadbackError,
    forecast_id,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
    HistoryForecastDispatch39Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_write39 import (
    HistoryForecastCommitUnconfirmed39,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord as Slot,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

from .history_hourly_v2 import (
    SCOPES,
    HistoryHourlyCommitUnconfirmed,
    HistoryHourlyDeadline,
    HistoryHourlyV2,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryHourly39(HistoryHourlyV2):
    def __init__(self, bootstrap):
        super().__init__(bootstrap)
        if (
            self.forecasts is not None
            and type(self.forecasts) is not HistoryForecastDispatch39Service
        ):
            raise ValueError("history39_hourly_actual_dispatch_required")

    def _sources(self):
        sources = super()._sources()
        own = tuple(
            (name, hashlib.sha256((self.originals.source_root / name).read_bytes()).hexdigest())
            for name in (
                "apps/hub_api/history_product_bootstrap39.py",
                "apps/hub_worker/history_hourly39.py",
            )
        )
        if own[1][1] != LOADED_SOURCE_SHA256:
            raise ValueError("history39_hourly_loaded_source_changed")
        if any(
            getattr(HistoryHourly39, name) is not function or function.__code__ is not code
            for name, function, code in LOADED_METHODS
        ):
            raise ValueError("history39_hourly_loaded_callable_changed")
        return tuple(dict((*sources, *own)).items())

    def process_one(self, *, cancel=None, budget_seconds=90.0):
        if type(budget_seconds) not in (int, float) or not 1 <= budget_seconds <= 90:
            raise ValueError("history_hourly_budget_1_to_90_seconds_required")
        cancel = cancel or threading.Event()
        started_clock, started = self.originals.now(), monotonic()
        deadline = started + budget_seconds

        def check():
            self._check(cancel, deadline)

        check()
        sources = self._sources()
        initial_cutoff = started_clock.replace(second=0, microsecond=0)
        hour = initial_cutoff.replace(minute=0)
        chosen = None
        # Reuse the same two keys and existing fence files, including old unknowns.
        with self.commands._read_session() as session:
            for scope in SCOPES:
                if self._fence_path(scope).exists():
                    self.last_fence_reason = (
                        "history_hourly_unconfirmed_fence_requires_reconciliation"
                    )
                    continue
                if self.forecasts is not None and self.forecasts.writer._fence_path(scope).exists():
                    self.last_fence_reason = "history39_scope_fence_requires_reconciliation"
                    continue
                identifier = "hhour2_" + canonical_hash([self.owner, scope, hour.isoformat()])[:48]
                if self._read_slot(session, identifier) is None:
                    chosen = scope, identifier
                    break
        if chosen is None:
            return None
        scope, identifier = chosen
        model = dict(self.grants).get(scope)
        proof, prepare_error, deep_seconds = None, None, 0.0
        if model is None:
            prepare_error = "history_hourly_trusted_model_not_configured"
        elif not self.bootstrap.allow_producer:
            prepare_error = "history_hourly_production_not_granted"
        else:
            try:
                check()
                deep_started = monotonic()
                proof = self.forecasts.authenticate_model(scope)
                deep_seconds = monotonic() - deep_started
                check()
            except Exception as error:
                prepare_error = (
                    str(error)[:128]
                    if isinstance(error, ValueError)
                    else "history_hourly_prepare_unavailable"
                )
        cutoff = self.originals.now().replace(second=0, microsecond=0)
        if cutoff.replace(minute=0) != hour:
            cutoff = initial_cutoff
            prepare_error = "history39_hour_changed_during_authentication"
        request = "auto_" + canonical_hash([model, scope, cutoff.isoformat()])[:48]
        expected = None if model is None else forecast_id(self.owner, request)
        source = {
            "schema_version": "history-hourly-source.v2",
            "files": [
                {
                    "path": name,
                    "sha256": digest,
                    "bytes_base64": base64.b64encode(
                        (self.originals.source_root / name).read_bytes()
                    ).decode(),
                }
                for name, digest in sources
            ],
        }
        source_ref, source_hash = self.archive.put(source)
        receipt = dict(
            schema_version="history-hourly-receipt.v2",
            slot_id=identifier,
            owner_claim=self.owner,
            scope_id=scope,
            hour_at=hour.isoformat(),
            cutoff_at=cutoff.isoformat(),
            started_at=started_clock.isoformat(),
            deadline_at=(started_clock + timedelta(seconds=budget_seconds)).isoformat(),
            model_id=model,
            request_id=request,
            forecast_id=expected,
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
            executor="history_forecast_write39",
            deep_verify_seconds=deep_seconds,
        )
        # The original budget is never extended. Even an expired preparation is
        # consumed once with a real blocked receipt; no forecast follows it.
        if not self._fence(receipt):
            return None
        try:
            with self.commands._write_session() as session:
                if self._read_slot(session, identifier) is not None:
                    return None
                if self._sources() != sources or self.archive.read(
                    source_ref, source_hash
                ) != encoded(source):
                    raise ValueError("history_hourly_source_original_changed")
                session.add(
                    Slot(
                        slot_id=identifier,
                        owner_claim=self.owner,
                        scope_id=scope,
                        hour_at=hour,
                        cutoff_at=cutoff,
                        started_at=started_clock,
                        deadline_at=started_clock + timedelta(seconds=budget_seconds),
                        model_id=model,
                        request_id=request,
                        forecast_id=expected,
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
        except IntegrityError:
            return None
        except Exception as error:
            raise HistoryHourlyCommitUnconfirmed(
                "history_hourly_reservation_commit_unconfirmed"
            ) from error
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
        if prepare_error is not None:
            return self._finish(identifier, "blocked", prepare_error, expected)
        try:
            check()
            feature_started = monotonic()
            first = None
            with self.commands._read_session() as session:
                if session.get(Forecast, expected) is not None:
                    first = self.forecasts.read(session, expected)
                    if (first.scope_id, first.model_id, first.cutoff_at) != (scope, model, cutoff):
                        raise ValueError("history_hourly_forecast_firstsave_conflict")
                else:
                    prepared = self.forecasts.prepare(
                        session, scope_id=scope, request_id=request, cutoff_at=cutoff, proof=proof
                    )
            check()
        except Exception as error:
            reason = (
                str(error)[:128]
                if isinstance(error, ValueError)
                else "history_hourly_prepare_unavailable"
            )
            return self._finish(identifier, "blocked", reason, expected)
        if first is not None:
            return self._finish(identifier, "committed_verified", None, first.forecast_id)
        timing = {
            "deep_verify": deep_seconds,
            "feature_transfer": monotonic() - feature_started,
            "deep_verified_at": proof.deep_verified_at,
            "model_authenticated_at": prepared.value.model_authenticated_at.isoformat(),
            "produced_at": prepared.value.produced_at.isoformat(),
        }
        try:
            value = self.forecasts.save_prepared(prepared, check=check, timing=timing)
        except HistoryForecastCommitUnconfirmed39:
            return self._finish(
                identifier, "unconfirmed", "history_hourly_forecast_commit_unconfirmed", expected
            )
        except HistoryDatasetForecastCommittedReadbackError:
            return self._finish(
                identifier,
                "committed_readback_unconfirmed",
                "history_hourly_forecast_readback_unconfirmed",
                expected,
            )
        except HistoryHourlyDeadline as error:
            return self._finish(identifier, "blocked", str(error), expected)
        except Exception as error:
            reason = (
                str(error)[:128]
                if isinstance(error, ValueError)
                else "history39_writer_rolled_back"
            )
            return self._finish(identifier, "blocked", reason, expected)
        result = self._finish(identifier, "committed_verified", None, value.forecast_id)
        self.last_phase_timing = {**self.forecasts.last_phase_timing, "ACK": monotonic() - started}
        self.forecasts.writer._publish_phases39(self.last_phase_timing, value, "hourly_ACK")
        return result


def install_history_hourly39_on_risk_lane(realtime, database, *, bootstrap=None):
    bootstrap = configured_history_product39(database) if bootstrap is None else bootstrap
    if bootstrap is None:
        return None
    scheduler = realtime.scheduler
    if getattr(scheduler, "_history_hourly_v2", None) is not None:
        raise ValueError("history_hourly_risk_lane_already_bound")
    hourly, original = HistoryHourly39(bootstrap), scheduler.risk_enhancement_tick

    async def combined():
        started = monotonic()
        count = 0 if original is None else await original()
        remaining = min(90.0, 115.0 - (monotonic() - started))
        return count if remaining < 1 else count + await hourly.tick(remaining)

    scheduler.risk_enhancement_tick = combined
    scheduler._history_hourly_v2 = hourly
    return hourly


LOADED_METHODS = tuple(
    (name, function, function.__code__)
    for name, function in HistoryHourly39.__dict__.items()
    if callable(function) and hasattr(function, "__code__")
)
