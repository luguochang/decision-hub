"""Confirmed frozen model evidence; fresh current-input proof and tiny writer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from types import MappingProxyType

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
from ..ports.history_dataset_forecast_v2 import HistoryDatasetProspectiveForecastV2
from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_model_qualification_v3 import (
    HistoryQualifiedForecastAcceptanceV3,
    HistoryQualifiedForecastBindingV3,
)
from ..ports.history_observed_training import seal, verify_payload
from ..ports.market_path import canonical_hash, spot_scope
from . import history_forecast_transfer39 as transfer
from . import history_outcome_originals_v2 as current
from .history_dataset_forecast_v2 import HistoryDatasetForecastCommittedReadbackError, forecast_id
from .history_forecast_write39 import HistoryForecastCommitUnconfirmed39
from .history_model_freeze_v3 import FrozenModelBundleV3
from .history_model_qualification_v3 import (
    HistoryModelQualificationV3Service,
    native_record41,
    schemas41,
    verify_schemas41,
)
from .history_observed_dataset import encoded, require_snapshot, snapshot_identity

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class QualifiedModelProofV3:
    qualification: bytes
    acceptance: bytes
    model_bytes: bytes
    sources: tuple
    schemas: tuple
    rows: tuple
    context: tuple
    deep_verified_at: str
    issuer: object
    thread_id: int
    authentication: bytes


@dataclass(frozen=True, slots=True)
class PreparedQualifiedForecastV3:
    value: HistoryDatasetProspectiveForecastV2
    proof: QualifiedModelProofV3
    feature_bytes: bytes
    source_bytes: bytes
    candidates: tuple
    capture_refs: tuple
    capture_candidates: tuple
    schemas: tuple
    files: tuple
    binding: HistoryQualifiedForecastBindingV3
    original_ref: str
    original_hash: str
    binding_ref: str
    binding_hash: str
    context: tuple
    issuer: object
    thread_id: int
    authentication: bytes


class HistoryQualifiedForecastV3Service:
    def __init__(self, qualifications):
        if type(qualifications) is not HistoryModelQualificationV3Service:
            raise ValueError("history41_actual_qualification_service_required")
        self.qualifications = qualifications
        self.legacy = qualifications.forecasts
        self.models, self.originals, self.commands = (
            self.legacy.models,
            self.legacy.originals,
            self.legacy.commands,
        )
        self.owner_claim, self.grants, self.archive = (
            self.legacy.owner_claim,
            self.legacy.grants,
            self.legacy.archive,
        )
        self.key = secrets.token_bytes(32)
        self._proofs, self._prepared = {}, {}
        self.last_phase_timing = MappingProxyType({})
        self._identity = (
            qualifications,
            self.legacy,
            self.models,
            self.originals,
            self.commands,
            self.owner_claim,
            self.grants,
            self.archive,
        )

    def _sources(self, expected=None):
        if (
            (
                self.qualifications,
                self.qualifications.forecasts,
                self.legacy.models,
                self.legacy.originals,
                self.legacy.commands,
                self.legacy.owner_claim,
                self.legacy.grants,
                self.legacy.archive,
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
            raise ValueError("history41_loaded_source_or_dependency_changed")
        return self.qualifications._sources(expected)

    def _context(self, session):
        return (
            transfer.database_identity(session),
            id(self),
            id(self.qualifications),
            self.owner_claim,
            tuple(sorted(self.grants.items())),
            tuple(sorted(self.qualifications.serving_grants.items())),
            str(self.originals.data_directory),
            str(self.originals.source_root),
            self.originals.fixture_mode,
        )

    def _fence_path(self, scope):
        return (
            self.qualifications.root
            / "forecast-fences"
            / (canonical_hash([self.owner_claim, scope]) + ".json")
        )

    def _fence(self, value):
        if self.legacy.writer._fence_path(value.scope_id).exists():
            raise HistoryForecastCommitUnconfirmed39(
                "history39_scope_fence_requires_reconciliation"
            )
        path = self._fence_path(value.scope_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = encoded(
            dict(
                owner_claim=self.owner_claim,
                forecast_id=value.forecast_id,
                scope_id=value.scope_id,
                model_id=value.model_id,
                request_id=value.request_id,
                cutoff_at=value.cutoff_at.isoformat(),
                automatic_retry=False,
            )
        )
        try:
            with path.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise HistoryForecastCommitUnconfirmed39(
                "history41_scope_fence_requires_readonly_reconciliation"
            ) from None
        return body

    def _release_fence(self, value, body):
        path = self._fence_path(value.scope_id)
        if path.read_bytes() != body:
            raise ValueError("history41_scope_fence_changed")
        path.unlink()

    @staticmethod
    def _proof_leaves(proof):
        return (
            proof.qualification,
            proof.acceptance,
            proof.model_bytes,
            proof.sources,
            proof.schemas,
            proof.rows,
            proof.context,
            proof.deep_verified_at,
            id(proof.issuer),
            proof.thread_id,
        )

    @staticmethod
    def _prepared_leaves(prepared):
        return (
            encoded(prepared.value.model_dump(mode="json")),
            id(prepared.proof),
            prepared.feature_bytes,
            prepared.source_bytes,
            prepared.candidates,
            prepared.capture_refs,
            prepared.capture_candidates,
            prepared.schemas,
            prepared.files,
            encoded(prepared.binding.model_dump(mode="json")),
            prepared.original_ref,
            prepared.original_hash,
            prepared.binding_ref,
            prepared.binding_hash,
            prepared.context,
            id(prepared.issuer),
            prepared.thread_id,
        )

    def _authenticate(self, session, proof):
        issued = self._proofs.get(id(proof))
        if (
            type(proof) is not QualifiedModelProofV3
            or issued is None
            or issued[0] is not proof
            or proof.issuer is not self
            or proof.thread_id != threading.get_ident()
            or not transfer.native_equal39(self._proof_leaves(proof), issued[1])
            or not transfer.native_equal39(proof.context, self._context(session))
            or not hmac.compare_digest(
                proof.authentication, hmac.digest(self.key, repr(issued[1]).encode(), "sha256")
            )
        ):
            raise ValueError("history41_model_qualification_proof_changed")
        self._sources(proof.sources)
        verify_schemas41(session, proof.schemas)
        for record, identifier, expected in proof.rows:
            if not transfer.native_equal39(native_record41(session, record, identifier), expected):
                raise ValueError("history41_qualification_version_CAS_changed")
        from ..ports.history_model_qualification_v3 import (
            HistoryModelQualificationAcceptanceV3,
            HistoryModelQualificationV3,
        )

        qualification = verify_payload(
            HistoryModelQualificationV3.model_validate_json(proof.qualification)
        )
        acceptance = verify_payload(
            HistoryModelQualificationAcceptanceV3.model_validate_json(proof.acceptance)
        )
        if (
            self.qualifications.serving_grants.get(qualification.scope_id)
            != qualification.qualification_id
            or self.grants.get(qualification.scope_id) != qualification.model_id
            or self.qualifications._event(session, qualification.qualification_id) != acceptance
            or acceptance.state != "confirmed"
            or self.qualifications._fence_path(qualification.qualification_id).exists()
        ):
            raise ValueError("history41_qualification_not_currently_confirmed_or_granted")
        model = verify_payload(HistoryDatasetFitModelV2.model_validate_json(proof.model_bytes))
        return qualification, acceptance, model

    def authenticate_model(self, scope_id, *, checkpoint=None):
        with self.commands._read_session() as session:
            return self.issue_model(session, scope_id, checkpoint=checkpoint)

    def issue_model(self, session, scope_id, *, checkpoint=None):
        check = checkpoint or (lambda: None)
        check()
        self._sources()
        identifier = self.qualifications.serving_grants.get(scope_id)
        if not identifier or scope_id not in self.grants:
            raise ValueError("history41_confirmed_qualification_not_server_granted")
        if self._fence_path(scope_id).exists() or self.legacy.writer._fence_path(scope_id).exists():
            raise HistoryForecastCommitUnconfirmed39(
                "history41_scope_fence_requires_readonly_reconciliation"
            )
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history41_model_proof_physical_readonly_required")
        qualification, acceptance, bundle, model = self.qualifications.read(
            session, identifier, checkpoint=check
        )
        if (
            qualification.scope_id != scope_id
            or qualification.model_id != self.grants[scope_id]
            or acceptance.confirmation_at > self.originals.now()
        ):
            raise ValueError("history41_exact_qualification_model_or_clock_invalid")
        schema = schemas41(
            session,
            (*transfer.PROJECTIONS, QualificationRow.__tablename__, EventRow.__tablename__),
        )
        rows = tuple(
            (record, identity, native_record41(session, record, identity))
            for record, identity in (
                (QualificationRow, identifier),
                (EventRow, acceptance.event_id),
                (ModelRow, model.model_id),
            )
        )
        proof = QualifiedModelProofV3(
            encoded(qualification.model_dump(mode="json")),
            encoded(acceptance.model_dump(mode="json")),
            encoded(model.model_dump(mode="json")),
            self._sources(),
            schema,
            rows,
            self._context(session),
            qualification.verified_at.isoformat(),
            self,
            threading.get_ident(),
            b"",
        )
        leaves = self._proof_leaves(proof)
        # Replace class objects in native leaf authentication with fixed table names.
        # They remain separately pinned by exact record class identity below.
        signature = hmac.digest(self.key, repr(leaves).encode(), "sha256")
        proof = replace(proof, authentication=signature)
        self._proofs[id(proof)] = (proof, leaves)
        self._authenticate(session, proof)
        bundle.verify()
        check()
        return proof

    def discard_model_proof(self, proof):
        if type(proof) is not QualifiedModelProofV3 or proof.issuer is not self:
            raise ValueError("history41_actual_issued_model_proof_required")
        self._proofs.pop(id(proof), None)

    @staticmethod
    def _selected(bars, cutoff):
        closed = [bar for bar in bars if bar.close_at <= cutoff]
        latest = closed[-1].close_at if closed else None
        if latest is None or (cutoff - latest).total_seconds() > 180:
            raise ValueError("history_v2_forecast_recent_31_closes_required")
        by_close = {bar.close_at: bar for bar in closed}
        times = tuple(latest - timedelta(minutes=index) for index in range(30, -1, -1))
        if any(stamp not in by_close for stamp in times):
            raise ValueError("history_v2_forecast_recent_31_closes_required")
        return tuple(by_close[stamp] for stamp in times)

    def _feature(self, session, scope_id, cutoff, *, check):
        candidates = transfer.feature_candidates(session, scope_id, cutoff)
        ref_column = transfer.PROJECTIONS["market_candles"].split(",").index("capture_ref")
        refs = tuple(sorted({row[ref_column] for row in candidates}))
        authority = current.HistoryOutcomeCaptureAuthorityV2(self.legacy)
        cache = authority.issue(session, refs, checkpoint=check)
        try:
            scope = next(
                spot_scope(asset)
                for asset in ("BTC-USDT", "ETH-USDT")
                if spot_scope(asset).scope_id == scope_id
            )
            bars, feature = current.load_window(session, scope, cutoff, cutoff, cache)
            selected = self._selected(bars, cutoff)
            captures = cache.captures
            files = tuple((path, digest, size) for path, (digest, size) in authority._files.items())
            authority.close(session, cache)
        except BaseException:
            authority.abort()
            raise
        if not transfer.native_equal39(
            transfer.feature_candidates(session, scope_id, cutoff), candidates
        ):
            raise ValueError("history41_current_feature_selection_changed")
        return selected, feature, candidates, refs, captures, files

    def prepare(self, session, *, scope_id, request_id, cutoff_at, proof, checkpoint=None):
        check = checkpoint or (lambda: None)
        check()
        boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history41_physical_readonly_preparation_required")
        qualification, acceptance, model = self._authenticate(session, proof)
        cutoff = cutoff_at
        if (
            cutoff.timestamp() % 60
            or cutoff > self.originals.now()
            or qualification.scope_id != scope_id
            or model.model_known_at > cutoff
            or acceptance.confirmation_at > cutoff
        ):
            raise ValueError("history41_internal_T_or_qualification_clock_invalid")
        bundle = FrozenModelBundleV3(
            self.qualifications.root,
            qualification.bundle_ref,
            qualification.bundle_hash,
            qualification.manifest_hash,
            checkpoint=check,
        )
        if bundle.model(model.model_id) != model:
            raise ValueError("history41_authenticated_model_payload_changed")
        selected, feature, candidates, refs, captures, files = self._feature(
            session, scope_id, cutoff, check=check
        )
        source = dict(
            schema_version="history-v2-forecast-source-original.v1",
            files=[
                dict(
                    path=path,
                    sha256=digest,
                    bytes_base64=base64.b64encode(
                        (self.originals.source_root / path).read_bytes()
                    ).decode(),
                )
                for path, digest in proof.sources
            ],
        )
        feature_ref, feature_hash = self.archive.put(feature)
        source_ref, source_hash = self.archive.put(source)
        authenticated = self.originals.now()
        rv, group, frequencies = self.legacy.writer._numerics(model, selected)
        produced = self.originals.now()
        value = seal(
            HistoryDatasetProspectiveForecastV2,
            forecast_id=forecast_id(self.owner_claim, request_id),
            owner_claim=self.owner_claim,
            request_id=request_id,
            scope_id=scope_id,
            model_id=model.model_id,
            model_hash=model.payload_hash,
            model_known_at=model.model_known_at,
            model_authenticated_at=authenticated,
            model_original_ref=qualification.model_original_ref,
            model_original_hash=qualification.model_original_hash,
            cutoff_at=cutoff,
            latest_close_at=selected[-1].close_at,
            feature_known_at=max(bar.known_at for bar in selected),
            selected_closes=selected,
            feature_original_ref=feature_ref,
            feature_original_hash=feature_hash,
            original_readset=tuple(
                sorted(
                    {
                        (
                            "archive",
                            qualification.model_original_ref,
                            qualification.model_original_hash,
                        ),
                        *(("raw", bar.capture_ref, bar.capture_hash) for bar in selected),
                    }
                )
            ),
            realized_vol_30m=rv,
            selected_group=group,
            frequencies=frequencies,
            source_files=proof.sources,
            source_original_ref=source_ref,
            source_original_hash=source_hash,
            produced_at=produced,
            recorded_at=produced,
            fixture_mode=self.originals.fixture_mode,
        )
        original_ref, original_hash = self.archive.put(value.model_dump(mode="json"))
        binding = seal(
            HistoryQualifiedForecastBindingV3,
            forecast_id=value.forecast_id,
            forecast_hash=value.payload_hash,
            owner_claim=self.owner_claim,
            request_id=request_id,
            qualification_id=qualification.qualification_id,
            qualification_hash=qualification.payload_hash,
            acceptance_id=acceptance.event_id,
            acceptance_hash=acceptance.payload_hash,
            qualification_version=acceptance.version,
            bundle_hash=qualification.bundle_hash,
            recorded_at=produced,
        )
        binding_ref, binding_hash = self.qualifications.archive.put(binding.model_dump(mode="json"))
        files = transfer.capture_files(
            (
                *((path, digest) for path, digest, _size in files),
                (feature_ref, feature_hash),
                (source_ref, source_hash),
                (original_ref, original_hash),
                (binding_ref, binding_hash),
            )
        )
        schemas = schemas41(
            session,
            (
                "market_candles",
                "raw_captures",
                QualificationRow.__tablename__,
                EventRow.__tablename__,
                ModelRow.__tablename__,
                BindingRow.__tablename__,
                AckRow.__tablename__,
                ForecastRow.__tablename__,
            ),
        )
        self._authenticate(session, proof)
        verify_schemas41(session, schemas)
        bundle.verify()
        transfer.verify_files(files)
        require_snapshot(session, boundary)
        prepared = PreparedQualifiedForecastV3(
            value,
            proof,
            encoded(feature),
            encoded(source),
            candidates,
            refs,
            captures,
            schemas,
            files,
            binding,
            original_ref,
            original_hash,
            binding_ref,
            binding_hash,
            self._context(session),
            self,
            threading.get_ident(),
            b"",
        )
        leaves = self._prepared_leaves(prepared)
        prepared = replace(
            prepared, authentication=hmac.digest(self.key, repr(leaves).encode(), "sha256")
        )
        self._prepared[id(prepared)] = (prepared, leaves)
        check()
        return prepared

    def _verify_small(self, session, prepared):
        issued = self._prepared.get(id(prepared))
        if (
            type(prepared) is not PreparedQualifiedForecastV3
            or issued is None
            or issued[0] is not prepared
            or prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not transfer.native_equal39(self._prepared_leaves(prepared), issued[1])
            or not transfer.native_equal39(prepared.context, self._context(session))
            or not hmac.compare_digest(
                prepared.authentication, hmac.digest(self.key, repr(issued[1]).encode(), "sha256")
            )
        ):
            raise ValueError("history41_preparation_immutable_proof_changed")
        self._authenticate(session, prepared.proof)
        verify_schemas41(session, prepared.schemas)
        if not transfer.native_equal39(
            transfer.feature_candidates(session, prepared.value.scope_id, prepared.value.cutoff_at),
            prepared.candidates,
        ) or not transfer.native_equal39(
            current._capture_pool(session, prepared.capture_refs, lambda: None),
            prepared.capture_candidates,
        ):
            raise ValueError("history41_current_input_native_CAS_changed")
        # Historical corpus and all large whole-byte SHA passes are deliberately
        # outside this transaction. Qualification and current-input CAS replace them.

    def record(self, session, prepared):
        boundary = snapshot_identity(session)
        self._verify_small(session, prepared)
        value = verify_payload(prepared.value)
        existing = session.get(ForecastRow, value.forecast_id, populate_existing=True)
        if existing is not None:
            raise ValueError("history41_firstsave_exists_read_without_rewrite")
        session.add(
            ForecastRow(
                forecast_id=value.forecast_id,
                owner_claim=self.owner_claim,
                request_id=value.request_id,
                scope_id=value.scope_id,
                model_id=value.model_id,
                model_hash=value.model_hash,
                cutoff_at=value.cutoff_at,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=prepared.original_ref,
                original_hash=prepared.original_hash,
                produced_at=value.produced_at,
                recorded_at=value.recorded_at,
            )
        )
        binding = prepared.binding
        session.add(
            BindingRow(
                **{
                    name: getattr(binding, name)
                    for name in BindingRow.__table__.columns.keys()
                    if name not in ("payload_json", "payload_hash", "original_ref", "original_hash")
                },
                payload_json=encoded(binding.model_dump(mode="json")).decode(),
                payload_hash=binding.payload_hash,
                original_ref=prepared.binding_ref,
                original_hash=prepared.binding_hash,
            )
        )
        session.flush()
        self._verify_small(session, prepared)
        require_snapshot(session, (*boundary[:4], boundary[4] + 2, *boundary[5:]))
        return value

    def _binding(self, session, identifier):
        return self.qualifications._identity_read(
            session, BindingRow, HistoryQualifiedForecastBindingV3, identifier, "forecast_id"
        )

    def _ack(self, session, identifier):
        return self.qualifications._identity_read(
            session, AckRow, HistoryQualifiedForecastAcceptanceV3, identifier, "forecast_id"
        )

    def _read(self, session, identifier, *, allow_unconfirmed=False, expected=None, check=None):
        check = check or (lambda: None)
        boundary = snapshot_identity(session)
        schema = schemas41(
            session,
            (
                ForecastRow.__tablename__,
                BindingRow.__tablename__,
                AckRow.__tablename__,
                QualificationRow.__tablename__,
                EventRow.__tablename__,
                ModelRow.__tablename__,
                "raw_captures",
                "market_candles",
            ),
        )
        value = self.legacy.writer._saved_identity(session, identifier)
        self._sources(value.source_files)
        binding = self._binding(session, identifier)
        if (
            binding.forecast_hash != value.payload_hash
            or binding.request_id != value.request_id
            or self.qualifications.serving_grants.get(value.scope_id) != binding.qualification_id
        ):
            raise ValueError("history41_saved_forecast_qualification_binding_changed")
        if not allow_unconfirmed:
            if self._fence_path(value.scope_id).exists():
                raise HistoryForecastCommitUnconfirmed39(
                    "history41_scope_fence_requires_readonly_reconciliation"
                )
            ack = self._ack(session, identifier)
            if (
                ack.forecast_hash != value.payload_hash
                or ack.binding_hash != binding.payload_hash
                or ack.confirmed_at < value.recorded_at
            ):
                raise ValueError("history41_forecast_saved_ACK_changed")
        qualification, event, bundle, model = self.qualifications._read(
            session, binding.qualification_id, checkpoint=check, event_id=binding.acceptance_id
        )
        if (
            qualification.payload_hash,
            event.payload_hash,
            event.version,
            qualification.bundle_hash,
        ) != (
            binding.qualification_hash,
            binding.acceptance_hash,
            binding.qualification_version,
            binding.bundle_hash,
        ) or (
            model.scope_id,
            model.model_id,
            model.payload_hash,
            model.model_known_at,
            qualification.model_original_ref,
            qualification.model_original_hash,
        ) != (
            value.scope_id,
            value.model_id,
            value.model_hash,
            value.model_known_at,
            value.model_original_ref,
            value.model_original_hash,
        ):
            raise ValueError("history41_saved_qualification_exact_model_changed")
        source_body = self.archive.read(value.source_original_ref, value.source_original_hash)
        source = json.loads(source_body)
        if tuple(
            (item["path"], item["sha256"]) for item in source["files"]
        ) != value.source_files or any(
            hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
            != item["sha256"]
            for item in source["files"]
        ):
            raise ValueError("history41_saved_forecast_source_original_changed")
        selected, feature, _candidates, _refs, _captures, _files = self._feature(
            session, value.scope_id, value.cutoff_at, check=check
        )
        if (
            selected != value.selected_closes
            or self.archive.read(value.feature_original_ref, value.feature_original_hash)
            != encoded(feature)
            or self.archive.reference(feature)
            != (value.feature_original_ref, value.feature_original_hash)
            or self.legacy.writer._numerics(model, selected)
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
            or (expected is not None and value != expected)
        ):
            raise ValueError("history41_saved_forecast_complete_inputs_or_numerics_changed")
        if (
            self._binding(session, identifier) != binding
            or self.legacy.writer._saved_identity(session, identifier) != value
        ):
            raise ValueError("history41_saved_forecast_readback_changed_at_exit")
        bundle.verify()
        self._sources(value.source_files)
        verify_schemas41(session, schema)
        require_snapshot(session, boundary)
        check()
        return value, binding

    def read(self, session, identifier):
        return self._read(session, identifier)[0]

    def _publish_phases39(self, phases, value, state):
        self.last_phase_timing = MappingProxyType(dict(phases))
        try:
            logging.getLogger("uvicorn.error").warning(
                "history_forecast_phase41 forecast_id=%s scope_id=%s "
                "request_id=%s state=%s phases=%s",
                value.forecast_id,
                value.scope_id,
                value.request_id,
                state,
                json.dumps(dict(phases), sort_keys=True, separators=(",", ":")),
            )
        except Exception:
            pass

    def save_prepared(self, prepared, *, check=None, timing=None):
        check = check or (lambda: None)
        check()
        value, phases = prepared.value, {} if timing is None else dict(timing)
        fence = self._fence(value)
        waiting = perf_counter()
        began = commit_started = committed = False
        try:
            with self.commands.database.factory() as session:
                self.commands.acquisition._writer(session)
                started, began = perf_counter(), True
                phases["writer_wait"] = started - waiting
                self.record(session, prepared)
                check()
                committing, commit_started = perf_counter(), True
                session.commit()
                committed = True
                phases["commit"] = perf_counter() - committing
                phases["writer_hold"] = perf_counter() - started
        except Exception as error:
            phases["writer_attempt_elapsed"] = perf_counter() - waiting
            self._publish_phases39(
                phases, value, "committed_readback_unconfirmed" if committed else "writer_error"
            )
            if committed:
                failure = HistoryDatasetForecastCommittedReadbackError(
                    "history41_committed_readback_unconfirmed"
                )
            elif began and commit_started:
                failure = HistoryForecastCommitUnconfirmed39(
                    "history41_forecast_commit_unconfirmed"
                )
            else:
                self._release_fence(value, fence)
                raise
            for name in ("forecast_id", "request_id", "scope_id", "model_id", "cutoff_at"):
                setattr(failure, name, getattr(value, name))
            raise failure from error
        readback = perf_counter()
        try:
            with self.commands._read_session() as session:
                loaded, binding = self._read(
                    session, value.forecast_id, allow_unconfirmed=True, expected=value, check=check
                )
                transfer.verify_files(prepared.files)
                self._verify_small(session, prepared)
            confirmed = self.originals.now()
            ack = seal(
                HistoryQualifiedForecastAcceptanceV3,
                forecast_id=value.forecast_id,
                forecast_hash=value.payload_hash,
                binding_hash=binding.payload_hash,
                owner_claim=self.owner_claim,
                confirmed_at=confirmed,
                recorded_at=self.originals.now(),
            )
            ack_ref, ack_hash = self.qualifications.archive.put(ack.model_dump(mode="json"))

            def accept(session):
                self._verify_small(session, prepared)
                if (
                    self.legacy.writer._saved_identity(session, value.forecast_id) != value
                    or self._binding(session, value.forecast_id) != binding
                ):
                    raise ValueError("history41_forecast_ACK_CAS_changed")
                if session.get(AckRow, value.forecast_id) is not None:
                    raise ValueError("history41_forecast_ACK_firstsave_exists")
                session.add(
                    AckRow(
                        **{
                            name: getattr(ack, name)
                            for name in AckRow.__table__.columns.keys()
                            if name
                            not in ("payload_json", "payload_hash", "original_ref", "original_hash")
                        },
                        payload_json=encoded(ack.model_dump(mode="json")).decode(),
                        payload_hash=ack.payload_hash,
                        original_ref=ack_ref,
                        original_hash=ack_hash,
                    )
                )
                session.flush()
                self._verify_small(session, prepared)

            self.qualifications._attempt(accept)
            with self.commands._read_session() as session:
                if self._ack(session, value.forecast_id) != ack:
                    raise ValueError("history41_forecast_ACK_independent_readback_changed")
                # Final complete bytes/native pass is outside both writer transactions.
                self._read(
                    session, value.forecast_id, allow_unconfirmed=True, expected=value, check=check
                )
                transfer.verify_files(prepared.files)
                self._verify_small(session, prepared)
        except Exception as error:
            phases["readback"] = perf_counter() - readback
            self._publish_phases39(phases, value, "committed_readback_unconfirmed")
            failure = HistoryDatasetForecastCommittedReadbackError(
                "history41_committed_readback_unconfirmed"
            )
            for name in ("forecast_id", "request_id", "scope_id", "model_id", "cutoff_at"):
                setattr(failure, name, getattr(value, name))
            raise failure from error
        phases["readback"] = perf_counter() - readback
        self._release_fence(value, fence)
        self._prepared.pop(id(prepared), None)
        self._proofs.pop(id(prepared.proof), None)
        self._publish_phases39(phases, loaded, "committed_verified")
        return loaded

    def producer_once(self, scope_id, request_id=None):
        started = perf_counter()

        def check():
            if perf_counter() - started >= 90.0:
                raise ValueError("history41_forecast_total_budget_reached")

        check()
        if request_id is not None:
            with self.commands._read_session() as session:
                identifier = forecast_id(self.owner_claim, request_id)
                if session.get(ForecastRow, identifier) is not None:
                    from .history_forecast_dispatch41 import HistoryForecastDispatch41Service

                    first = HistoryForecastDispatch41Service(self).read(session, identifier)
                    if (first.scope_id, first.model_id) != (scope_id, self.grants.get(scope_id)):
                        raise ValueError("history41_forecast_firstsave_request_conflict")
                    return first
        authentication = perf_counter()
        proof = self.authenticate_model(scope_id, checkpoint=check)
        qualified_seconds = perf_counter() - authentication
        cutoff = self.originals.now().replace(second=0, microsecond=0)
        request = (
            request_id
            or "auto_" + canonical_hash([self.grants[scope_id], scope_id, cutoff.isoformat()])[:48]
        )
        preparing = perf_counter()
        with self.commands._read_session() as session:
            prepared = self.prepare(
                session,
                scope_id=scope_id,
                request_id=request,
                cutoff_at=cutoff,
                proof=proof,
                checkpoint=check,
            )
        loaded = self.save_prepared(
            prepared,
            check=check,
            timing=dict(
                qualification_read=qualified_seconds,
                deep_verify=0.0,
                qualification_verified_at=proof.deep_verified_at,
                feature_transfer=perf_counter() - preparing,
                model_authenticated_at=prepared.value.model_authenticated_at.isoformat(),
                produced_at=prepared.value.produced_at.isoformat(),
            ),
        )
        self._publish_phases39(
            {**self.last_phase_timing, "ACK": perf_counter() - started}, loaded, "ACK"
        )
        return loaded


LOADED_METHODS = tuple(
    (cls, name, getattr(cls, name), getattr(cls, name).__code__)
    for cls in (HistoryQualifiedForecastV3Service, FrozenModelBundleV3)
    for name in cls.__dict__
    if callable(getattr(cls, name)) and hasattr(getattr(cls, name), "__code__")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "schemas41",
        "verify_schemas41",
        "native_record41",
        "encoded",
        "snapshot_identity",
        "require_snapshot",
        "seal",
        "verify_payload",
        "forecast_id",
    )
)
