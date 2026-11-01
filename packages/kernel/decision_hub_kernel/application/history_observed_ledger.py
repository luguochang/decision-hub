"""Explicit caller-owned registration/freeze transactions; no runtime job execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..persistence.db import as_utc, utcnow
from ..persistence.history_observed_records import (
    HistoryObservedAcquisitionJobRecord,
    HistoryObservedTrainingRecord,
)
from ..ports.history_observed_training import (
    HistoryObservedDatasetManifestV1,
    HistoryObservedTrainingProtocolV1,
    HistoryTrainingReadinessV1,
    fixed_cutoffs,
    readiness,
    seal,
    verify_payload,
)
from ..ports.market_path import MarketPathScope, aware_utc, canonical_hash, spot_scope
from .history_observed_dataset import (
    HistoryObservedDatasetBuilder,
    HistoryObservedPreparation,
    encoded,
    require_snapshot,
    snapshot_identity,
)

SOURCE_FILES = tuple(
    "packages/kernel/decision_hub_kernel/" + name
    for name in (
        "ports/history_observed_training.py",
        "ports/market_path.py",
        "ports/path_forecast.py",
        "decision/path_risk.py",
        "application/history_observed_dataset.py",
        "application/history_observed_ledger.py",
        "application/market_path_store.py",
        "persistence/history_observed_records.py",
        "persistence/risk_records.py",
        "persistence/db.py",
    )
) + (
    "packages/kernel/decision_hub_kernel/application/history_observed_acquisition.py",
    "packages/provider_adapters/market/okx_path.py",
    "packages/provider_adapters/research/raw_capture.py",
    "packages/kernel/decision_hub_kernel/ports/history_observed_model.py",
    "packages/kernel/decision_hub_kernel/application/history_observed_model.py",
)


class HistoryObservedLedger:
    def __init__(
        self,
        root: Path,
        builder: HistoryObservedDatasetBuilder,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.root, self.builder, self.clock = root.resolve(), builder, clock

    def sources(self) -> tuple[tuple[str, str], ...]:
        import hashlib

        return tuple(
            (name, hashlib.sha256((self.root / name).read_bytes()).hexdigest())
            for name in SOURCE_FILES
        )

    def require_sources(self, protocol: HistoryObservedTrainingProtocolV1) -> None:
        if self.sources() != protocol.implementation_files:
            raise ValueError("history_observed_implementation_changed")

    def protocol(self, session: Session, registration_id: str) -> HistoryObservedTrainingProtocolV1:
        boundary = snapshot_identity(session)
        row = session.get(HistoryObservedTrainingRecord, registration_id, populate_existing=True)
        if row is None or row.kind != "protocol" or row.scope_id != "":
            raise ValueError("history_observed_registration_missing")
        value = verify_payload(
            HistoryObservedTrainingProtocolV1.model_validate_json(row.payload_json)
        )
        if (
            self.builder.archive.read(row.original_ref, row.original_hash).decode()
            != row.payload_json
        ):
            raise ValueError("history_observed_registration_original_changed")
        if (
            row.record_id,
            row.registration_id,
            row.request_id,
            row.payload_hash,
            as_utc(row.known_at),
            as_utc(row.recorded_at),
        ) != (
            value.registration_id,
            value.registration_id,
            value.request_id,
            value.payload_hash,
            value.registered_at,
            value.registered_at,
        ) or value.registered_at > aware_utc(self.clock()):
            raise ValueError("history_observed_registration_row_changed")
        self.require_sources(value)
        require_snapshot(session, boundary)
        return value

    def register(self, session: Session, request_id: str) -> HistoryObservedTrainingProtocolV1:
        boundary = snapshot_identity(session)
        if not request_id or len(request_id) > 128:
            raise ValueError("history_observed_request_id_invalid")
        registration_id = (
            "hobs_" + canonical_hash(["history-observed-training-protocol.v1", request_id])[:48]
        )
        existing = session.get(
            HistoryObservedTrainingRecord, registration_id, populate_existing=True
        )
        if existing is not None:
            value = self.protocol(session, registration_id)
            require_snapshot(session, boundary)
            return value
        now = aware_utc(self.clock())
        protocol = seal(
            HistoryObservedTrainingProtocolV1,
            registration_id=registration_id,
            request_id=request_id,
            registered_at=now,
            source_proof_recorded_at=now,
            scopes=(spot_scope("BTC-USDT"), spot_scope("ETH-USDT")),
            daily_cutoffs=fixed_cutoffs(now),
            implementation_files=self.sources(),
        )
        self.require_sources(protocol)
        original_ref, original_hash = self.builder.archive.put(protocol.model_dump(mode="json"))
        session.add(
            HistoryObservedTrainingRecord(
                record_id=registration_id,
                registration_id=registration_id,
                request_id=request_id,
                kind="protocol",
                scope_id="",
                payload_json=encoded(protocol.model_dump(mode="json")).decode(),
                payload_hash=protocol.payload_hash,
                original_ref=original_ref,
                original_hash=original_hash,
                known_at=now,
                recorded_at=now,
            )
        )
        for scope in protocol.scopes:
            for cutoff in protocol.daily_cutoffs:
                job_id = (
                    "hobs_job_"
                    + canonical_hash([registration_id, scope.scope_id, cutoff.isoformat()])[:48]
                )
                session.add(
                    HistoryObservedAcquisitionJobRecord(
                        job_id=job_id,
                        registration_id=registration_id,
                        scope_id=scope.scope_id,
                        cutoff_at=cutoff,
                        deadline_at=now + timedelta(hours=6),
                        state="held",
                        attempts=0,
                        lease_owner=None,
                        lease_generation=0,
                        lease_expires_at=None,
                        next_attempt_at=now,
                        last_attempt_at=None,
                        checkpoint_json="{}",
                        error_code=None,
                        completed_at=None,
                        protocol_hash=protocol.payload_hash,
                        recorded_at=now,
                    )
                )
        session.flush()
        self.require_sources(protocol)
        value = self.protocol(session, registration_id)
        require_snapshot(session, (*boundary[:4], boundary[4] + 181, *boundary[5:]))
        return value

    def prepare(
        self, session: Session, registration_id: str, scope: MarketPathScope, known_by: datetime
    ) -> HistoryObservedPreparation:
        boundary = snapshot_identity(session)
        protocol = self.protocol(session, registration_id)
        value = self.builder.build(session, protocol, scope, known_by)
        self.require_sources(protocol)
        require_snapshot(session, boundary)
        return value

    def freeze(
        self, session: Session, prepared: HistoryObservedPreparation
    ) -> HistoryObservedDatasetManifestV1:
        boundary = snapshot_identity(session)
        registration_id, scope = prepared.dataset.registration_id, prepared.dataset.scope
        protocol = self.protocol(session, registration_id)
        known = prepared.dataset.data_information_asof
        if (
            prepared.dataset.protocol_hash != protocol.payload_hash
            or prepared.dataset.sealed_at > aware_utc(self.clock())
        ):
            raise ValueError("history_observed_prepared_protocol_or_clock_changed")
        self.builder.confirm(session, prepared)
        self.require_sources(protocol)
        record_id = "hobs_dataset_" + canonical_hash([registration_id, scope.scope_id])[:48]
        row = session.get(HistoryObservedTrainingRecord, record_id, populate_existing=True)
        if row is not None:
            value = self.dataset(session, registration_id, scope)
            if value.data_information_asof != known:
                raise ValueError("history_observed_dataset_already_frozen")
            require_snapshot(session, boundary)
            return value
        now = aware_utc(self.clock())
        value = seal(
            HistoryObservedDatasetManifestV1,
            **{
                **prepared.dataset.model_dump(mode="python", exclude={"payload_hash"}),
                "sealed_at": now,
            },
        )
        original_ref, original_hash = self.builder.archive.put(value.model_dump(mode="json"))
        require_snapshot(session, boundary, before_write=True)
        session.add(
            HistoryObservedTrainingRecord(
                record_id=record_id,
                registration_id=registration_id,
                request_id=protocol.request_id,
                kind="dataset",
                scope_id=scope.scope_id,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=original_ref,
                original_hash=original_hash,
                known_at=now,
                recorded_at=now,
            )
        )
        session.flush()
        self.require_sources(protocol)
        expected_boundary = (*boundary[:4], boundary[4] + 1, *boundary[5:])
        require_snapshot(session, expected_boundary)
        return value

    def _dataset_original(
        self, session: Session, registration_id: str, scope: MarketPathScope
    ) -> tuple[HistoryObservedTrainingProtocolV1, HistoryObservedDatasetManifestV1]:
        boundary = snapshot_identity(session)
        protocol = self.protocol(session, registration_id)
        record_id = "hobs_dataset_" + canonical_hash([registration_id, scope.scope_id])[:48]
        row = session.get(HistoryObservedTrainingRecord, record_id, populate_existing=True)
        if row is None:
            raise ValueError("history_observed_dataset_missing")
        value = verify_payload(
            HistoryObservedDatasetManifestV1.model_validate_json(row.payload_json)
        )
        if (
            self.builder.archive.read(row.original_ref, row.original_hash).decode()
            != row.payload_json
        ):
            raise ValueError("history_observed_dataset_original_changed")
        if (
            row.record_id,
            row.registration_id,
            row.request_id,
            row.kind,
            row.scope_id,
            row.payload_hash,
            as_utc(row.known_at),
        ) != (
            record_id,
            registration_id,
            protocol.request_id,
            "dataset",
            scope.scope_id,
            value.payload_hash,
            value.sealed_at,
        ) or not value.sealed_at <= as_utc(row.recorded_at) <= aware_utc(self.clock()):
            raise ValueError("history_observed_dataset_record_changed")
        self.require_sources(protocol)
        require_snapshot(session, boundary)
        return protocol, value

    def dataset(
        self, session: Session, registration_id: str, scope: MarketPathScope
    ) -> HistoryObservedDatasetManifestV1:
        boundary = snapshot_identity(session)
        protocol, value = self._dataset_original(session, registration_id, scope)
        self.builder.verify(session, protocol, value)
        self.require_sources(protocol)
        require_snapshot(session, boundary)
        return value

    def prepare_saved(
        self, session: Session, registration_id: str, scope: MarketPathScope
    ) -> HistoryObservedPreparation:
        boundary = snapshot_identity(session)
        protocol, value = self._dataset_original(session, registration_id, scope)
        prepared = self.builder.prepare_verified(session, protocol, value)
        self.require_sources(protocol)
        require_snapshot(session, boundary)
        return prepared

    def confirm_saved(self, session: Session, prepared: HistoryObservedPreparation) -> None:
        boundary = snapshot_identity(session)
        dataset = prepared.dataset
        protocol, original = self._dataset_original(session, dataset.registration_id, dataset.scope)
        if original != dataset:
            raise ValueError("history_observed_saved_dataset_original_changed")
        self.builder.confirm(session, prepared)
        self.require_sources(protocol)
        require_snapshot(session, boundary)

    def readiness(
        self, session: Session, registration_id: str, scope: MarketPathScope
    ) -> HistoryTrainingReadinessV1:
        # Numerical count gates never confer source admission or actual fit permission.
        return readiness(self.dataset(session, registration_id, scope))
