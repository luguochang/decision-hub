"""Authenticated independent fit/save and future-only empirical consumer.

No network, implicit database, legacy trial writer, or financial promotion.
Callers own physical transactions; fit values are calculated in an RO snapshot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter

from ..persistence.db import RawCaptureRecord, as_utc
from ..persistence.history_observed_records import (
    HistoryObservedAcquisitionJobRecord as Job,
)
from ..persistence.history_observed_records import HistoryObservedTrainingRecord as Record
from ..ports.history_observed_model import (
    FrozenHistoryObservedPathModelV2,
    HistoryObservedFitValuesV1,
    HistoryObservedProspectiveForecastV1,
    HistoryObservedSourceAdmissionV1,
)
from ..ports.history_observed_training import (
    HistoryObservedDatasetManifestV1,
    HistoryObservedTrainingProtocolV1,
    HistoryObservedTrainingWindowV1,
    label_complete,
    readiness,
    seal,
    verify_payload,
)
from ..ports.market_path import MarketCandle, MarketPathScope, aware_utc, canonical_hash
from ..ports.path_forecast import EmpiricalDistribution
from .history_observed_dataset import (
    HistoryObservedPreparation,
    capture_payload,
    encoded,
    require_snapshot,
    snapshot_identity,
)
from .history_observed_ledger import HistoryObservedLedger

MODEL_SOURCES = (
    "packages/kernel/decision_hub_kernel/ports/history_observed_model.py",
    "packages/kernel/decision_hub_kernel/application/history_observed_model.py",
)
TERMINAL = {"completed", "partial", "failed", "expired"}


def job_payload(job: Job) -> dict[str, object]:
    result: dict[str, object] = {}
    for column in job.__table__.columns:
        value = getattr(job, column.name)
        if isinstance(value, datetime):
            value = as_utc(value).isoformat()
        if column.name == "checkpoint_json":
            value = hashlib.sha256(job.checkpoint_json.encode()).hexdigest()
        result[column.name] = value
    return result


@dataclass(frozen=True)
class HistoryObservedFitPreparation:
    dataset: HistoryObservedPreparation
    fit: HistoryObservedFitValuesV1
    issuer: object
    thread_id: int
    _auth: bytes = field(repr=False)

    def authenticated_by(self, expected: bytes) -> bool:
        return hmac.compare_digest(self._auth, expected)


class HistoryObservedModelService:
    def __init__(self, ledger: HistoryObservedLedger) -> None:
        self.ledger = ledger
        self._key = secrets.token_bytes(32)

    def now(self) -> datetime:
        return aware_utc(self.ledger.clock())

    def _auth(self, dataset_hash: str, fit_hash: str, thread: int) -> bytes:
        return hmac.digest(self._key, encoded([dataset_hash, fit_hash, thread, id(self)]), "sha256")

    def _sources(self, protocol: HistoryObservedTrainingProtocolV1) -> None:
        if not set(MODEL_SOURCES).issubset(dict(protocol.implementation_files)):
            raise ValueError("history_observed_model_producer_not_registered")
        self.ledger.require_sources(protocol)

    def _jobs(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
    ) -> list[Job]:
        jobs = list(
            session.scalars(
                select(Job)
                .where(Job.registration_id == protocol.registration_id)
                .execution_options(populate_existing=True)
            )
        )
        expected = {
            (scope.scope_id, day) for scope in protocol.scopes for day in protocol.daily_cutoffs
        }
        if len(jobs) != 180 or {(job.scope_id, as_utc(job.cutoff_at)) for job in jobs} != expected:
            raise ValueError("history_observed_admission_exact_job_set_required")
        for job in jobs:
            if (
                job.protocol_hash != protocol.payload_hash
                or as_utc(job.recorded_at) != protocol.registered_at
                or as_utc(job.deadline_at) != protocol.registered_at + timedelta(hours=6)
                or not 0 <= job.attempts <= protocol.per_job_attempt_cap
                or job.lease_generation < 0
            ):
                raise ValueError("history_observed_admission_job_binding_invalid")
        if sum(job.attempts for job in jobs) > protocol.total_attempt_cap or any(
            sum(job.attempts for job in jobs if job.scope_id == scope.scope_id)
            > protocol.per_scope_attempt_cap
            for scope in protocol.scopes
        ):
            raise ValueError("history_observed_admission_attempt_budget_invalid")
        return jobs

    def _readset(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
    ) -> dict[str, Any]:
        """Actual checkpoint/capture witness, never a caller's reported-passed flag."""
        boundary = snapshot_identity(session)
        self._sources(protocol)
        cutoff = dataset.data_information_asof
        jobs = self._jobs(session, protocol, dataset)
        own = sorted(
            (job for job in jobs if job.scope_id == dataset.scope.scope_id),
            key=lambda job: job.cutoff_at,
        )
        pages: dict[tuple[str, str], dict[str, Any]] = {}
        for job in own:
            if (
                job.state not in TERMINAL
                or job.lease_owner is not None
                or job.lease_expires_at is not None
                or job.completed_at is None
                or not protocol.registered_at <= as_utc(job.completed_at) <= cutoff
                or (
                    job.last_attempt_at is not None
                    and not protocol.registered_at <= as_utc(job.last_attempt_at) <= cutoff
                )
            ):
                raise ValueError("history_observed_admission_collection_not_closed_at_K")
            checkpoint = json.loads(job.checkpoint_json)
            if (
                not isinstance(checkpoint, dict)
                or set(checkpoint)
                != {
                    "schema_version",
                    "phase",
                    "cursor",
                    "retry_used",
                    "inflight",
                    "pages",
                    "page_count",
                    "attempt_count",
                    "payload_hash",
                }
                or checkpoint["schema_version"] != "history-observed-acquisition-checkpoint.v1"
                or canonical_hash({k: v for k, v in checkpoint.items() if k != "payload_hash"})
                != checkpoint["payload_hash"]
                or checkpoint["inflight"] is not False
                or checkpoint["retry_used"] not in (0, 1)
                or not isinstance(checkpoint["pages"], list)
                or checkpoint["page_count"] != len(checkpoint["pages"])
                or checkpoint["attempt_count"] != job.attempts
                or not len(checkpoint["pages"]) <= job.attempts <= 2 * len(checkpoint["pages"]) + 2
                or len(checkpoint["pages"]) > protocol.daily_page_cap
            ):
                raise ValueError("history_observed_admission_checkpoint_invalid")
            previous = None
            for page in checkpoint["pages"]:
                if not isinstance(page, dict) or set(page) != {
                    "phase",
                    "cursor",
                    "next_cursor",
                    "capture_ref",
                    "capture_hash",
                    "received_at",
                    "rows",
                }:
                    raise ValueError("history_observed_admission_page_invalid")
                phase = page["phase"]
                day = as_utc(job.cutoff_at)
                start = (
                    day + timedelta(minutes=1) if phase == "label" else day - timedelta(minutes=31)
                )
                end = day + timedelta(days=1, minutes=1) if phase == "label" else day
                first = str(int(end.timestamp() * 1000))
                expected_cursor = (
                    previous["next_cursor"]
                    if previous is not None and previous["phase"] == phase
                    else first
                )
                received = aware_utc(datetime.fromisoformat(page["received_at"]))
                if (
                    phase not in {"label", "feature"}
                    or page["cursor"] != expected_cursor
                    or not protocol.registered_at <= received <= cutoff
                    or not isinstance(page["rows"], list)
                    or not 1 <= len(page["rows"]) <= 300
                    or any(
                        not isinstance(row, list)
                        or len(row) != 2
                        or not isinstance(row[0], int)
                        or row[0] % 60_000
                        or not int(start.timestamp() * 1000) <= row[0] < int(end.timestamp() * 1000)
                        or not isinstance(row[1], str)
                        or len(row[1]) != 64
                        for row in page["rows"]
                    )
                    or (
                        previous is not None
                        and received
                        < aware_utc(datetime.fromisoformat(previous["received_at"]))
                        + timedelta(seconds=1)
                    )
                ):
                    raise ValueError("history_observed_admission_page_cursor_or_clock_invalid")
                identity = (page["capture_ref"], page["capture_hash"])
                if identity in pages:
                    raise ValueError("history_observed_admission_duplicate_page_original")
                captures = list(
                    session.scalars(
                        select(RawCaptureRecord)
                        .where(
                            RawCaptureRecord.body_ref == identity[0],
                            RawCaptureRecord.body_hash == identity[1],
                        )
                        .execution_options(populate_existing=True)
                    )
                )
                captures = [
                    capture
                    for capture in captures
                    if capture.context_id == job.job_id
                    and capture.adapter_version == OKXMarketPathAdapter.adapter_version
                    and capture.status_code == 200
                    and capture.error_code is None
                    and as_utc(capture.completed_at) == received
                    and protocol.registered_at <= as_utc(capture.started_at) <= received
                    and received <= as_utc(capture.recorded_at) <= cutoff
                ]
                if len(captures) != 1:
                    raise ValueError("history_observed_admission_capture_producer_or_clock_invalid")
                body = self.ledger.builder.originals.raw_reader(identity[0])
                capture = captures[0]
                if (
                    hashlib.sha256(body).hexdigest() != identity[1]
                    or len(body) != capture.byte_count
                ):
                    raise ValueError("history_observed_admission_page_original_changed")
                pages[identity] = {
                    "job_id": job.job_id,
                    "page": page,
                    "capture": capture_payload(capture),
                }
                previous = page
            if job.state == "completed" and (
                checkpoint["phase"] != "done" or len(checkpoint["pages"]) < 2
            ):
                raise ValueError("history_observed_admission_false_completed_job")
        used: dict[tuple[str, str], dict[str, Any]] = {}
        joint = [
            window
            for window in dataset.windows
            if label_complete(window) and window.feature.realized_vol_30m is not None
        ]
        for window in joint:
            source = json.loads(
                self.ledger.builder.archive.read(
                    window.source_manifest_ref, window.source_manifest_hash
                )
            )
            latest = window.feature.latest_close_at
            assert latest is not None
            required = {
                int((latest - timedelta(minutes=index)).timestamp() * 1000)
                for index in range(1, 32)
            }
            required |= {
                int((window.market_cutoff_at + timedelta(minutes=index)).timestamp() * 1000)
                for index in range(1, 1441)
            }
            found = set()
            for value in source["bars"]:
                bar = MarketCandle.model_validate(value)
                stamp = int(bar.open_at.timestamp() * 1000)
                if stamp not in required:
                    continue
                if bar.capture_ref is None or bar.capture_hash is None:
                    raise ValueError("history_observed_admission_selected_original_missing")
                identity = (bar.capture_ref, bar.capture_hash)
                if (
                    identity not in pages
                    or [stamp, canonical_hash(bar.semantic_payload())]
                    not in pages[identity]["page"]["rows"]
                ):
                    raise ValueError("history_observed_admission_selected_row_not_acquired")
                found.add(stamp)
                used[identity] = pages[identity]
            if found != required:
                raise ValueError("history_observed_admission_selected_minutes_incomplete")
        if not used:
            raise ValueError("history_observed_admission_no_effective_originals")
        for ref, digest in pages:
            if hashlib.sha256(self.ledger.builder.originals.raw_reader(ref)).hexdigest() != digest:
                raise ValueError("history_observed_admission_original_changed_at_exit")
        self._sources(protocol)
        require_snapshot(session, boundary)
        return {
            "schema_version": "history-observed-admission-readset.v1",
            "registration_id": protocol.registration_id,
            "protocol_hash": protocol.payload_hash,
            "dataset_hash": dataset.payload_hash,
            "scope": dataset.scope.model_dump(mode="json"),
            "data_information_asof": cutoff.isoformat(),
            "jobs": [job_payload(job) for job in own],
            "captures": [used[identity]["capture"] for identity in sorted(used)],
            "source_manifest_hashes": [window.source_manifest_hash for window in dataset.windows],
            "implementation_files": protocol.implementation_files,
        }

    def _admission(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
    ) -> HistoryObservedSourceAdmissionV1:
        readset = self._readset(session, protocol, dataset)
        ref, digest = self.ledger.builder.archive.put(readset)
        return seal(
            HistoryObservedSourceAdmissionV1,
            registration_id=protocol.registration_id,
            protocol_hash=protocol.payload_hash,
            dataset_hash=dataset.payload_hash,
            scope=dataset.scope,
            registered_at=protocol.registered_at,
            data_information_asof=dataset.data_information_asof,
            dataset_sealed_at=dataset.sealed_at,
            authenticated_at=self.now(),
            daily_cutoffs=protocol.daily_cutoffs,
            source_manifest_hashes=tuple(window.source_manifest_hash for window in dataset.windows),
            readset_original_ref=ref,
            readset_original_hash=digest,
            implementation_files=protocol.implementation_files,
            selected_capture_count=len(readset["captures"]),
        )

    def _confirm_admission(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
        admission: HistoryObservedSourceAdmissionV1,
        *,
        metadata_only: bool = False,
    ) -> None:
        verify_payload(admission)
        if (
            admission.registration_id,
            admission.protocol_hash,
            admission.dataset_hash,
            admission.scope,
            admission.registered_at,
            admission.data_information_asof,
            admission.dataset_sealed_at,
            admission.daily_cutoffs,
            admission.source_manifest_hashes,
            admission.implementation_files,
        ) != (
            protocol.registration_id,
            protocol.payload_hash,
            dataset.payload_hash,
            dataset.scope,
            protocol.registered_at,
            dataset.data_information_asof,
            dataset.sealed_at,
            protocol.daily_cutoffs,
            tuple(window.source_manifest_hash for window in dataset.windows),
            protocol.implementation_files,
        ) or admission.authenticated_at > self.now():
            raise ValueError("history_observed_admission_binding_or_future_invalid")
        original = self.ledger.builder.archive.read(
            admission.readset_original_ref, admission.readset_original_hash
        )
        if metadata_only:
            boundary = snapshot_identity(session)
            initial = json.loads(original)
            jobs = self._jobs(session, protocol, dataset)
            own = sorted(
                (job for job in jobs if job.scope_id == dataset.scope.scope_id),
                key=lambda job: job.cutoff_at,
            )
            current = {**initial, "jobs": [job_payload(job) for job in own], "captures": []}
            for expected in initial["captures"]:
                capture = session.get(
                    RawCaptureRecord, expected["capture_id"], populate_existing=True
                )
                if capture is None:
                    raise ValueError("history_observed_admission_selected_capture_missing")
                current["captures"].append(capture_payload(capture))
            for capture in current["captures"]:
                ref, digest = capture["body_ref"], capture["body_hash"]
                if (
                    not isinstance(ref, str)
                    or not isinstance(digest, str)
                    or hashlib.sha256(self.ledger.builder.originals.raw_reader(ref)).hexdigest()
                    != digest
                ):
                    raise ValueError("history_observed_admission_original_changed_at_writer")
            self._sources(protocol)
            require_snapshot(session, boundary)
        else:
            current = self._readset(session, protocol, dataset)
        if original != encoded(current) or admission.selected_capture_count != len(
            current["captures"]
        ):
            raise ValueError("history_observed_admission_readset_changed")

    def _supports(
        self, dataset: HistoryObservedDatasetManifestV1
    ) -> tuple[EmpiricalDistribution, EmpiricalDistribution, EmpiricalDistribution, Decimal]:
        status = readiness(dataset)
        if not status.candidate_count_gate_met or status.rv_median is None:
            raise ValueError("history_observed_fit_60_30_30_gate_not_met")
        joint = [
            item
            for item in dataset.windows
            if label_complete(item) and item.feature.realized_vol_30m is not None
        ]

        def support(windows: list[HistoryObservedTrainingWindowV1]) -> EmpiricalDistribution:
            return EmpiricalDistribution(
                n=len(windows),
                long_mae_sorted=tuple(
                    sorted(item.path.mae_long for item in windows if item.path.mae_long is not None)
                ),
                short_mae_sorted=tuple(
                    sorted(
                        item.path.mae_short for item in windows if item.path.mae_short is not None
                    )
                ),
            )

        low = [
            item
            for item in joint
            if item.feature.realized_vol_30m is not None
            and item.feature.realized_vol_30m <= status.rv_median
        ]
        high = [
            item
            for item in joint
            if item.feature.realized_vol_30m is not None
            and item.feature.realized_vol_30m > status.rv_median
        ]
        return support(joint), support(low), support(high), status.rv_median

    def prepare_fit(
        self, session: Session, registration_id: str, scope: MarketPathScope
    ) -> HistoryObservedFitPreparation:
        boundary = snapshot_identity(session)
        protocol = self.ledger.protocol(session, registration_id)
        self._sources(protocol)
        prepared = self.ledger.prepare_saved(session, registration_id, scope)
        dataset = prepared.dataset
        # Reject count insufficiency before writing an admission original.
        baseline, low, high, middle = self._supports(dataset)
        admission = self._admission(session, protocol, dataset)
        started = self.now()
        baseline, low, high, middle = self._supports(dataset)
        completed = self.now()
        fit = seal(
            HistoryObservedFitValuesV1,
            admission=admission,
            derived_at=dataset.derived_at,
            fit_started_at=started,
            fit_completed_at=completed,
            baseline=baseline,
            conditional_low=low,
            conditional_high=high,
            training_vol_median=middle,
        )
        self._sources(protocol)
        require_snapshot(session, boundary)
        thread = threading.get_ident()
        return HistoryObservedFitPreparation(
            prepared, fit, self, thread, self._auth(dataset.payload_hash, fit.payload_hash, thread)
        )

    def save(
        self, session: Session, prepared: HistoryObservedFitPreparation
    ) -> FrozenHistoryObservedPathModelV2:
        boundary = snapshot_identity(session)
        fit = verify_payload(prepared.fit)
        dataset = prepared.dataset.dataset
        if (
            prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not prepared.authenticated_by(
                self._auth(dataset.payload_hash, fit.payload_hash, prepared.thread_id)
            )
        ):
            raise ValueError("history_observed_fit_preparation_changed")
        self.ledger.confirm_saved(session, prepared.dataset)
        protocol = self.ledger.protocol(session, dataset.registration_id)
        self._confirm_admission(session, protocol, dataset, fit.admission, metadata_only=True)
        if (
            fit.baseline,
            fit.conditional_low,
            fit.conditional_high,
            fit.training_vol_median,
        ) != self._supports(dataset):
            raise ValueError("history_observed_fit_support_changed")
        record_id = (
            "hobs_model_" + canonical_hash([dataset.registration_id, dataset.scope.scope_id])[:48]
        )
        if session.get(Record, record_id, populate_existing=True) is not None:
            value = self._saved_model(session, protocol, dataset, metadata_only=True)
            require_snapshot(session, boundary)
            return value
        saved_at = self.now()
        value = seal(FrozenHistoryObservedPathModelV2, fit=fit, model_known_at=saved_at)
        ref, digest = self.ledger.builder.archive.put(value.model_dump(mode="json"))
        self._sources(protocol)
        require_snapshot(session, boundary, before_write=True)
        session.add(
            Record(
                record_id=record_id,
                registration_id=dataset.registration_id,
                request_id=protocol.request_id,
                kind="model",
                scope_id=dataset.scope.scope_id,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=ref,
                original_hash=digest,
                known_at=saved_at,
                recorded_at=saved_at,
            )
        )
        session.flush()
        self._sources(protocol)
        require_snapshot(session, (*boundary[:4], boundary[4] + 1, *boundary[5:]))
        return value

    def model(
        self, session: Session, registration_id: str, scope: MarketPathScope
    ) -> FrozenHistoryObservedPathModelV2:
        boundary = snapshot_identity(session)
        dataset = self.ledger.dataset(session, registration_id, scope)
        protocol = self.ledger.protocol(session, registration_id)
        value = self._saved_model(session, protocol, dataset)
        self._sources(protocol)
        require_snapshot(session, boundary)
        return value

    def _saved_model(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
        *,
        metadata_only: bool = False,
    ) -> FrozenHistoryObservedPathModelV2:
        boundary = snapshot_identity(session)
        registration_id, scope = dataset.registration_id, dataset.scope
        record_id = "hobs_model_" + canonical_hash([registration_id, scope.scope_id])[:48]
        row = session.get(Record, record_id, populate_existing=True)
        if row is None:
            raise ValueError("history_observed_saved_model_missing")
        value = verify_payload(
            FrozenHistoryObservedPathModelV2.model_validate_json(row.payload_json)
        )
        if (
            row.record_id,
            row.registration_id,
            row.request_id,
            row.kind,
            row.scope_id,
            row.payload_hash,
            as_utc(row.known_at),
            as_utc(row.recorded_at),
        ) != (
            record_id,
            registration_id,
            protocol.request_id,
            "model",
            scope.scope_id,
            value.payload_hash,
            value.model_known_at,
            value.model_known_at,
        ) or value.model_known_at > self.now():
            raise ValueError("history_observed_saved_model_row_or_future_invalid")
        if (
            self.ledger.builder.archive.read(row.original_ref, row.original_hash)
            != row.payload_json.encode()
        ):
            raise ValueError("history_observed_saved_model_original_changed")
        self._confirm_admission(
            session, protocol, dataset, value.fit.admission, metadata_only=metadata_only
        )
        if value.fit.derived_at != dataset.derived_at or (
            value.fit.baseline,
            value.fit.conditional_low,
            value.fit.conditional_high,
            value.fit.training_vol_median,
        ) != self._supports(dataset):
            raise ValueError("history_observed_saved_model_actual_support_changed")
        self._sources(protocol)
        require_snapshot(session, boundary)
        return value

    def forecast(
        self,
        session: Session,
        registration_id: str,
        scope: MarketPathScope,
        cutoff_at: datetime,
        *,
        threshold: str = "0.02",
    ) -> HistoryObservedProspectiveForecastV1:
        boundary = snapshot_identity(session)
        cutoff = aware_utc(cutoff_at)
        if cutoff > self.now() or cutoff.timestamp() % 60 or threshold not in {"0.02", "0.05"}:
            raise ValueError("history_observed_prospective_cutoff_or_threshold_invalid")
        model = self.model(session, registration_id, scope)
        authenticated = self.now()
        if not model.model_known_at <= cutoff <= authenticated:
            raise ValueError("history_observed_model_not_known_at_prospective_T")
        bars, source = self.ledger.builder.originals.load(session, scope, cutoff, cutoff)
        closed = [bar for bar in bars if bar.close_at <= cutoff]
        latest = closed[-1].close_at if closed else None
        if latest is None or (cutoff - latest).total_seconds() > 180:
            raise ValueError("history_observed_prospective_recent_31_closes_required")
        by_close = {bar.close_at: bar for bar in closed}
        times = tuple(latest - timedelta(minutes=index) for index in range(30, -1, -1))
        if any(stamp not in by_close for stamp in times):
            raise ValueError("history_observed_prospective_recent_31_closes_required")
        selected = [by_close[stamp] for stamp in times]
        with localcontext() as context:
            context.prec, context.rounding = 28, ROUND_HALF_EVEN
            rv = Decimal(
                str(
                    math.sqrt(
                        sum(
                            math.log(float(right.close / left.close)) ** 2
                            for left, right in zip(selected, selected[1:], strict=False)
                        )
                    )
                )
            )
            support = (
                model.fit.conditional_low
                if rv <= model.fit.training_vol_median
                else model.fit.conditional_high
            )
            limit = Decimal(threshold)

            def probability(values: tuple[Decimal, ...]) -> Decimal:
                return (Decimal(sum(value >= limit for value in values)) + Decimal("0.5")) / (
                    Decimal(len(values)) + 1
                )

            baseline_long = probability(model.fit.baseline.long_mae_sorted)
            baseline_short = probability(model.fit.baseline.short_mae_sorted)
            conditional_long = probability(support.long_mae_sorted)
            conditional_short = probability(support.short_mae_sorted)
        ref, digest = self.ledger.builder.archive.put(source)
        value = seal(
            HistoryObservedProspectiveForecastV1,
            registration_id=registration_id,
            scope=scope,
            cutoff_at=cutoff,
            model_hash=model.payload_hash,
            model_known_at=model.model_known_at,
            model_authenticated_at=authenticated,
            feature_known_at=max(bar.known_at for bar in selected),
            latest_close_at=latest,
            feature_original_ref=ref,
            feature_original_hash=digest,
            realized_vol_30m=rv,
            selected_group="low" if rv <= model.fit.training_vol_median else "high",
            threshold=threshold,
            baseline_n=model.fit.baseline.n,
            conditional_n=support.n,
            baseline_long_probability=baseline_long,
            baseline_short_probability=baseline_short,
            conditional_long_probability=conditional_long,
            conditional_short_probability=conditional_short,
            produced_at=self.now(),
        )
        self._sources(self.ledger.protocol(session, registration_id))
        require_snapshot(session, boundary)
        return value
