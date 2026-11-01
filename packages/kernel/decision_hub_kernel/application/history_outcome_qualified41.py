"""Finite saved-forecast batch reader for the confirmed qualification sidecar.

The capability is issued and discarded in one caller-owned physical RO snapshot.
Historical 38/39 originals retain their complete deep reader. Qualified originals
share one actual issued model proof per model, without repeating bundle reads for
each forecast. This module neither writes originals nor grants production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from ..persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as ForecastRow,
)
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
from ..ports.history_model_qualification_v3 import (
    HistoryModelQualificationAcceptanceV3,
    HistoryModelQualificationV3,
)
from ..ports.history_observed_training import verify_payload
from ..ports.market_path import spot_scope
from . import history_forecast_transfer39 as transfer
from . import history_outcome_forecasts_v2 as old
from .history_forecast_dispatch41 import HistoryForecastDispatch41Service
from .history_model_qualification_v3 import native_record41, schemas41, verify_schemas41
from .history_observed_dataset import encoded, require_snapshot, snapshot_identity
from .history_outcome_originals_v2 import (
    CaptureCacheV2,
    HistoryOutcomeCaptureAuthorityV2,
    load_window,
)
from .history_qualified_forecast_v3 import QualifiedModelProofV3

SOURCE_FILE = "packages/kernel/decision_hub_kernel/application/history_outcome_qualified41.py"
LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class _QualifiedEntry:
    exact_model: tuple
    proof: QualifiedModelProofV3
    model_bytes: bytes


class _QualifiedRequest:
    """Issuer, immutable leaves and physical snapshot live only in this call."""

    def __init__(self, reader, session, boundary, sources):
        self.reader, self.session, self.boundary, self.sources = reader, session, boundary, sources
        self.thread_id = threading.get_ident()
        self.key, self.entries, self.leaves = secrets.token_bytes(32), (), ()
        self.active = True
        self.context = self._context()
        self.authentication = b""

    def _context(self):
        service = self.reader.forecasts
        return (
            id(self),
            id(self.reader),
            id(service),
            id(service.writer),
            *(id(value) for value in self.boundary[:4]),
            *self.boundary[4:],
            transfer.database_identity(self.session),
            service.owner_claim,
            tuple(sorted(service.grants.items())),
            tuple(sorted(service.writer.qualifications.serving_grants.items())),
            str(service.originals.data_directory),
            str(service.originals.source_root),
            service.originals.fixture_mode,
            self.sources,
        )

    def _leaf(self, entry):
        return (
            entry.exact_model,
            id(entry.proof),
            entry.model_bytes,
            self.reader.forecasts.writer._proof_leaves(entry.proof),
            entry.proof.authentication,
        )

    def _mac(self):
        return hmac.digest(
            self.key,
            encoded(
                [
                    self.context,
                    self.thread_id,
                    [
                        (
                            entry.exact_model,
                            id(entry.proof),
                            hashlib.sha256(entry.model_bytes).hexdigest(),
                            entry.proof.authentication.hex(),
                        )
                        for entry in self.entries
                    ],
                ]
            ),
            "sha256",
        )

    def issue(self, entries):
        if self.entries or not self.active:
            raise ValueError("history_outcome_qualified_request_reissued")
        self.entries = tuple(entries)
        self.leaves = tuple(self._leaf(entry) for entry in self.entries)
        self.authentication = self._mac()

    def authenticate(self):
        if (
            not self.active
            or self.thread_id != threading.get_ident()
            or not transfer.native_equal39(self.context, self._context())
            or not transfer.native_equal39(self.leaves, tuple(self._leaf(e) for e in self.entries))
            or not hmac.compare_digest(self.authentication, self._mac())
        ):
            raise ValueError("history_outcome_qualified_request_capability_changed")
        self.reader._sources(self.sources)
        require_snapshot(self.session, self.boundary, before_write=True)

    def close(self):
        self.active, self.key, self.entries, self.leaves = False, b"", (), ()


class HistoryOutcomeQualified41Authenticator:
    def __init__(self, forecasts):
        if type(forecasts) is not HistoryForecastDispatch41Service:
            raise ValueError("history_outcome_actual_qualified_dispatch_required")
        self.forecasts = forecasts
        self.legacy_reader = old.HistoryOutcomeForecastsV2Authenticator(forecasts.legacy)
        self._bindings = tuple(
            (obj, name, getattr(type(obj), name), getattr(type(obj), name).__code__)
            for obj, names in (
                (
                    self.legacy_reader,
                    (
                        "_sources",
                        "_forecast_schema",
                        "_forecast_row",
                        "_issue_model",
                        "_capture_files",
                        "_fingerprint",
                        "read_many",
                    ),
                ),
                (
                    forecasts.writer,
                    (
                        "issue_model",
                        "_authenticate",
                        "_proof_leaves",
                        "_binding",
                        "_ack",
                        "_selected",
                        "_fence_path",
                        "discard_model_proof",
                    ),
                ),
                (forecasts.legacy.writer, ("_saved_identity", "_numerics", "_fence_path")),
            )
            for name in names
        )
        self._identity = (
            forecasts,
            forecasts.writer,
            forecasts.legacy,
            forecasts.models,
            forecasts.originals,
            forecasts.commands,
            forecasts.archive,
            forecasts.owner_claim,
            tuple(sorted(forecasts.grants.items())),
            forecasts.writer.qualifications,
            tuple(sorted(forecasts.writer.qualifications.serving_grants.items())),
            self.legacy_reader,
        )

    def _sources(self, expected=None):
        service = self.forecasts
        if (
            (
                service,
                service.writer,
                service.legacy,
                service.models,
                service.originals,
                service.commands,
                service.archive,
                service.owner_claim,
                tuple(sorted(service.grants.items())),
                service.writer.qualifications,
                tuple(sorted(service.writer.qualifications.serving_grants.items())),
                self.legacy_reader,
            )
            != self._identity
            or type(self.legacy_reader) is not old.HistoryOutcomeForecastsV2Authenticator
            or self.legacy_reader.forecasts is not service.legacy
            or any(
                getattr(type(obj), name) is not fn
                or getattr(getattr(obj, name), "__func__", getattr(obj, name)) is not fn
                or fn.__code__ is not code
                for obj, name, fn, code in self._bindings
            )
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or hashlib.sha256(
                (service.originals.source_root / SOURCE_FILE).read_bytes()
            ).hexdigest()
            != LOADED_SOURCE_SHA256
            or any(
                getattr(cls, name) is not fn or fn.__code__ is not code
                for cls, name, fn, code in LOADED_METHODS
            )
            or any(
                globals()[name] is not fn or fn.__code__ is not code
                for name, fn, code in LOADED_FUNCTIONS
            )
            or any(globals()[name] is not value for name, value in LOADED_CLASSES)
            or transfer is not LOADED_TRANSFER
            or old is not LOADED_OLD
            or any(
                getattr(transfer, name) is not fn or fn.__code__ is not code
                for name, fn, code in LOADED_TRANSFER_FUNCTIONS
            )
        ):
            raise ValueError("history_outcome_qualified_loaded_source_or_dependency_changed")
        legacy_sources = self.legacy_reader._sources()
        result = (*legacy_sources, service._sources())
        if expected is not None and not transfer.native_equal39(result, expected):
            raise ValueError("history_outcome_qualified_source_changed")
        return result

    @staticmethod
    def _check(budget_seconds, checkpoint):
        if (
            type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds)
            or not 0 < budget_seconds <= 120
            or (checkpoint is not None and not callable(checkpoint))
        ):
            raise ValueError("history_outcome_forecast_batch_or_budget_invalid")
        deadline = perf_counter() + budget_seconds

        def check():
            if perf_counter() >= deadline:
                raise old.HistoryOutcomeForecastBudgetV2Error(
                    "history_outcome_full_score_budget_reached"
                )
            if checkpoint is not None:
                checkpoint()
            if perf_counter() >= deadline:
                raise old.HistoryOutcomeForecastBudgetV2Error(
                    "history_outcome_full_score_budget_reached"
                )

        return check

    def _issue_model(self, session, scope_id, check):
        # This actual issuer consumes the caller's native RO session. It does
        # not use authenticate_model(), which would open another snapshot.
        return self.forecasts.writer.issue_model(session, scope_id, checkpoint=check)

    def _entry(self, session, scope_id, check):
        check()
        proof = self._issue_model(session, scope_id, check)
        if type(proof) is not QualifiedModelProofV3 or proof.issuer is not self.forecasts.writer:
            raise ValueError("history_outcome_actual_qualified_model_proof_required")
        qualification, acceptance, model = self.forecasts.writer._authenticate(session, proof)
        if (
            model.scope_id != scope_id
            or qualification.scope_id != scope_id
            or self.forecasts.grants.get(scope_id) != model.model_id
            or model.owner_claim != self.forecasts.owner_claim
            or acceptance.confirmation_at > self.forecasts.originals.now()
        ):
            raise ValueError("history_outcome_exact_qualified_model_changed")
        check()
        return _QualifiedEntry(
            (
                model.model_id,
                model.payload_hash,
                qualification.model_original_ref,
                qualification.model_original_hash,
                qualification.qualification_id,
            ),
            proof,
            proof.model_bytes,
        )

    def _source_files(self, files, sources, check):
        pairs = dict(pair for vector in sources for pair in vector)
        pairs[SOURCE_FILE], pairs[old.SOURCE_FILE] = LOADED_SOURCE_SHA256, old.LOADED_SOURCE_SHA256
        self.legacy_reader._capture_files(
            files,
            (
                (str(self.forecasts.originals.source_root / path), sha)
                for path, sha in pairs.items()
            ),
            check,
        )

    def _proof_files(self, session, entry, files, check):
        qualification = verify_payload(
            HistoryModelQualificationV3.model_validate_json(entry.proof.qualification)
        )
        # Full pack verification has already occurred in the actual issuer.
        # Its content-bound qualification pins the complete hash and size; one
        # request exit hashes the unique union, including shared packs only once.
        target = str(Path(qualification.bundle_ref).resolve(strict=True))
        if not Path(target).is_relative_to(self.forecasts.writer.qualifications.root):
            raise ValueError("history_outcome_qualified_bundle_path_changed")
        expected = qualification.bundle_hash, qualification.bundle_bytes
        if target in files and not transfer.native_equal39(files[target], expected):
            raise ValueError("history_outcome_conflicting_qualified_bundle")
        files[target] = expected
        for record, _identifier, row in entry.proof.rows:
            if record not in (QualificationRow, EventRow):
                continue
            fields = dict(zip(record.__table__.columns.keys(), row, strict=True))
            self.legacy_reader._capture_files(
                files,
                ((fields["original_ref"], fields["original_hash"]),),
                check,
            )
        return qualification

    def issue_model(self, session, scope_id, *, checkpoint=None):
        """Return actual issuer-bound full model bytes; no cross-call capability."""
        check = self._check(120, checkpoint)
        boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history_outcome_forecast_physical_readonly_required")
        sources, files, entry = self._sources(), {}, None
        if scope_id not in self.forecasts.writer.qualifications.serving_grants:
            # Absence of a qualified serving grant keeps the exact historical
            # model reader. A present invalid/revoked qualification never falls
            # back to this branch.
            model_id = self.forecasts.grants.get(scope_id)
            if not model_id:
                raise ValueError("history_outcome_exact_model_server_grant_required")
            authority, proof, model = self.legacy_reader._issue_model(session, model_id)
            if (
                type(authority) is not transfer.TransferAuthority39
                or type(proof) is not transfer.ModelProof39
                or proof.issuer is not authority
                or authority.service is not self.forecasts.legacy.writer
                or model.scope_id != scope_id
                or model.model_id != model_id
                or model.owner_claim != self.forecasts.owner_claim
                or encoded(model.model_dump(mode="json")) != proof.model_bytes
            ):
                raise ValueError("history_outcome_exact_model_proof_changed")
            authority.verify(session, proof)
            self._sources(sources)
            check()
            require_snapshot(session, boundary)
            return proof.model_bytes
        cache = _QualifiedRequest(self, session, boundary, sources)
        try:
            self._source_files(files, sources, check)
            entry = self._entry(session, scope_id, check)
            cache.issue((entry,))
            cache.authenticate()
            self._proof_files(session, entry, files, check)
            self.forecasts.writer._authenticate(session, entry.proof)
            for path, expected in files.items():
                if self.legacy_reader._fingerprint(path, check) != expected:
                    raise ValueError("history39_original_bytes_changed")
            cache.authenticate()
            check()
            require_snapshot(session, boundary)
            return entry.model_bytes
        finally:
            cache.close()
            if entry is not None:
                self.forecasts.writer.discard_model_proof(entry.proof)

    def _verify_forecast(self, session, value, entry, feature_bytes, capture_cache, files, check):
        writer, qualifications = self.forecasts.writer, self.forecasts.writer.qualifications
        qualification = verify_payload(
            HistoryModelQualificationV3.model_validate_json(entry.proof.qualification)
        )
        acceptance = verify_payload(
            HistoryModelQualificationAcceptanceV3.model_validate_json(entry.proof.acceptance)
        )
        model = verify_payload(HistoryDatasetFitModelV2.model_validate_json(entry.model_bytes))
        binding, ack = (
            writer._binding(session, value.forecast_id),
            writer._ack(session, value.forecast_id),
        )
        if (
            writer._fence_path(value.scope_id).exists()
            or writer.legacy.writer._fence_path(value.scope_id).exists()
            or (
                binding.owner_claim,
                binding.forecast_hash,
                binding.request_id,
                binding.qualification_id,
                binding.qualification_hash,
                binding.acceptance_id,
                binding.acceptance_hash,
                binding.qualification_version,
                binding.bundle_hash,
            )
            != (
                value.owner_claim,
                value.payload_hash,
                value.request_id,
                qualification.qualification_id,
                qualification.payload_hash,
                acceptance.event_id,
                acceptance.payload_hash,
                acceptance.version,
                qualification.bundle_hash,
            )
            or qualifications.serving_grants.get(value.scope_id) != qualification.qualification_id
            or (ack.owner_claim, ack.forecast_hash, ack.binding_hash)
            != (value.owner_claim, value.payload_hash, binding.payload_hash)
            or ack.confirmed_at < value.recorded_at
            or acceptance.confirmation_at > value.cutoff_at
            or (
                model.scope_id,
                model.model_id,
                model.payload_hash,
                model.model_known_at,
                qualification.model_original_ref,
                qualification.model_original_hash,
            )
            != (
                value.scope_id,
                value.model_id,
                value.model_hash,
                value.model_known_at,
                value.model_original_ref,
                value.model_original_hash,
            )
        ):
            raise ValueError("history_outcome_qualified_forecast_binding_or_ACK_changed")
        native = []
        for record in (BindingRow, AckRow):
            row = native_record41(session, record, value.forecast_id)
            native.append((record, value.forecast_id, row))
            fields = dict(zip(record.__table__.columns.keys(), row, strict=True))
            self.legacy_reader._capture_files(
                files, ((fields["original_ref"], fields["original_hash"]),), check
            )
        source = json.loads(
            writer.archive.read(value.source_original_ref, value.source_original_hash)
        )
        if (
            source.get("schema_version") != "history-v2-forecast-source-original.v1"
            or tuple((item["path"], item["sha256"]) for item in source["files"])
            != value.source_files
        ):
            raise ValueError("history41_saved_forecast_source_original_changed")
        for item in source["files"]:
            check()
            if (
                hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
                != item["sha256"]
            ):
                raise ValueError("history41_saved_forecast_source_original_changed")
        scope = next(
            spot_scope(asset)
            for asset in ("BTC-USDT", "ETH-USDT")
            if spot_scope(asset).scope_id == value.scope_id
        )
        bars, feature = load_window(session, scope, value.cutoff_at, value.cutoff_at, capture_cache)
        selected = writer._selected(bars, value.cutoff_at)
        if (
            selected != value.selected_closes
            or encoded(feature) != feature_bytes
            or writer.archive.reference(feature)
            != (value.feature_original_ref, value.feature_original_hash)
            or writer.legacy.writer._numerics(model, selected)
            != (value.realized_vol_30m, value.selected_group, value.frequencies)
            or value.original_readset
            != tuple(
                sorted(
                    {
                        ("archive", value.model_original_ref, value.model_original_hash),
                        *(("raw", bar.capture_ref, bar.capture_hash) for bar in selected),
                    }
                )
            )
        ):
            raise ValueError("history41_saved_forecast_complete_inputs_or_numerics_changed")
        self.legacy_reader._capture_files(
            files,
            (
                (value.feature_original_ref, value.feature_original_hash),
                (value.source_original_ref, value.source_original_hash),
            ),
            check,
        )
        candidates = transfer.feature_candidates(session, value.scope_id, value.cutoff_at)
        return tuple(native), candidates

    def read_many(
        self, session, forecast_ids, *, budget_seconds=120.0, checkpoint=None, capture_cache=None
    ):
        """Authenticate all originals in the same snapshot, or return no result."""
        check = self._check(budget_seconds, checkpoint)
        if (
            type(forecast_ids) is not tuple
            or len(forecast_ids) > 48
            or any(type(identifier) is not str or not identifier for identifier in forecast_ids)
            or len(set(forecast_ids)) != len(forecast_ids)
        ):
            raise ValueError("history_outcome_forecast_batch_or_budget_invalid")
        own_capture_cache = capture_cache is None
        if capture_cache is not None:
            if (
                type(capture_cache) is not CaptureCacheV2
                or type(capture_cache.issuer) is not HistoryOutcomeCaptureAuthorityV2
                or capture_cache.issuer.forecasts is not self.forecasts.legacy
            ):
                raise ValueError("history_outcome_exact_capture_capability_required")
            capture_cache.issuer.authenticate(session, capture_cache)
            boundary = capture_cache.boundary
        else:
            boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history_outcome_forecast_physical_readonly_required")
        sources = self._sources()
        schema = schemas41(
            session,
            (
                *transfer.PROJECTIONS,
                ForecastRow.__tablename__,
                QualificationRow.__tablename__,
                EventRow.__tablename__,
                BindingRow.__tablename__,
                AckRow.__tablename__,
            ),
        )
        cache = _QualifiedRequest(self, session, boundary, sources)
        entries, files, saved, rows, feature_bytes = [], {}, [], [], {}
        legacy_ids, qualified, exact_models, capture_refs = [], [], {}, set()
        try:
            self._source_files(files, sources, check)
            forecast_schema = self.legacy_reader._forecast_schema(session)
            for identifier in forecast_ids:
                check()
                value = self.forecasts.legacy.writer._saved_identity(session, identifier)
                if value.source_files not in sources:
                    raise ValueError("history41_unknown_or_mixed_producer_source")
                row = self.legacy_reader._forecast_row(session, identifier)
                saved.append(value)
                rows.append((identifier, row))
                fields = dict(zip((column[1] for column in forecast_schema), row, strict=True))
                self.legacy_reader._capture_files(
                    files, ((fields["original_ref"], fields["original_hash"]),), check
                )
                body = self.forecasts.archive.read(
                    value.feature_original_ref, value.feature_original_hash
                )
                feature_bytes[identifier] = body
                capture_refs.update(bar["capture_ref"] for bar in json.loads(body)["bars"])
                if value.source_files in sources[:2]:
                    legacy_ids.append(identifier)
                else:
                    qualified.append(value)
                    exact = (
                        value.model_id,
                        value.model_hash,
                        value.model_original_ref,
                        value.model_original_hash,
                    )
                    if value.model_id in exact_models and exact_models[value.model_id] != exact:
                        raise ValueError("history_outcome_conflicting_exact_model")
                    exact_models[value.model_id] = exact
            if capture_cache is None:
                capture_cache = HistoryOutcomeCaptureAuthorityV2(self.forecasts.legacy).issue(
                    session,
                    tuple(sorted(capture_refs)),
                    checkpoint=check,
                    boundary=boundary,
                )
            elif not capture_refs <= set(capture_cache.refs):
                raise ValueError("history_outcome_capture_ref_not_in_complete_pool")
            if legacy_ids:
                legacy_values = self.legacy_reader.read_many(
                    session,
                    tuple(legacy_ids),
                    checkpoint=check,
                    capture_cache=capture_cache,
                )
                if legacy_values != tuple(
                    value for value in saved if value.forecast_id in legacy_ids
                ):
                    raise ValueError("history_outcome_legacy_saved_identity_changed")
                check()
            for model_id, exact in exact_models.items():
                value = next(value for value in qualified if value.model_id == model_id)
                entry = self._entry(session, value.scope_id, check)
                entries.append(entry)
                if entry.exact_model[:4] != exact:
                    raise ValueError("history_outcome_exact_qualified_model_changed")
                self._proof_files(session, entry, files, check)
            cache.issue(entries)
            entry_map = {entry.exact_model[0]: entry for entry in entries}
            closures = []
            for value in qualified:
                check()
                cache.authenticate()
                closures.append(
                    (
                        value,
                        *self._verify_forecast(
                            session,
                            value,
                            entry_map[value.model_id],
                            feature_bytes[value.forecast_id],
                            capture_cache,
                            files,
                            check,
                        ),
                    )
                )
            cache.authenticate()
            for entry in entries:
                self.forecasts.writer._authenticate(session, entry.proof)
                check()
            for value, native, candidates in closures:
                if not transfer.native_equal39(
                    transfer.feature_candidates(session, value.scope_id, value.cutoff_at),
                    candidates,
                ) or any(
                    not transfer.native_equal39(
                        native_record41(session, record, identifier), expected
                    )
                    for record, identifier, expected in native
                ):
                    raise ValueError("history_outcome_qualified_complete_native_readset_changed")
                check()
            for identifier, expected in rows:
                if not transfer.native_equal39(
                    self.legacy_reader._forecast_row(session, identifier), expected
                ):
                    raise ValueError("history_outcome_forecast_native_readset_changed")
                check()
            for path, expected in files.items():
                if self.legacy_reader._fingerprint(path, check) != expected:
                    raise ValueError("history39_original_bytes_changed")
            verify_schemas41(session, schema)
            cache.authenticate()
            check()
            if own_capture_cache:
                capture_cache.issuer.close(session, capture_cache)
            else:
                capture_cache.issuer.verify(session, capture_cache)
            return tuple(saved)
        finally:
            cache.close()
            for entry in entries:
                self.forecasts.writer.discard_model_proof(entry.proof)
            if own_capture_cache and capture_cache is not None:
                capture_cache.issuer.abort()


LOADED_METHODS = tuple(
    (cls, name, getattr(cls, name), getattr(cls, name).__code__)
    for cls in (_QualifiedRequest, HistoryOutcomeQualified41Authenticator)
    for name in cls.__dict__
    if callable(getattr(cls, name)) and hasattr(getattr(cls, name), "__code__")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "encoded",
        "verify_payload",
        "spot_scope",
        "snapshot_identity",
        "require_snapshot",
        "schemas41",
        "verify_schemas41",
        "native_record41",
        "load_window",
    )
)
LOADED_CLASSES = tuple(
    (name, globals()[name])
    for name in (
        "_QualifiedRequest",
        "_QualifiedEntry",
        "HistoryOutcomeQualified41Authenticator",
        "HistoryForecastDispatch41Service",
        "QualifiedModelProofV3",
        "CaptureCacheV2",
        "HistoryOutcomeCaptureAuthorityV2",
        "HistoryDatasetFitModelV2",
        "HistoryModelQualificationV3",
        "HistoryModelQualificationAcceptanceV3",
        "ForecastRow",
        "QualificationRow",
        "EventRow",
        "BindingRow",
        "AckRow",
    )
)
LOADED_TRANSFER, LOADED_OLD = transfer, old
LOADED_TRANSFER_FUNCTIONS = tuple(
    (name, getattr(transfer, name), getattr(transfer, name).__code__)
    for name in ("native_equal39", "database_identity", "feature_candidates")
)
