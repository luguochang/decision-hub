"""Complete saved-forecast authentication in one caller-owned RO snapshot.

The model capabilities are private to one invocation. They neither survive the
request nor authorize a write, and retained source originals are never executed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from sqlalchemy.orm import Session

from ..ports.history_dataset_forecast_v2 import HistoryDatasetProspectiveForecastV2
from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_observed_training import verify_payload
from ..ports.market_path import spot_scope
from . import history_forecast_transfer39 as transfer
from .history_forecast_dispatch39 import HistoryForecastDispatch39Service
from .history_observed_dataset import encoded, require_snapshot, snapshot_identity
from .history_outcome_originals_v2 import (
    CaptureCacheV2,
    HistoryOutcomeCaptureAuthorityV2,
    load_window,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE_FILE = "packages/kernel/decision_hub_kernel/application/history_outcome_forecasts_v2.py"
FORECAST_TABLE = "market_history_v2_forecasts"
FORECAST_COLUMNS = (
    "forecast_id",
    "owner_claim",
    "request_id",
    "scope_id",
    "model_id",
    "model_hash",
    "cutoff_at",
    "payload_json",
    "payload_hash",
    "original_ref",
    "original_hash",
    "produced_at",
    "recorded_at",
)


class HistoryOutcomeForecastBudgetV2Error(ValueError):
    automatic_retry = False


@dataclass(frozen=True, slots=True)
class _ModelEntry:
    exact_model: tuple[str, str, str, str]
    model_bytes: bytes
    authority: transfer.TransferAuthority39
    proof: transfer.ModelProof39


class _RequestModels:
    """One issuer, one physical snapshot, immutable leaves, no persistent cache."""

    def __init__(self, authenticator, session, boundary, sources):
        self.authenticator = authenticator
        self.session, self.boundary, self.sources = session, boundary, sources
        self.thread_id = threading.get_ident()
        self.key = secrets.token_bytes(32)
        self.context = self._context()
        self.entries = ()
        self.leaves = ()
        self.authentication = b""
        self.active = True

    def _context(self):
        forecasts = self.authenticator.forecasts
        return (
            id(self),
            id(self.authenticator),
            id(forecasts),
            *(id(value) for value in self.boundary[:4]),
            *self.boundary[4:],
            transfer.database_identity(self.session),
            forecasts.owner_claim,
            tuple(sorted(forecasts.grants.items())),
            str(forecasts.originals.data_directory),
            str(forecasts.originals.source_root),
            forecasts.originals.fixture_mode,
            self.sources,
        )

    @staticmethod
    def _leaf(entry):
        return (
            entry.exact_model,
            entry.model_bytes,
            id(entry.authority),
            id(entry.proof),
            transfer.TransferAuthority39.members(entry.proof),
            entry.proof.authentication,
        )

    def _mac(self):
        # TransferAuthority already authenticated every full native row. Bind its
        # digest and frozen leaf snapshot without another 100k-row JSON encoding.
        return hmac.digest(
            self.key,
            encoded(
                [
                    self.context,
                    self.thread_id,
                    [
                        (
                            entry.exact_model,
                            hashlib.sha256(entry.model_bytes).hexdigest(),
                            id(entry.authority),
                            id(entry.proof),
                            entry.proof.manifest_hash,
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
            raise ValueError("history_outcome_forecast_request_cache_reissued")
        self.entries = tuple(entries)
        self.leaves = tuple(self._leaf(entry) for entry in self.entries)
        self.authentication = self._mac()

    def authenticate(self):
        if (
            not self.active
            or self.thread_id != threading.get_ident()
            or not transfer.native_equal39(self.context, self._context())
            or not transfer.native_equal39(
                self.leaves, tuple(self._leaf(entry) for entry in self.entries)
            )
            or not hmac.compare_digest(self.authentication, self._mac())
        ):
            raise ValueError("history_outcome_forecast_request_cache_changed")
        require_snapshot(self.session, self.boundary, before_write=True)

    def close(self):
        self.active = False
        self.key = b""
        self.entries = self.leaves = ()


class HistoryOutcomeForecastsV2Authenticator:
    def __init__(self, forecasts: HistoryForecastDispatch39Service):
        if type(forecasts) is not HistoryForecastDispatch39Service:
            raise ValueError("history_outcome_exact_forecast_dispatch_required")
        self.forecasts = forecasts
        self._instances = (
            forecasts,
            forecasts.writer,
            forecasts.legacy,
            forecasts.models,
            forecasts.originals,
            forecasts.archive,
        )
        self._grants = tuple(sorted(forecasts.grants.items()))
        self._owner = forecasts.owner_claim

    def _sources(self, expected=None):
        forecasts = self.forecasts
        if (
            (
                forecasts,
                forecasts.writer,
                forecasts.legacy,
                forecasts.models,
                forecasts.originals,
                forecasts.archive,
            )
            != self._instances
            or tuple(sorted(forecasts.grants.items())) != self._grants
            or forecasts.owner_claim != self._owner
        ):
            raise ValueError("history_outcome_forecast_dependency_changed")
        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or hashlib.sha256(
                (forecasts.originals.source_root / SOURCE_FILE).read_bytes()
            ).hexdigest()
            != LOADED_SOURCE_SHA256
            or any(
                getattr(cls, name) is not function or function.__code__ is not code
                for cls, name, function, code in LOADED_METHODS
            )
            or any(
                globals()[name] is not function or function.__code__ is not code
                for name, function, code in LOADED_FUNCTIONS
            )
            or any(globals()[name] is not value for name, value in LOADED_CLASSES)
            or transfer is not LOADED_TRANSFER
            or any(
                getattr(transfer, name) is not function or function.__code__ is not code
                for name, function, code in LOADED_TRANSFER_FUNCTIONS
            )
        ):
            raise ValueError("history_outcome_forecast_loaded_source_or_callable_changed")
        result = (forecasts.legacy._sources(), forecasts._sources())
        if expected is not None and not transfer.native_equal39(result, expected):
            raise ValueError("history_outcome_forecast_source_changed")
        return result

    @staticmethod
    def _forecast_schema(session):
        schema = tuple(
            tuple(row)
            for row in session.connection()
            .exec_driver_sql(f"PRAGMA table_xinfo({FORECAST_TABLE})")
            .all()
        )
        if (
            len(schema) != len(FORECAST_COLUMNS)
            or set(row[1] for row in schema) != set(FORECAST_COLUMNS)
            or any(row[6] != 0 for row in schema)
        ):
            raise ValueError("history_outcome_complete_forecast_schema_changed")
        return schema

    @staticmethod
    def _forecast_row(session, identifier):
        rows = (
            session.connection()
            .exec_driver_sql(f"SELECT * FROM {FORECAST_TABLE} WHERE forecast_id=?", (identifier,))
            .all()
        )
        if len(rows) != 1:
            raise ValueError("history_v2_forecast_missing_or_owner_conflict")
        return tuple(rows[0])

    def _issue_model(self, session, identifier):
        authority = transfer.TransferAuthority39(self.forecasts.writer)
        proof, model = authority.issue(session, identifier)
        return authority, proof, model

    @staticmethod
    def _fingerprint(path, check):
        digest, size = hashlib.sha256(), 0
        with Path(path).open("rb") as handle:
            while block := handle.read(1024 * 1024):
                check()
                digest.update(block)
                size += len(block)
        check()
        return digest.hexdigest(), size

    def _capture_files(self, files, pairs, check):
        for path, digest in pairs:
            check()
            path = str(Path(path).resolve(strict=True))
            if path in files:
                if files[path][0] != digest:
                    raise ValueError("history39_conflicting_original_readset")
                continue
            actual, size = self._fingerprint(path, check)
            if actual != digest:
                raise ValueError("history39_original_bytes_changed")
            files[path] = digest, size

    @staticmethod
    def _adopt_model_files(files, proof, check):
        # This exact issuer has just hashed every full original in this request.
        # Retain its authenticated sha/size leaves; do not hash the same large
        # readset a third time. The request exit checks the complete union.
        for path, digest, size in proof.files:
            check()
            expected = digest, size
            if path in files and not transfer.native_equal39(files[path], expected):
                raise ValueError("history39_conflicting_original_readset")
            files[path] = expected

    @staticmethod
    def _merge_readset(readset, groups, check):
        for group in groups:
            expected = readset.setdefault(group.table, {})
            for index, row in enumerate(group.rows):
                if index % transfer.BATCH == 0:
                    check()
                if row[0] in expected and not transfer.native_equal39(expected[row[0]], row):
                    raise ValueError("history_outcome_conflicting_native_readset")
                expected[row[0]] = row

    def _verify_forecast(
        self, session, value, entry, sources, files, check, feature_bytes, capture_cache
    ):
        forecasts, writer = self.forecasts, self.forecasts.writer
        if value.source_files not in sources:
            raise ValueError("history39_unknown_or_mixed_producer_source")
        if forecasts.grants.get(value.scope_id) != value.model_id:
            raise ValueError("history_v2_forecast_scope_not_server_granted")
        # Reconstruct from issuer-bound bytes. No mutable model DTO is cached.
        model = verify_payload(HistoryDatasetFitModelV2.model_validate_json(entry.model_bytes))
        model_row = next(
            group.rows[0] for group in entry.proof.rows if group.table == "market_history_v2_models"
        )
        if (
            model.owner_claim != forecasts.owner_claim
            or model.scope_id != value.scope_id
            or (
                model.model_id,
                model.payload_hash,
                model.model_known_at,
                model_row[8],
                model_row[9],
            )
            != (
                value.model_id,
                value.model_hash,
                value.model_known_at,
                value.model_original_ref,
                value.model_original_hash,
            )
        ):
            raise ValueError("history_v2_forecast_model_original_changed")
        source = json.loads(
            writer.archive.read(value.source_original_ref, value.source_original_hash)
        )
        if (
            source.get("schema_version") != "history-v2-forecast-source-original.v1"
            or tuple((item["path"], item["sha256"]) for item in source["files"])
            != value.source_files
        ):
            raise ValueError("history_v2_forecast_saved_source_changed")
        for item in source["files"]:
            check()
            if (
                hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
                != item["sha256"]
            ):
                raise ValueError("history_v2_forecast_saved_source_changed")
        scope = next(
            spot_scope(asset)
            for asset in ("BTC-USDT", "ETH-USDT")
            if spot_scope(asset).scope_id == value.scope_id
        )
        bars, feature = load_window(session, scope, value.cutoff_at, value.cutoff_at, capture_cache)
        closed = [bar for bar in bars if bar.close_at <= value.cutoff_at]
        latest = closed[-1].close_at if closed else None
        if latest is None or (value.cutoff_at - latest).total_seconds() > 180:
            raise ValueError("history_v2_forecast_recent_31_closes_required")
        by_close = {bar.close_at: bar for bar in closed}
        times = tuple(latest - timedelta(minutes=index) for index in range(30, -1, -1))
        if any(time not in by_close for time in times):
            raise ValueError("history_v2_forecast_recent_31_closes_required")
        selected = tuple(by_close[time] for time in times)
        check()
        if (
            selected != value.selected_closes
            or encoded(feature) != feature_bytes
            or writer.archive.reference(feature)
            != (value.feature_original_ref, value.feature_original_hash)
        ):
            raise ValueError("history_v2_forecast_feature_readset_changed")
        if writer._numerics(model, selected) != (
            value.realized_vol_30m,
            value.selected_group,
            value.frequencies,
        ):
            raise ValueError("history_v2_forecast_actual_probability_changed")
        expected = tuple(
            sorted(
                {
                    ("archive", value.model_original_ref, value.model_original_hash),
                    *(("raw", bar.capture_ref, bar.capture_hash) for bar in selected),
                }
            )
        )
        if value.original_readset != expected:
            raise ValueError("history_v2_forecast_incomplete_original_readset")
        candidates = transfer.feature_candidates(session, value.scope_id, value.cutoff_at)
        feature_pairs = tuple((bar["capture_ref"], bar["capture_hash"]) for bar in feature["bars"])
        rows = (
            transfer.NativeRows39(
                "market_candles", tuple(sorted(candidates, key=lambda row: row[0]))
            ),
        )
        self._capture_files(
            files,
            (
                (value.feature_original_ref, value.feature_original_hash),
                (value.source_original_ref, value.source_original_hash),
                *(
                    (str(forecasts.originals.raw.root / digest[:2] / digest), digest)
                    for _, digest in feature_pairs
                ),
            ),
            check,
        )
        return rows, candidates

    def read_many(
        self,
        session: Session,
        forecast_ids: tuple[str, ...],
        *,
        budget_seconds: float = 120.0,
        checkpoint: Callable[[], None] | None = None,
        capture_cache: CaptureCacheV2 | None = None,
    ) -> tuple[HistoryDatasetProspectiveForecastV2, ...]:
        """Authenticate all requested saved originals or raise; never return partials.

        Caller checkpoint may enforce a shorter overall score deadline. This
        method never opens a connection, commits, writes an archive, or retries.
        A slow deep reader is checked immediately on return and fails closed.
        """
        started = perf_counter()
        if (
            type(forecast_ids) is not tuple
            or len(forecast_ids) > 48
            or any(type(value) is not str or not value for value in forecast_ids)
            or len(set(forecast_ids)) != len(forecast_ids)
            or type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds)
            or not 0 < budget_seconds <= 120
            or (checkpoint is not None and not callable(checkpoint))
        ):
            raise ValueError("history_outcome_forecast_batch_or_budget_invalid")
        deadline = started + budget_seconds
        own_capture_cache = capture_cache is None
        if capture_cache is not None:
            if (
                type(capture_cache) is not CaptureCacheV2
                or type(capture_cache.issuer) is not HistoryOutcomeCaptureAuthorityV2
                or capture_cache.issuer.forecasts is not self.forecasts
            ):
                raise ValueError("history_outcome_exact_capture_capability_required")
            capture_cache.issuer.authenticate(session, capture_cache)
            boundary = capture_cache.boundary
        else:
            boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history_outcome_forecast_physical_readonly_required")

        def check():
            if perf_counter() >= deadline:
                raise HistoryOutcomeForecastBudgetV2Error(
                    "history_outcome_full_score_budget_reached"
                )
            if checkpoint is not None:
                checkpoint()
            if perf_counter() >= deadline:
                raise HistoryOutcomeForecastBudgetV2Error(
                    "history_outcome_full_score_budget_reached"
                )

        check()
        sources = self._sources()
        schema = self._forecast_schema(session)
        shared_schema = transfer.capture_schemas39(session, tuple(transfer.PROJECTIONS))
        cache = _RequestModels(self, session, boundary, sources)
        try:
            saved, native_rows, exact_models = [], [], {}
            files, readset = {}, {}
            self._capture_files(
                files,
                (
                    (str(self.forecasts.originals.source_root / path), digest)
                    for path, digest in (*sources[1], (SOURCE_FILE, LOADED_SOURCE_SHA256))
                ),
                check,
            )
            for identifier in forecast_ids:
                check()
                value = self.forecasts.writer._saved_identity(session, identifier)
                if value.source_files not in sources:
                    raise ValueError("history39_unknown_or_mixed_producer_source")
                row = self._forecast_row(session, identifier)
                saved.append(value)
                native_rows.append((identifier, row))
                exact = (
                    value.model_id,
                    value.model_hash,
                    value.model_original_ref,
                    value.model_original_hash,
                )
                if value.model_id in exact_models and exact_models[value.model_id] != exact:
                    raise ValueError("history_outcome_conflicting_exact_model")
                exact_models[value.model_id] = exact
                columns = dict(zip((column[1] for column in schema), row, strict=True))
                self._capture_files(
                    files, ((columns["original_ref"], columns["original_hash"]),), check
                )
            feature_bytes, capture_refs = {}, set()
            for value in saved:
                check()
                body = self.forecasts.archive.read(
                    value.feature_original_ref, value.feature_original_hash
                )
                feature_bytes[value.forecast_id] = body
                retained = json.loads(body)
                capture_refs.update(bar["capture_ref"] for bar in retained["bars"])
                self._capture_files(
                    files, ((value.feature_original_ref, value.feature_original_hash),), check
                )
            if capture_cache is None:
                capture_cache = HistoryOutcomeCaptureAuthorityV2(self.forecasts).issue(
                    session, tuple(sorted(capture_refs)), checkpoint=check, boundary=boundary
                )
            elif not capture_refs <= set(capture_cache.refs):
                raise ValueError("history_outcome_capture_ref_not_in_complete_pool")
            entries = []
            for identifier, exact in exact_models.items():
                check()
                authority, proof, model = self._issue_model(session, identifier)
                check()
                if (
                    type(authority) is not transfer.TransferAuthority39
                    or type(proof) is not transfer.ModelProof39
                    or proof.issuer is not authority
                    or authority.service is not self.forecasts.writer
                    or encoded(model.model_dump(mode="json")) != proof.model_bytes
                    or (model.model_id, model.payload_hash) != exact[:2]
                ):
                    raise ValueError("history_outcome_exact_model_proof_changed")
                authority.authenticate(session, proof)
                entry = _ModelEntry(exact, proof.model_bytes, authority, proof)
                entries.append(entry)
                self._adopt_model_files(files, proof, check)
                self._merge_readset(readset, proof.rows, check)
            cache.issue(entries)
            cache.authenticate()
            entry_map = {entry.exact_model[0]: entry for entry in entries}
            closures = []
            for value in saved:
                check()
                cache.authenticate()
                closures.append(
                    self._verify_forecast(
                        session,
                        value,
                        entry_map[value.model_id],
                        sources,
                        files,
                        check,
                        feature_bytes[value.forecast_id],
                        capture_cache,
                    )
                )
                self._merge_readset(readset, closures[-1][0], check)
            # Complete exit authentication covers shared model metadata/files,
            # every forecast row, and all eligible feature/capture candidates.
            check()
            cache.authenticate()
            for entry in entries:
                entry.authority.authenticate(session, entry.proof)
                check()
            for table, rows in sorted(readset.items()):
                transfer.verify_rows(
                    session,
                    (transfer.NativeRows39(table, tuple(rows[key] for key in sorted(rows))),),
                )
                check()
            for value, (_rows, candidates), (identifier, expected_row) in zip(
                saved, closures, native_rows, strict=True
            ):
                if not transfer.native_equal39(
                    transfer.feature_candidates(session, value.scope_id, value.cutoff_at),
                    candidates,
                ) or not transfer.native_equal39(
                    self._forecast_row(session, identifier), expected_row
                ):
                    raise ValueError("history_outcome_forecast_native_readset_changed")
                check()
            for path, expected in files.items():
                if self._fingerprint(path, check) != expected:
                    raise ValueError("history39_original_bytes_changed")
            transfer.verify_schemas39(session, shared_schema)
            if not transfer.native_equal39(self._forecast_schema(session), schema):
                raise ValueError("history_outcome_complete_forecast_schema_changed")
            self._sources(sources)
            cache.authenticate()
            check()
            if own_capture_cache:
                capture_cache.issuer.close(session, capture_cache)
            else:
                capture_cache.issuer.verify(session, capture_cache)
            return tuple(saved)
        finally:
            cache.close()
            if own_capture_cache and capture_cache is not None:
                capture_cache.issuer.abort()


LOADED_METHODS = tuple(
    (cls, name, getattr(cls, name), getattr(cls, name).__code__)
    for cls in (_RequestModels, HistoryOutcomeForecastsV2Authenticator)
    for name in cls.__dict__
    if callable(getattr(cls, name)) and hasattr(getattr(cls, name), "__code__")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "encoded",
        "verify_payload",
        "snapshot_identity",
        "require_snapshot",
        "spot_scope",
        "load_window",
    )
)
LOADED_CLASSES = tuple(
    (name, globals()[name])
    for name in (
        "_RequestModels",
        "_ModelEntry",
        "HistoryOutcomeForecastsV2Authenticator",
        "HistoryForecastDispatch39Service",
        "HistoryDatasetFitModelV2",
        "HistoryDatasetProspectiveForecastV2",
        "HistoryOutcomeForecastBudgetV2Error",
        "CaptureCacheV2",
        "HistoryOutcomeCaptureAuthorityV2",
    )
)
LOADED_TRANSFER = transfer
LOADED_TRANSFER_FUNCTIONS = tuple(
    (name, getattr(transfer, name), getattr(transfer, name).__code__)
    for name in (
        "native_equal39",
        "database_identity",
        "capture_schemas39",
        "verify_schemas39",
        "capture_rows",
        "verify_rows",
        "feature_candidates",
    )
)
