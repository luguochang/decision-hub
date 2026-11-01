"""Immutable evidence qualification and append-only post-save confirmation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from types import MappingProxyType

from sqlalchemy import select

from ..persistence.db import as_utc
from ..persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as ForecastRow,
)
from ..persistence.history_dataset_model_v2_records import HistoryDatasetModelV2Record as ModelRow
from ..persistence.history_model_qualification_v3_records import (
    HistoryModelQualificationAcceptanceV3Record as EventRow,
)
from ..persistence.history_model_qualification_v3_records import (
    HistoryModelQualificationV3Record as QualificationRow,
)
from ..persistence.history_model_qualification_v3_records import (
    HistoryQualifiedForecastAcceptanceV3Record as AckRow,
)
from ..persistence.history_model_qualification_v3_records import (
    HistoryQualifiedForecastBindingV3Record as BindingRow,
)
from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_dataset_v2 import HistoryDatasetOriginalV2
from ..ports.history_model_qualification_v3 import (
    HistoryModelQualificationAcceptanceV3,
    HistoryModelQualificationV3,
)
from ..ports.history_observed_training import seal, verify_payload
from ..ports.market_path import canonical_hash, spot_scope
from . import history_forecast_transfer39 as transfer
from .history_forecast_dispatch39 import HistoryForecastDispatch39Service
from .history_model_freeze_v3 import (
    FrozenModelBundleV3,
    FrozenModelPreparationV3,
    HistoryModelFreezeV3Publisher,
    HistoryModelQualificationBoundV3Error,
    sources41,
)
from .history_observed_dataset import (
    HistoryObservedArchive,
    encoded,
    require_snapshot,
    snapshot_identity,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
RECORD_TYPES = (QualificationRow, EventRow, BindingRow, AckRow)
SCHEMA_COLUMNS = MappingProxyType(
    {
        **dict(transfer.FULL_SCHEMA_COLUMNS),
        **{
            record.__tablename__: tuple(column.name for column in record.__table__.columns)
            for record in RECORD_TYPES
        },
        ForecastRow.__tablename__: tuple(column.name for column in ForecastRow.__table__.columns),
    }
)


class HistoryQualificationCommitUnconfirmedV3(ValueError):
    automatic_retry = False


def schemas41(session, tables):
    result = []
    for table in sorted(set(tables)):
        if table not in SCHEMA_COLUMNS:
            raise ValueError("history41_metadata_table_invalid")
        rows = tuple(
            tuple(row)
            for row in session.connection().exec_driver_sql(f'PRAGMA table_xinfo("{table}")').all()
        )
        if (
            len(rows) != len(SCHEMA_COLUMNS[table])
            or set(row[1] for row in rows) != set(SCHEMA_COLUMNS[table])
            or any(row[6] != 0 for row in rows)
        ):
            raise ValueError("history41_complete_metadata_schema_changed")
        result.append((table, rows))
    return tuple(result)


def verify_schemas41(session, expected):
    if not transfer.native_equal39(
        schemas41(session, tuple(table for table, _ in expected)), expected
    ):
        raise ValueError("history41_complete_metadata_schema_changed")


def native_record41(session, record, identifier):
    if record not in (*RECORD_TYPES, ModelRow):
        raise ValueError("history41_native_record_type_invalid")
    table, key = record.__tablename__, tuple(record.__table__.primary_key)[0].name
    row = (
        session.connection()
        .exec_driver_sql(f'SELECT * FROM "{table}" WHERE "{key}"=?', (identifier,))
        .one_or_none()
    )
    return None if row is None else tuple(row)


def qualification_id(owner, request, scope, evidence):
    return "hqual3_" + canonical_hash([owner, request, scope, evidence])[:48]


class HistoryModelQualificationV3Service:
    def __init__(self, forecasts, *, publication_grants, serving_grants):
        scopes = {spot_scope(asset).scope_id for asset in ("BTC-USDT", "ETH-USDT")}
        if (
            type(forecasts) is not HistoryForecastDispatch39Service
            or type(publication_grants) is not dict
            or type(serving_grants) is not dict
            or not set(publication_grants) <= scopes
            or not set(serving_grants) <= scopes
            or any(
                forecasts.grants.get(scope) != model for scope, model in publication_grants.items()
            )
            or any(
                type(value) is not str
                or not value.startswith("hqual3_")
                or len(value) != 55
                or any(c not in "0123456789abcdef" for c in value[7:])
                for value in serving_grants.values()
            )
        ):
            raise ValueError("history_qualification_server_grants_invalid")
        self.forecasts, self.originals = forecasts, forecasts.originals
        self.commands, self.owner_claim = forecasts.commands, forecasts.owner_claim
        self.publication_grants = MappingProxyType(dict(publication_grants))
        self.serving_grants = MappingProxyType(dict(serving_grants))
        self.root = (self.originals.data_directory / "history-model-qualification-v3").resolve()
        self.archive = HistoryObservedArchive(self.root / "originals")
        self.publisher = HistoryModelFreezeV3Publisher(forecasts)
        self._identity = (
            forecasts,
            self.originals,
            self.commands,
            self.owner_claim,
            self.publication_grants,
            self.serving_grants,
            self.root,
            self.archive,
            self.publisher,
        )

    def _sources(self, expected=None):
        if (
            (
                self.forecasts,
                self.forecasts.originals,
                self.forecasts.commands,
                self.forecasts.owner_claim,
                self.publication_grants,
                self.serving_grants,
                self.root,
                self.archive,
                self.publisher,
            )
            != self._identity
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or any(
                getattr(cls, name) is not function or function.__code__ is not code
                for cls, name, function, code in LOADED_METHODS
            )
            or any(
                globals()[name] is not function or function.__code__ is not code
                for name, function, code in LOADED_FUNCTIONS
            )
        ):
            raise ValueError("history_qualification_dependency_or_callable_changed")
        return sources41(self.forecasts, expected)

    def _fence_path(self, identifier):
        return self.root / "fences" / (canonical_hash([self.owner_claim, identifier]) + ".json")

    def _fence(self, identifier, request):
        path = self._fence_path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = encoded(
            dict(
                qualification_id=identifier,
                owner_claim=self.owner_claim,
                request_id=request,
                automatic_retry=False,
            )
        )
        try:
            with path.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise HistoryQualificationCommitUnconfirmedV3(
                "history_qualification_fence_requires_readonly_reconciliation"
            ) from None
        return body

    def _release(self, identifier, body):
        path = self._fence_path(identifier)
        if path.read_bytes() != body:
            raise ValueError("history_qualification_fence_changed")
        path.unlink()

    def prepare(self, *, model_ids, request_id, budget_seconds=180.0, checkpoint=None):
        if not set(model_ids) <= set(self.publication_grants.values()):
            raise ValueError("history_qualification_publication_not_granted")
        self._sources()
        with self.commands._read_session() as session:
            return self.publisher.prepare(
                session,
                model_ids=model_ids,
                request_id=request_id,
                budget_seconds=budget_seconds,
                checkpoint=checkpoint,
            )

    def _identity_read(self, session, record, dto, identifier, id_field):
        row = session.get(record, identifier, populate_existing=True)
        if row is None or row.owner_claim != self.owner_claim:
            raise ValueError("history_qualification_original_missing_or_owner_conflict")
        body = self.archive.read(row.original_ref, row.original_hash)
        value = verify_payload(dto.model_validate_json(body))
        if (
            getattr(value, id_field) != identifier
            or value.owner_claim != self.owner_claim
            or body != row.payload_json.encode()
            or row.payload_hash != value.payload_hash
            or value.recorded_at > self.originals.now()
        ):
            raise ValueError("history_qualification_original_identity_changed")
        for column in record.__table__.columns:
            name = column.name
            if name in ("payload_json", "payload_hash", "original_ref", "original_hash"):
                continue
            expected = getattr(value, name)
            actual = getattr(row, name)
            if hasattr(expected, "tzinfo"):
                actual = as_utc(actual)
            if type(actual) is not type(expected) or actual != expected:
                raise ValueError("history_qualification_complete_header_changed")
        return value

    def _header(self, session, identifier):
        value = self._identity_read(
            session, QualificationRow, HistoryModelQualificationV3, identifier, "qualification_id"
        )
        if identifier != qualification_id(
            value.owner_claim,
            value.request_id,
            value.scope_id,
            value.model_dump(mode="json", exclude={"qualification_id", "payload_hash"}),
        ):
            raise ValueError("history_qualification_granted_content_identity_changed")
        return value

    def _event(self, session, identifier, *, event_id=None):
        selected = event_id or session.scalar(
            select(EventRow.event_id)
            .where(EventRow.qualification_id == identifier)
            .order_by(EventRow.version.desc())
            .limit(1)
        )
        return (
            None
            if selected is None
            else self._identity_read(
                session, EventRow, HistoryModelQualificationAcceptanceV3, selected, "event_id"
            )
        )

    def _small_model(self, session, value, model):
        row = session.get(ModelRow, value.model_id, populate_existing=True)
        if (
            row is None
            or (
                row.owner_claim,
                row.scope_id,
                row.dataset_id,
                row.payload_hash,
                row.original_ref,
                row.original_hash,
                as_utc(row.known_at),
            )
            != (
                self.owner_claim,
                value.scope_id,
                value.dataset_id,
                value.model_hash,
                value.model_original_ref,
                value.model_original_hash,
                value.model_known_at,
            )
            or row.payload_json.encode() != encoded(model.model_dump(mode="json"))
            or model.payload_hash != value.model_hash
            or (row.request_id, row.plan_id, as_utc(row.recorded_at))
            != (model.request_id, model.plan_id, model.recorded_at)
            or (
                model.model_id,
                model.dataset_id,
                model.dataset_hash,
                model.model_known_at,
                model.data_information_asof,
                model.dataset_original_ref,
                model.dataset_original_hash,
            )
            != (
                value.model_id,
                value.dataset_id,
                value.dataset_hash,
                value.model_known_at,
                value.data_information_asof,
                value.dataset_original_ref,
                value.dataset_original_hash,
            )
        ):
            raise ValueError("history_qualification_exact_live_model_header_changed")

    def _read(
        self,
        session,
        identifier,
        *,
        confirmed=True,
        checkpoint=None,
        allow_fence=False,
        event_id=None,
    ):
        check = checkpoint or (lambda: None)
        boundary = snapshot_identity(session)
        self._sources()
        schema = schemas41(
            session,
            (*transfer.PROJECTIONS, QualificationRow.__tablename__, EventRow.__tablename__),
        )
        if not allow_fence and self._fence_path(identifier).exists():
            raise HistoryQualificationCommitUnconfirmedV3(
                "history_qualification_fence_requires_readonly_reconciliation"
            )
        value = self._header(session, identifier)
        self._sources(value.source_files)
        event = self._event(session, identifier, event_id=event_id)
        native = tuple(
            (record, identity, native_record41(session, record, identity))
            for record, identity in (
                (QualificationRow, identifier),
                (ModelRow, value.model_id),
                *(((EventRow, event.event_id),) if event is not None else ()),
            )
        )
        if event is not None and (
            event.qualification_id != identifier
            or event.qualification_hash != value.payload_hash
            or event.confirmation_at < value.recorded_at
        ):
            raise ValueError("history_qualification_acceptance_identity_changed")
        if confirmed and (event is None or event.state != "confirmed"):
            raise ValueError("history_qualification_not_confirmed")
        bundle = FrozenModelBundleV3(
            self.root, value.bundle_ref, value.bundle_hash, value.manifest_hash, checkpoint=check
        )
        if (
            bundle.path.stat().st_size != value.bundle_bytes
            or bundle.manifest["owner_claim"] != self.owner_claim
            or bundle.manifest["fixture_mode"] is not self.originals.fixture_mode
            or tuple(tuple(item) for item in bundle.manifest["source_files"]) != value.source_files
        ):
            raise ValueError("history_qualification_bundle_identity_changed")
        model = bundle.model(value.model_id)
        frozen_schema = tuple(
            (table, tuple(tuple(row) for row in rows)) for table, rows in bundle.manifest["schemas"]
        )
        if not transfer.native_equal39(
            transfer.capture_schemas39(session, tuple(transfer.PROJECTIONS)), frozen_schema
        ):
            raise ValueError("history_qualification_exact_schema_version_changed")
        entry = next(item for item in bundle.manifest["models"] if item[0] == value.model_id)
        if tuple(entry[3:5]) != (value.model_original_ref, value.model_original_hash):
            raise ValueError("history_qualification_retained_model_ref_changed")
        self._small_model(session, value, model)
        self._header(session, identifier)
        if self._event(session, identifier, event_id=event_id) != event:
            raise ValueError("history_qualification_acceptance_changed_at_exit")
        verify_schemas41(session, schema)
        if any(
            not transfer.native_equal39(native_record41(session, record, identity), expected)
            for record, identity, expected in native
        ):
            raise ValueError("history_qualification_complete_native_header_changed_at_exit")
        self._sources(value.source_files)
        bundle.verify()
        require_snapshot(session, boundary)
        check()
        return value, event, bundle, model

    def read(self, session, qualification_id, *, confirmed=True, checkpoint=None):
        return self._read(session, qualification_id, confirmed=confirmed, checkpoint=checkpoint)

    def _attempt(self, action):
        began = committed = commit_started = False
        try:
            with self.commands.database.factory() as session:
                self.commands.acquisition._writer(session)
                began = True
                schema = schemas41(
                    session,
                    (
                        QualificationRow.__tablename__,
                        EventRow.__tablename__,
                        ModelRow.__tablename__,
                    ),
                )
                result = action(session)
                verify_schemas41(session, schema)
                commit_started = True
                session.commit()
                committed = True
            return result
        except Exception as error:
            if committed or (began and commit_started):
                raise HistoryQualificationCommitUnconfirmedV3(
                    "history_qualification_commit_or_ack_unconfirmed"
                ) from error
            raise

    def publish(self, prepared, *, budget_seconds=180.0, checkpoint=None):
        if type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 180:
            raise ValueError("history_qualification_publish_budget_invalid")
        deadline = perf_counter() + budget_seconds

        def check():
            if checkpoint is not None:
                checkpoint()
            if perf_counter() >= deadline:
                raise HistoryModelQualificationBoundV3Error(
                    "history_qualification_publish_budget_reached"
                )

        check()
        if type(prepared) is not FrozenModelPreparationV3:
            raise ValueError("history_qualification_exact_freeze_preparation_required")
        with self.commands._read_session() as session:
            self.publisher.authenticate(session, prepared)
            bundle = FrozenModelBundleV3(
                self.root,
                prepared.bundle_ref,
                prepared.bundle_hash,
                prepared.manifest_hash,
                checkpoint=check,
            )
            models = tuple(
                verify_payload(HistoryDatasetFitModelV2.model_validate_json(body))
                for body in prepared.models
            )
            frozen_schema = tuple(
                (table, tuple(tuple(row) for row in rows))
                for table, rows in bundle.manifest["schemas"]
            )
            headers = []
            for model in models:
                if self.publication_grants.get(model.scope_id) != model.model_id:
                    raise ValueError("history_qualification_publication_not_granted")
                entry = next(
                    item for item in bundle.manifest["models"] if item[0] == model.model_id
                )
                if bundle.model(model.model_id) != model:
                    raise ValueError("history_qualification_frozen_model_changed")
                value = seal(
                    HistoryModelQualificationV3,
                    qualification_id="hqual3_" + "0" * 48,
                    owner_claim=self.owner_claim,
                    request_id=prepared.request_id,
                    scope_id=model.scope_id,
                    model_id=model.model_id,
                    model_hash=model.payload_hash,
                    model_original_ref=entry[3],
                    model_original_hash=entry[4],
                    model_known_at=model.model_known_at,
                    dataset_id=model.dataset_id,
                    dataset_hash=model.dataset_hash,
                    dataset_original_ref=model.dataset_original_ref,
                    dataset_original_hash=model.dataset_original_hash,
                    data_information_asof=model.data_information_asof,
                    source_files=prepared.sources,
                    bundle_ref=prepared.bundle_ref,
                    bundle_hash=prepared.bundle_hash,
                    bundle_bytes=prepared.bundle_bytes,
                    manifest_hash=prepared.manifest_hash,
                    verified_at=prepared.verified_at,
                    recorded_at=self.originals.now(),
                    fixture_mode=self.originals.fixture_mode,
                )
                value = seal(
                    HistoryModelQualificationV3,
                    **{
                        **value.model_dump(exclude={"payload_hash", "qualification_id"}),
                        "qualification_id": qualification_id(
                            self.owner_claim,
                            prepared.request_id,
                            model.scope_id,
                            value.model_dump(
                                mode="json", exclude={"payload_hash", "qualification_id"}
                            ),
                        ),
                    },
                )
                self._small_model(session, value, model)
                headers.append(value)
        results = []
        for value in headers:
            check()
            with self.commands._read_session() as session:
                existing = session.scalar(
                    select(QualificationRow.qualification_id).where(
                        QualificationRow.owner_claim == self.owner_claim,
                        QualificationRow.request_id == value.request_id,
                        QualificationRow.scope_id == value.scope_id,
                    )
                )
                if existing is not None:
                    first, _event, _bundle, first_model = self.read(
                        session, existing, checkpoint=check
                    )
                    if (
                        first.model_id,
                        first.model_hash,
                        first.dataset_id,
                        first.dataset_hash,
                        first.model_known_at,
                        first.data_information_asof,
                        first.source_files,
                    ) != (
                        value.model_id,
                        value.model_hash,
                        value.dataset_id,
                        value.dataset_hash,
                        value.model_known_at,
                        value.data_information_asof,
                        value.source_files,
                    ):
                        raise ValueError("history_qualification_firstsave_request_conflict")
                    if encoded(first_model.model_dump(mode="json")) not in prepared.models:
                        raise ValueError("history_qualification_firstsave_model_semantics_changed")
                    results.append(first)
                    continue
            ref, digest = self.archive.put(value.model_dump(mode="json"))
            fence = self._fence(value.qualification_id, value.request_id)
            header_committed = False
            try:
                model = next(item for item in models if item.model_id == value.model_id)

                def insert(session, value=value, ref=ref, digest=digest, model=model):
                    check()
                    self.publisher.authenticate(session, prepared)
                    if not transfer.native_equal39(
                        transfer.capture_schemas39(session, tuple(transfer.PROJECTIONS)),
                        frozen_schema,
                    ):
                        raise ValueError("history_qualification_exact_schema_version_changed")
                    if session.get(QualificationRow, value.qualification_id) is not None:
                        raise ValueError("history_qualification_firstsave_request_exists")
                    self._small_model(session, value, model)
                    session.add(
                        QualificationRow(
                            **{
                                name: getattr(value, name)
                                for name in QualificationRow.__table__.columns.keys()
                                if name
                                not in (
                                    "payload_json",
                                    "payload_hash",
                                    "original_ref",
                                    "original_hash",
                                )
                            },
                            payload_json=encoded(value.model_dump(mode="json")).decode(),
                            payload_hash=value.payload_hash,
                            original_ref=ref,
                            original_hash=digest,
                        )
                    )
                    session.flush()
                    check()

                self._attempt(insert)
                header_committed = True
                # Only a new physical RO connection can establish actual save confirmation.
                with self.commands._read_session() as session:
                    loaded, event, saved_bundle, _model = self._read(
                        session,
                        value.qualification_id,
                        confirmed=False,
                        allow_fence=True,
                        checkpoint=check,
                    )
                    if loaded != value or event is not None:
                        raise ValueError("history_qualification_saved_readback_changed")
                    saved_bundle.verify()
                confirmation = self.originals.now()
                acceptance = seal(
                    HistoryModelQualificationAcceptanceV3,
                    event_id="hqevt3_" + canonical_hash([value.qualification_id, 1])[:48],
                    qualification_id=value.qualification_id,
                    qualification_hash=value.payload_hash,
                    owner_claim=self.owner_claim,
                    request_id=value.request_id,
                    state="confirmed",
                    version=1,
                    confirmation_at=confirmation,
                    recorded_at=self.originals.now(),
                )
                event_ref, event_hash = self.archive.put(acceptance.model_dump(mode="json"))

                def confirm(
                    session,
                    value=value,
                    acceptance=acceptance,
                    event_ref=event_ref,
                    event_hash=event_hash,
                ):
                    check()
                    self._sources(value.source_files)
                    if (
                        self._header(session, value.qualification_id) != value
                        or self._event(session, value.qualification_id) is not None
                    ):
                        raise ValueError("history_qualification_confirmation_CAS_changed")
                    session.add(
                        EventRow(
                            **{
                                name: getattr(acceptance, name)
                                for name in EventRow.__table__.columns.keys()
                                if name
                                not in (
                                    "payload_json",
                                    "payload_hash",
                                    "original_ref",
                                    "original_hash",
                                )
                            },
                            payload_json=encoded(acceptance.model_dump(mode="json")).decode(),
                            payload_hash=acceptance.payload_hash,
                            original_ref=event_ref,
                            original_hash=event_hash,
                        )
                    )
                    session.flush()
                    check()

                self._attempt(confirm)
                with self.commands._read_session() as session:
                    loaded, event, saved_bundle, _ = self._read(
                        session, value.qualification_id, allow_fence=True, checkpoint=check
                    )
                    if loaded != value or event != acceptance:
                        raise ValueError("history_qualification_confirmation_readback_changed")
                    saved_bundle.verify()
                self._release(value.qualification_id, fence)
                results.append(value)
            except Exception as error:
                # A known/unknown saved header cannot become eligible through retry.
                # The durable fence remains until explicit read-only reconciliation.
                if not header_committed and not isinstance(
                    error, HistoryQualificationCommitUnconfirmedV3
                ):
                    self._release(value.qualification_id, fence)
                raise
        return tuple(results)

    def audit(self, qualification_id, *, budget_seconds=180.0, checkpoint=None):
        if type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 180:
            raise ValueError("history_qualification_cold_audit_budget_invalid")
        deadline = perf_counter() + budget_seconds

        def check():
            if checkpoint is not None:
                checkpoint()
            if perf_counter() >= deadline:
                raise HistoryModelQualificationBoundV3Error(
                    "history_qualification_cold_audit_budget_reached"
                )

        with self.commands._read_session() as live:
            value, event, bundle, model = self.read(live, qualification_id, checkpoint=check)
        for logical, digest, _size, _member in bundle.manifest["files"]:
            check()
            bundle.read(logical, digest)
        dataset = verify_payload(
            HistoryDatasetOriginalV2.model_validate_json(
                bundle.read(value.dataset_original_ref, value.dataset_original_hash)
            )
        )
        for source_ref, source_hash, sources in (
            (model.source_original_ref, model.source_original_hash, model.source_files),
            (dataset.source_original_ref, dataset.source_original_hash, dataset.source_files),
        ):
            source = json.loads(bundle.read(source_ref, source_hash))
            if tuple((item["path"], item["sha256"]) for item in source["files"]) != sources or any(
                hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
                != item["sha256"]
                for item in source["files"]
            ):
                raise ValueError("history_qualification_cold_source_original_changed")
        with bundle.snapshot_session() as frozen:
            self.originals._confirm_rows(frozen, dataset.provenance["row_readset"])
            check()
            result = self.forecasts.models._derive(frozen, dataset, model.scope_id)
            check()
            if (
                result["baseline"],
                result["low"],
                result["high"],
                result["rv_median"],
                tuple(result["windows"]),
            ) != (
                model.baseline,
                model.conditional_low,
                model.conditional_high,
                model.training_vol_median,
                model.windows,
            ):
                raise ValueError("history_qualification_cold_actual_math_changed")
        bundle.verify()
        return dict(
            qualification_id=value.qualification_id,
            qualification_hash=value.payload_hash,
            version=event.version,
            state=event.state,
            bundle_hash=value.bundle_hash,
            model_id=model.model_id,
            model_hash=model.payload_hash,
            frozen_math_verified=True,
            financial_qualified=False,
            automatic_retry=False,
            verified_at=self.originals.now().isoformat(),
        )

    def append_state(self, qualification_id, *, request_id, state, reason_code):
        """Trusted local publication control; a revoked original is never renewed."""
        if state not in ("revoked", "unconfirmed") or not reason_code:
            raise ValueError("history_qualification_terminal_state_required")
        with self.commands._read_session() as session:
            value, previous, _bundle, _model = self.read(session, qualification_id)
            if self.publication_grants.get(value.scope_id) != value.model_id:
                raise ValueError("history_qualification_publication_not_granted")
        event = seal(
            HistoryModelQualificationAcceptanceV3,
            event_id="hqevt3_"
            + canonical_hash([qualification_id, previous.version + 1, request_id])[:48],
            qualification_id=qualification_id,
            qualification_hash=value.payload_hash,
            owner_claim=self.owner_claim,
            request_id=request_id,
            state=state,
            version=previous.version + 1,
            confirmation_at=self.originals.now(),
            recorded_at=self.originals.now(),
            reason_code=reason_code,
        )
        ref, digest = self.archive.put(event.model_dump(mode="json"))
        fence = self._fence(qualification_id, request_id)
        committed = False
        try:

            def append(session):
                self._sources(value.source_files)
                if (
                    self._header(session, qualification_id) != value
                    or self._event(session, qualification_id) != previous
                ):
                    raise ValueError("history_qualification_terminal_version_CAS_changed")
                session.add(
                    EventRow(
                        **{
                            name: getattr(event, name)
                            for name in EventRow.__table__.columns.keys()
                            if name
                            not in ("payload_json", "payload_hash", "original_ref", "original_hash")
                        },
                        payload_json=encoded(event.model_dump(mode="json")).decode(),
                        payload_hash=event.payload_hash,
                        original_ref=ref,
                        original_hash=digest,
                    )
                )
                session.flush()

            self._attempt(append)
            committed = True
            with self.commands._read_session() as session:
                loaded, actual, bundle, _ = self._read(
                    session, qualification_id, confirmed=False, allow_fence=True
                )
                if loaded != value or actual != event:
                    raise ValueError("history_qualification_terminal_readback_changed")
                bundle.verify()
            self._release(qualification_id, fence)
            return event
        except Exception as error:
            if not committed and not isinstance(error, HistoryQualificationCommitUnconfirmedV3):
                self._release(qualification_id, fence)
            raise


LOADED_METHODS = tuple(
    (cls, name, getattr(cls, name), getattr(cls, name).__code__)
    for cls in (
        HistoryModelQualificationV3Service,
        FrozenModelBundleV3,
        HistoryModelFreezeV3Publisher,
    )
    for name in cls.__dict__
    if callable(getattr(cls, name)) and hasattr(getattr(cls, name), "__code__")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "sources41",
        "schemas41",
        "verify_schemas41",
        "native_record41",
        "qualification_id",
        "encoded",
        "snapshot_identity",
        "require_snapshot",
        "seal",
        "verify_payload",
    )
)
