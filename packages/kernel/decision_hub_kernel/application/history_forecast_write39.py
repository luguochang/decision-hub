"""Same V2 originals and math with one deep RO read and a bounded fresh writer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from types import MappingProxyType

from ..persistence.db import as_utc
from ..persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Record,
)
from ..ports.history_dataset_forecast_v2 import HistoryDatasetProspectiveForecastV2
from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_observed_training import seal, verify_payload
from ..ports.market_path import canonical_hash
from . import history_forecast_transfer39 as transfer
from .history_dataset_forecast_v2 import (
    HistoryDatasetForecastCommittedReadbackError,
    HistoryDatasetForecastV2Service,
    forecast_id,
)
from .history_observed_dataset import encoded, require_snapshot, snapshot_identity

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE_FILES39 = (
    "packages/kernel/decision_hub_kernel/application/history_forecast_transfer39.py",
    "packages/kernel/decision_hub_kernel/application/history_forecast_write39.py",
    "packages/kernel/decision_hub_kernel/application/history_forecast_dispatch39.py",
    "apps/hub_api/history_product_bootstrap39.py",
    "apps/hub_worker/history_hourly39.py",
    "apps/hub_api/history_product_bootstrap.py",
    "apps/hub_api/history_product_routes.py",
    "apps/hub_api/history_owner_authority.py",
    "apps/hub_worker/history_hourly_v2.py",
)


@dataclass(frozen=True, slots=True)
class PreparedForecast39:
    value: HistoryDatasetProspectiveForecastV2
    proof: transfer.ModelProof39
    feature_bytes: bytes
    source_bytes: bytes
    candidates: tuple[tuple, ...]
    capture_candidates: tuple[tuple, ...]
    feature_rows: tuple[transfer.NativeRows39, ...]
    files: tuple[tuple[str, str, int], ...]
    issuer: object
    thread_id: int
    authentication: bytes


class HistoryForecastCommitUnconfirmed39(ValueError):
    automatic_retry = False


class HistoryForecastWrite39Service(HistoryDatasetForecastV2Service):
    def __init__(self, models, *, owner_claim, grants):
        super().__init__(models, owner_claim=owner_claim, grants=grants)
        self._bindings39 = tuple(
            (obj, name, getattr(type(obj), name), getattr(type(obj), name).__code__)
            for obj, name in (
                (models, "_derive"),
                (self.originals, "read_original"),
                (self.originals, "_confirm_rows"),
                (self.originals, "now"),
                (self.archive, "put"),
                (self.archive, "read"),
                (self.archive, "reference"),
                (self.models.archive, "read"),
                (self.originals.archive, "read"),
                (self.originals.raw, "read"),
                (self.reader.raw_reader.__self__, "read"),
                (self.commands, "_read_session"),
                (self.commands.acquisition, "_writer"),
                (self.commands.database, "session"),
            )
        )
        self._instances39 = (
            self.commands.database,
            self.commands.database.engine,
            self.commands.database.factory,
            self.commands.acquisition,
            self.originals.raw,
            self.models.archive,
            self.originals.archive,
            self.originals.clock,
            self.originals.fixture_mode,
            self.originals.data_directory,
            self.originals.source_root,
            self.archive.root,
            self.models.archive.root,
            self.originals.archive.root,
            self.originals.raw.root,
            self.reader.raw_reader.__self__.root,
        )
        self._clock_code39 = getattr(self.originals.clock, "__code__", None)
        self.last_phase_timing = MappingProxyType({})

    def _publish_phases39(self, phases, value, state):
        self.last_phase_timing = MappingProxyType(dict(phases))
        try:
            logging.getLogger("uvicorn.error").warning(
                "history_forecast_phase39 forecast_id=%s scope_id=%s "
                "request_id=%s state=%s phases=%s",
                value.forecast_id,
                value.scope_id,
                value.request_id,
                state,
                json.dumps(dict(phases), sort_keys=True, separators=(",", ":")),
            )
        except Exception:
            # Diagnostic output cannot alter an already committed outcome.
            pass

    def _sources(self, expected=None):
        old = super()._sources()
        if (
            self.commands.database,
            self.commands.database.engine,
            self.commands.database.factory,
            self.commands.acquisition,
            self.originals.raw,
            self.models.archive,
            self.originals.archive,
            self.originals.clock,
            self.originals.fixture_mode,
            self.originals.data_directory,
            self.originals.source_root,
            self.archive.root,
            self.models.archive.root,
            self.originals.archive.root,
            self.originals.raw.root,
            self.reader.raw_reader.__self__.root,
        ) != self._instances39 or self.originals.fixture_mode is not self._instances39[8]:
            raise ValueError("history39_dependency_or_clock_changed")
        if getattr(self.originals.clock, "__code__", None) is not self._clock_code39:
            raise ValueError("history39_dependency_or_clock_changed")
        if any(
            getattr(type(obj), name) is not function
            or getattr(getattr(obj, name), "__func__", getattr(obj, name)) is not function
            or function.__code__ is not code
            for obj, name, function, code in self._bindings39
        ):
            raise ValueError("history39_loaded_dependency_callable_changed")
        if any(
            getattr(HistoryForecastWrite39Service, name) is not function
            or function.__code__ is not code
            for name, function, code in LOADED_METHODS
        ):
            raise ValueError("history39_loaded_writer_callable_changed")
        if any(
            globals()[name] is not function or function.__code__ is not code
            for name, function, code in LOADED_GLOBALS
        ):
            raise ValueError("history39_loaded_writer_callable_changed")
        own = tuple(
            (name, hashlib.sha256((self.originals.source_root / name).read_bytes()).hexdigest())
            for name in SOURCE_FILES39
        )
        from apps.hub_api.history_product_bootstrap39 import LOADED_SOURCE_SHA256 as BOOTSTRAP_SHA
        from apps.hub_worker.history_hourly39 import LOADED_SOURCE_SHA256 as HOURLY_SHA

        from .history_forecast_dispatch39 import LOADED_SOURCE_SHA256 as DISPATCH_SHA

        if tuple(digest for _, digest in own[:5]) != (
            transfer.LOADED_SOURCE_SHA256,
            LOADED_SOURCE_SHA256,
            DISPATCH_SHA,
            BOOTSTRAP_SHA,
            HOURLY_SHA,
        ):
            raise ValueError("history39_loaded_source_changed")
        result = (*old, *own)
        if expected is not None and result != expected:
            raise ValueError("history39_producer_source_changed")
        return result

    def authenticate_model(self, scope_id):
        self._sources()
        if scope_id not in self.grants:
            raise ValueError("history_v2_forecast_scope_not_server_granted")
        if self._fence_path(scope_id).exists():
            raise HistoryForecastCommitUnconfirmed39(
                "history39_scope_fence_requires_reconciliation"
            )
        authority = transfer.TransferAuthority39(self)
        with self.commands._read_session() as session:
            proof, model = authority.issue(session, self.grants[scope_id])
        if model.scope_id != scope_id:
            raise ValueError("history_v2_forecast_model_not_known_at_T")
        return proof

    def _fence_path(self, scope_id):
        return (
            self.archive.root
            / "fences39"
            / (canonical_hash([self.owner_claim, scope_id]) + ".json")
        )

    def _fence39(self, value):
        path = self._fence_path(value.scope_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = encoded(
            {
                "owner_claim": value.owner_claim,
                "scope_id": value.scope_id,
                "forecast_id": value.forecast_id,
                "request_id": value.request_id,
                "model_id": value.model_id,
                "cutoff_at": value.cutoff_at.isoformat(),
                "prepared_payload_hash": value.payload_hash,
                "automatic_retry": False,
            }
        )
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise HistoryForecastCommitUnconfirmed39(
                "history39_scope_fence_requires_reconciliation"
            ) from None
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        return body

    def _release_fence39(self, value, body):
        path = self._fence_path(value.scope_id)
        if path.read_bytes() != body:
            raise ValueError("history39_writer_fence_changed")
        path.unlink()

    def reconcile(self, scope_id):
        """Explicit read-only DB reconciliation; absent rows never authorize replay."""
        self._sources()
        path = self._fence_path(scope_id)
        body = path.read_bytes()
        if len(body) > 4096:
            raise ValueError("history39_writer_fence_oversize")
        fence = json.loads(body)
        if (fence["owner_claim"], fence["scope_id"], fence["model_id"]) != (
            self.owner_claim,
            scope_id,
            self.grants.get(scope_id),
        ):
            raise ValueError("history39_writer_fence_changed")
        from .history_forecast_dispatch39 import HistoryForecastDispatch39Service

        with self.commands._read_session() as session:
            value = HistoryForecastDispatch39Service(self).read(session, fence["forecast_id"])
        if (value.request_id, value.cutoff_at.isoformat()) != (
            fence["request_id"],
            fence["cutoff_at"],
        ):
            raise ValueError("history39_writer_fence_changed")
        self._release_fence39(value, body)
        return value

    def _auth39(self, prepared):
        return hmac.digest(
            self._key,
            encoded(
                [
                    prepared.value.model_dump(mode="json"),
                    prepared.proof.manifest_hash,
                    id(prepared.proof),
                    hashlib.sha256(prepared.feature_bytes).hexdigest(),
                    hashlib.sha256(prepared.source_bytes).hexdigest(),
                    prepared.candidates,
                    prepared.capture_candidates,
                    [(group.table, group.rows) for group in prepared.feature_rows],
                    prepared.files,
                    prepared.thread_id,
                    id(self),
                ]
            ),
            "sha256",
        )

    @staticmethod
    def _capture_candidates(session, pairs):
        return tuple(
            (
                ref,
                digest,
                tuple(
                    sorted(
                        (
                            tuple(row)
                            for row in session.connection()
                            .exec_driver_sql(
                                f"SELECT {transfer.PROJECTIONS['raw_captures']} FROM raw_captures "
                                "WHERE body_ref=? AND body_hash=?",
                                (ref, digest),
                            )
                            .all()
                        ),
                        key=lambda row: row[0],
                    )
                ),
            )
            for ref, digest in sorted(set(pairs))
        )

    def prepare(self, session, *, scope_id, request_id, cutoff_at, proof):
        boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history39_fresh_readonly_preparation_required")
        if scope_id not in self.grants:
            raise ValueError("history_v2_forecast_scope_not_server_granted")
        proof.issuer.verify(session, proof)
        model = verify_payload(HistoryDatasetFitModelV2.model_validate_json(proof.model_bytes))
        cutoff, now = cutoff_at, self.originals.now()
        if cutoff.timestamp() % 60 or cutoff > now:
            raise ValueError("history_v2_forecast_internal_cutoff_invalid")
        if (
            model.model_id != self.grants[scope_id]
            or model.scope_id != scope_id
            or model.model_known_at > cutoff
        ):
            raise ValueError("history_v2_forecast_model_not_known_at_T")
        selected, feature = self._feature(session, scope_id, cutoff)
        candidates = transfer.feature_candidates(session, scope_id, cutoff)
        feature_rows = transfer.capture_rows(
            session,
            (),
            tuple(("market_candles", row[0]) for row in candidates)
            + tuple(
                ("raw_captures", row["capture_id"]) for row in feature["retained_capture_originals"]
            ),
        )
        pairs = tuple((bar["capture_ref"], bar["capture_hash"]) for bar in feature["bars"])
        captures = self._capture_candidates(session, pairs)
        source = {
            "schema_version": "history-v2-forecast-source-original.v1",
            "files": [
                {
                    "path": name,
                    "sha256": digest,
                    "bytes_base64": base64.b64encode(
                        (self.originals.source_root / name).read_bytes()
                    ).decode(),
                }
                for name, digest in proof.sources
            ],
        }
        # Large content-addressed originals are written before BEGIN IMMEDIATE.
        feature_ref, feature_hash = self.archive.put(feature)
        source_ref, source_hash = self.archive.put(source)
        files = transfer.capture_files(
            (
                *((path, digest) for path, digest, _ in proof.files),
                *(
                    (str(self.originals.raw.root / digest[:2] / digest), digest)
                    for _, digest in pairs
                ),
                (feature_ref, feature_hash),
                (source_ref, source_hash),
            )
        )
        proof.issuer.verify(session, proof)
        transfer.verify_rows(session, feature_rows)
        if not transfer.native_equal39(
            transfer.feature_candidates(session, scope_id, cutoff), candidates
        ) or not transfer.native_equal39(self._capture_candidates(session, pairs), captures):
            raise ValueError("history39_feature_selection_closure_changed")
        transfer.verify_files(files)
        authenticated = self.originals.now()
        rv, group, frequencies = self._numerics(model, selected)
        produced = self.originals.now()
        model_row = next(
            group.rows[0] for group in proof.rows if group.table == "market_history_v2_models"
        )
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
            model_original_ref=model_row[8],
            model_original_hash=model_row[9],
            cutoff_at=cutoff,
            latest_close_at=selected[-1].close_at,
            feature_known_at=max(bar.known_at for bar in selected),
            selected_closes=selected,
            feature_original_ref=feature_ref,
            feature_original_hash=feature_hash,
            original_readset=tuple(
                sorted(
                    {
                        ("archive", model_row[8], model_row[9]),
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
        candidate = PreparedForecast39(
            value,
            proof,
            encoded(feature),
            encoded(source),
            candidates,
            captures,
            feature_rows,
            files,
            self,
            threading.get_ident(),
            b"",
        )
        result = PreparedForecast39(
            value,
            proof,
            candidate.feature_bytes,
            candidate.source_bytes,
            candidates,
            captures,
            feature_rows,
            files,
            self,
            candidate.thread_id,
            self._auth39(candidate),
        )
        self._sources(proof.sources)
        require_snapshot(session, boundary)
        return result

    def _verify39(self, session, prepared):
        if (
            type(prepared) is not PreparedForecast39
            or prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not hmac.compare_digest(prepared.authentication, self._auth39(prepared))
        ):
            raise ValueError("history39_preparation_changed")
        proof = prepared.proof
        if (
            type(proof) is not transfer.ModelProof39
            or type(proof.issuer) is not transfer.TransferAuthority39
            or proof.issuer.service is not self
        ):
            raise ValueError("history39_model_proof_changed")
        proof.issuer.authenticate(session, proof)
        transfer.verify_rows(session, proof.rows)
        transfer.verify_rows(session, prepared.feature_rows)
        value = prepared.value
        pairs = tuple((ref, digest) for ref, digest, _ in prepared.capture_candidates)
        if not transfer.native_equal39(
            transfer.feature_candidates(session, value.scope_id, value.cutoff_at),
            prepared.candidates,
        ) or not transfer.native_equal39(
            self._capture_candidates(session, pairs), prepared.capture_candidates
        ):
            raise ValueError("history39_feature_selection_closure_changed")
        transfer.verify_files(prepared.files)
        transfer.verify_schemas39(session, proof.schemas)
        self._sources(value.source_files)

    def _saved_identity(self, session, identifier):
        row = session.get(Record, identifier, populate_existing=True)
        if row is None or row.owner_claim != self.owner_claim:
            raise ValueError("history_v2_forecast_missing_or_owner_conflict")
        body = self.archive.read(row.original_ref, row.original_hash)
        value = verify_payload(HistoryDatasetProspectiveForecastV2.model_validate_json(body))
        if (
            (
                row.forecast_id,
                row.owner_claim,
                row.request_id,
                row.scope_id,
                row.model_id,
                row.model_hash,
                as_utc(row.cutoff_at),
                row.payload_json.encode(),
                row.payload_hash,
                as_utc(row.produced_at),
                as_utc(row.recorded_at),
            )
            != (
                value.forecast_id,
                value.owner_claim,
                value.request_id,
                value.scope_id,
                value.model_id,
                value.model_hash,
                value.cutoff_at,
                body,
                value.payload_hash,
                value.produced_at,
                value.recorded_at,
            )
            or value.recorded_at > self.originals.now()
            or (value.fixture_mode is not self.originals.fixture_mode)
        ):
            raise ValueError("history_v2_forecast_saved_identity_changed")
        return value

    def record(self, session, prepared):
        boundary = snapshot_identity(session)
        # Authenticate before consulting first-save state; never a full model read here.
        if (
            type(prepared) is not PreparedForecast39
            or prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not hmac.compare_digest(prepared.authentication, self._auth39(prepared))
        ):
            raise ValueError("history39_preparation_changed")
        prepared.proof.issuer.authenticate(session, prepared.proof)
        value = verify_payload(prepared.value)
        existing = session.get(Record, value.forecast_id, populate_existing=True)
        if existing is not None:
            first = self._saved_identity(session, value.forecast_id)
            if (first.scope_id, first.model_id, first.cutoff_at) != (
                value.scope_id,
                value.model_id,
                value.cutoff_at,
            ):
                raise ValueError("history_v2_forecast_request_conflict")
            require_snapshot(session, boundary)
            return first
        self._verify39(session, prepared)
        value = seal(
            HistoryDatasetProspectiveForecastV2,
            **{**value.model_dump(exclude={"payload_hash"}), "recorded_at": self.originals.now()},
        )
        ref, digest = self.archive.put(value.model_dump(mode="json"))
        require_snapshot(session, boundary, before_write=True)
        session.add(
            Record(
                forecast_id=value.forecast_id,
                owner_claim=value.owner_claim,
                request_id=value.request_id,
                scope_id=value.scope_id,
                model_id=value.model_id,
                model_hash=value.model_hash,
                cutoff_at=value.cutoff_at,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=ref,
                original_hash=digest,
                produced_at=value.produced_at,
                recorded_at=value.recorded_at,
            )
        )
        session.flush()
        self._verify39(session, prepared)
        transfer.verify_files(transfer.capture_files(((ref, digest),)))
        require_snapshot(session, (*boundary[:4], boundary[4] + 1, *boundary[5:]))
        return value

    def read_with_proof(self, session, identifier, prepared, expected):
        boundary = snapshot_identity(session)
        value = self._saved_identity(session, identifier)
        if value != expected or value.source_files != prepared.value.source_files:
            raise ValueError("history39_readback_value_changed")
        # A prior immutable first-save has its own actual timestamps. Every
        # model, feature, original/readset and numerical field must still equal
        # this freshly authenticated preparation, not merely record's return.
        clocks = {"model_authenticated_at", "produced_at", "recorded_at", "payload_hash"}
        if value.model_dump(exclude=clocks) != prepared.value.model_dump(exclude=clocks):
            raise ValueError("history39_firstsave_authenticated_semantics_changed")
        self._verify39(session, prepared)
        if (
            self.archive.read(value.feature_original_ref, value.feature_original_hash)
            != prepared.feature_bytes
            or self.archive.read(value.source_original_ref, value.source_original_hash)
            != prepared.source_bytes
        ):
            raise ValueError("history39_readback_original_changed")
        self._saved_identity(session, identifier)
        require_snapshot(session, boundary)
        return value

    def read(self, session, identifier):
        # A new connection without an internal proof retains a full model.read.
        boundary = snapshot_identity(session)
        value = self._saved_identity(session, identifier)
        self._sources(value.source_files)
        source = json.loads(
            self.archive.read(value.source_original_ref, value.source_original_hash)
        )
        if tuple(
            (item["path"], item["sha256"]) for item in source["files"]
        ) != value.source_files or any(
            hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
            != item["sha256"]
            for item in source["files"]
        ):
            raise ValueError("history_v2_forecast_saved_source_changed")
        feature = json.loads(
            self.archive.read(value.feature_original_ref, value.feature_original_hash)
        )
        super()._verify_inputs(session, value, feature=feature)
        self._sources(value.source_files)
        self._saved_identity(session, identifier)
        self.archive.read(value.source_original_ref, value.source_original_hash)
        self.archive.read(value.feature_original_ref, value.feature_original_hash)
        require_snapshot(session, boundary)
        return value

    def save_prepared(self, prepared, *, check=None, timing=None):
        """One attempt only. Unknown COMMIT and known readback failure are distinct."""
        phases = {} if timing is None else dict(timing)
        check = check or (lambda: None)
        check()
        fence = self._fence39(prepared.value)
        waiting = perf_counter()
        began = committed = False
        value = prepared.value
        try:
            with self.commands.database.factory() as session:
                self.commands.acquisition._writer(session)
                started = perf_counter()
                began = True
                phases["writer_wait"] = started - waiting
                value = self.record(session, prepared)
                check()
                commit_started = perf_counter()
                try:
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
                committed = True
                finished = perf_counter()
                phases["commit"] = finished - commit_started
                phases["writer_hold"] = finished - started
        except Exception as error:
            phases["writer_attempt_elapsed"] = perf_counter() - waiting
            phases["sqlite_errorcode"] = getattr(
                getattr(error, "orig", error), "sqlite_errorcode", None
            )
            self._publish_phases39(
                phases, value, "committed_readback_unconfirmed" if committed else "writer_error"
            )
            if committed:
                failure = HistoryDatasetForecastCommittedReadbackError(
                    "history_v2_forecast_committed_readback_unconfirmed"
                )
                for field in ("forecast_id", "request_id", "model_id", "scope_id", "cutoff_at"):
                    setattr(failure, field, getattr(value, field))
                raise failure from error
            # An exception from the commit boundary is uncertain and never replayed.
            # A record/check exception is rollback-known: propagate its gate.
            if began and "commit_started" in locals() and not committed:
                unknown = HistoryForecastCommitUnconfirmed39(
                    "history39_forecast_commit_unconfirmed"
                )
                for field in ("forecast_id", "request_id", "model_id", "scope_id", "cutoff_at"):
                    setattr(unknown, field, getattr(value, field))
                raise unknown from error
            self._release_fence39(prepared.value, fence)
            raise
        readback = perf_counter()
        try:
            with self.commands._read_session() as session:
                # A concurrent old first-save belongs to its exact old reader.
                if value.source_files == prepared.value.source_files:
                    loaded = self.read_with_proof(session, value.forecast_id, prepared, value)
                else:
                    from .history_forecast_dispatch39 import HistoryForecastDispatch39Service

                    loaded = HistoryForecastDispatch39Service(self).read(session, value.forecast_id)
                check()
        except Exception as error:
            phases["readback"] = perf_counter() - readback
            self._publish_phases39(phases, value, "committed_readback_unconfirmed")
            failure = HistoryDatasetForecastCommittedReadbackError(
                "history_v2_forecast_committed_readback_unconfirmed"
            )
            for field in ("forecast_id", "request_id", "model_id", "scope_id", "cutoff_at"):
                setattr(failure, field, getattr(value, field))
            raise failure from error
        phases["readback"] = perf_counter() - readback
        phases["file_count"] = len(prepared.files)
        phases["file_bytes_per_pass"] = sum(size for _, _, size in prepared.files)
        groups = (*prepared.proof.rows, *prepared.feature_rows)
        phases["row_count_per_pass"] = sum(len(group.rows) for group in groups)
        phases["row_sql_batches_per_pass"] = sum(
            (len(group.rows) + transfer.BATCH - 1) // transfer.BATCH for group in groups
        )
        self._publish_phases39(phases, loaded, "committed_verified")
        self._release_fence39(prepared.value, fence)
        return loaded

    def producer_once(self, scope_id, request_id=None):
        started = perf_counter()
        self._sources()
        if scope_id not in self.grants:
            raise ValueError("history_v2_forecast_scope_not_server_granted")
        if self._fence_path(scope_id).exists():
            raise HistoryForecastCommitUnconfirmed39(
                "history39_scope_fence_requires_reconciliation"
            )
        if request_id is not None:
            with self.commands._read_session() as session:
                identifier = forecast_id(self.owner_claim, request_id)
                if session.get(Record, identifier) is not None:
                    from .history_forecast_dispatch39 import HistoryForecastDispatch39Service

                    first = HistoryForecastDispatch39Service(self).read(session, identifier)
                    if (first.scope_id, first.model_id) != (scope_id, self.grants[scope_id]):
                        raise ValueError("history_v2_forecast_request_conflict")
                    return first
        deep_started = perf_counter()
        proof = self.authenticate_model(scope_id)
        deep_elapsed = perf_counter() - deep_started
        # The trusted clock freezes T only after the deep RO transaction closes.
        cutoff = self.originals.now().replace(second=0, microsecond=0)
        request = (
            request_id
            or "auto_" + canonical_hash([self.grants[scope_id], scope_id, cutoff.isoformat()])[:48]
        )
        feature_started = perf_counter()
        with self.commands._read_session() as session:
            prepared = self.prepare(
                session, scope_id=scope_id, request_id=request, cutoff_at=cutoff, proof=proof
            )
        phases = {
            "deep_verify": deep_elapsed,
            "feature_transfer": perf_counter() - feature_started,
            "deep_verified_at": proof.deep_verified_at,
            "model_authenticated_at": prepared.value.model_authenticated_at.isoformat(),
            "produced_at": prepared.value.produced_at.isoformat(),
        }
        loaded = self.save_prepared(prepared, timing=phases)
        self._publish_phases39(
            {
                **self.last_phase_timing,
                "ACK": perf_counter() - started,
                "recorded_at": loaded.recorded_at.isoformat(),
            },
            loaded,
            "ACK",
        )
        return loaded


LOADED_METHODS = tuple(
    (
        name,
        getattr(HistoryForecastWrite39Service, name),
        getattr(HistoryForecastWrite39Service, name).__code__,
    )
    for name in HistoryForecastWrite39Service.__dict__
    if hasattr(getattr(HistoryForecastWrite39Service, name), "__code__")
)
LOADED_GLOBALS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "forecast_id",
        "seal",
        "verify_payload",
        "encoded",
        "snapshot_identity",
        "require_snapshot",
    )
)
