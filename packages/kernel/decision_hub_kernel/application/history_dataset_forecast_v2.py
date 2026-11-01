"""Trusted server grants to one future forecast; no user hash, time or HTTP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from types import MappingProxyType

from ..persistence.db import as_utc
from ..persistence.history_dataset_forecast_v2_records import LOADED_SOURCE_SHA256 as RECORDS_SHA
from ..persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Record,
)
from ..persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as ModelRecord,
)
from ..ports.history_dataset_forecast_v2 import LOADED_SOURCE_SHA256 as PORTS_SHA
from ..ports.history_dataset_forecast_v2 import (
    HistoryDatasetProspectiveForecastV2,
    HistoryDatasetRiskFrequencyV2,
)
from ..ports.history_observed_training import seal, verify_payload
from ..ports.market_path import canonical_hash, spot_scope
from .history_dataset_model_v2 import HistoryDatasetModelV2Service
from .history_observed_dataset import (
    HistoryObservedArchive,
    HistoryObservedOriginals,
    encoded,
    require_snapshot,
    snapshot_identity,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
FORECAST_FILES = (
    "packages/kernel/decision_hub_kernel/ports/history_dataset_forecast_v2.py",
    "packages/kernel/decision_hub_kernel/persistence/history_dataset_forecast_v2_records.py",
    "packages/kernel/decision_hub_kernel/application/history_dataset_forecast_v2.py",
)


def forecast_id(owner, request):
    if not owner.strip() or not request.strip() or max(len(owner), len(request)) > 128:
        raise ValueError("history_v2_forecast_request_identity_invalid")
    return "hforecast2_" + canonical_hash([owner, request])[:48]


class HistoryDatasetForecastCommittedReadbackError(ValueError):
    committed = True
    automatic_retry = False


@dataclass(frozen=True)
class HistoryDatasetForecastPreparedV2:
    value: HistoryDatasetProspectiveForecastV2
    model: object
    feature_source: dict
    source_original: dict
    issuer: object
    thread_id: int
    authentication: bytes


class HistoryDatasetForecastV2Service:
    def __init__(
        self, models: HistoryDatasetModelV2Service, *, owner_claim: str, grants: dict[str, str]
    ):
        if type(models) is not HistoryDatasetModelV2Service:
            raise ValueError("history_v2_forecast_actual_model_service_required")
        enabled = {spot_scope(asset).scope_id for asset in ("BTC-USDT", "ETH-USDT")}
        if not grants or set(grants) - enabled or any(not value for value in grants.values()):
            raise ValueError("history_v2_forecast_server_grant_invalid")
        forecast_id(owner_claim, "server_identity")
        self.models, self.originals = models, models.originals
        self.commands = self.originals.commands
        self.reader = self.commands.ledger.builder.originals
        if type(self.reader) is not HistoryObservedOriginals:
            raise ValueError("history_v2_forecast_actual_original_reader_required")
        self.owner_claim = owner_claim
        self._owner_identity = owner_claim
        self.grants = MappingProxyType(dict(grants))
        self._grant_identity = tuple(sorted(grants.items()))
        self.archive = HistoryObservedArchive(
            self.originals.data_directory / "history-dataset-forecast-v2"
        )
        self._key = secrets.token_bytes(32)
        self._instances = (
            models,
            self.originals,
            self.commands,
            self.reader,
            self.archive,
            self.reader.raw_reader,
        )
        self._bindings = tuple(
            (obj, name, getattr(type(obj), name), getattr(type(obj), name).__code__)
            for obj, name in ((models, "read"), (models, "sources"), (self.reader, "load"))
        )

    def _sources(self, expected=None):
        if (
            (
                self.models,
                self.models.originals,
                self.originals.commands,
                self.commands.ledger.builder.originals,
                self.archive,
                self.reader.raw_reader,
            )
            != self._instances
            or tuple(sorted(self.grants.items())) != self._grant_identity
            or self.owner_claim != self._owner_identity
        ):
            raise ValueError("history_v2_forecast_dependency_or_grant_changed")
        if any(
            getattr(type(obj), name) is not function
            or getattr(getattr(obj, name), "__func__", None) is not function
            or function.__code__ is not code
            for obj, name, function, code in self._bindings
        ):
            raise ValueError("history_v2_forecast_loaded_callable_changed")
        if any(
            getattr(HistoryDatasetForecastV2Service, name) is not function
            or function.__code__ is not code
            for name, function, code in LOADED_METHODS
        ):
            raise ValueError("history_v2_forecast_loaded_callable_changed")
        sources = (
            *self.models.sources(),
            *(
                (name, hashlib.sha256((self.originals.source_root / name).read_bytes()).hexdigest())
                for name in FORECAST_FILES
            ),
        )
        loaded = (PORTS_SHA, RECORDS_SHA, LOADED_SOURCE_SHA256)
        if any(
            dict(sources)[name] != digest
            for name, digest in zip(FORECAST_FILES, loaded, strict=True)
        ) or (expected is not None and sources != expected):
            raise ValueError("history_v2_forecast_producer_source_changed")
        return sources

    def _feature(self, session, scope_id, cutoff):
        scope = next(
            spot_scope(asset)
            for asset in ("BTC-USDT", "ETH-USDT")
            if spot_scope(asset).scope_id == scope_id
        )
        bars, source = self.reader.load(session, scope, cutoff, cutoff)
        closed = [bar for bar in bars if bar.close_at <= cutoff]
        latest = closed[-1].close_at if closed else None
        if latest is None or (cutoff - latest).total_seconds() > 180:
            raise ValueError("history_v2_forecast_recent_31_closes_required")
        by_close = {bar.close_at: bar for bar in closed}
        times = tuple(latest - timedelta(minutes=i) for i in range(30, -1, -1))
        if any(value not in by_close for value in times):
            raise ValueError("history_v2_forecast_recent_31_closes_required")
        return tuple(by_close[value] for value in times), source

    @staticmethod
    def _numerics(model, selected):
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
                model.conditional_low if rv <= model.training_vol_median else model.conditional_high
            )
            frequencies = []
            for threshold in ("0.02", "0.05"):
                limit = Decimal(threshold)

                def probability(values, *, limit=limit):
                    return (Decimal(sum(value >= limit for value in values)) + Decimal("0.5")) / (
                        Decimal(len(values)) + 1
                    )

                frequencies.append(
                    HistoryDatasetRiskFrequencyV2(
                        threshold=threshold,
                        baseline_n=model.baseline.n,
                        conditional_n=support.n,
                        baseline_long_probability=probability(model.baseline.long_mae_sorted),
                        baseline_short_probability=probability(model.baseline.short_mae_sorted),
                        conditional_long_probability=probability(support.long_mae_sorted),
                        conditional_short_probability=probability(support.short_mae_sorted),
                    )
                )
        return rv, "low" if rv <= model.training_vol_median else "high", tuple(frequencies)

    def _auth(self, prepared):
        return hmac.digest(
            self._key,
            encoded(
                [
                    prepared.value.model_dump(mode="json"),
                    prepared.model.payload_hash,
                    prepared.feature_source,
                    prepared.source_original,
                    prepared.thread_id,
                    id(self),
                ]
            ),
            "sha256",
        )

    def prepare(self, session, *, scope_id, request_id, cutoff_at):
        boundary = snapshot_identity(session)
        sources = self._sources()
        if scope_id not in self.grants:
            raise ValueError("history_v2_forecast_scope_not_server_granted")
        cutoff, now = cutoff_at, self.originals.now()
        if cutoff.timestamp() % 60 or cutoff > now:
            raise ValueError("history_v2_forecast_internal_cutoff_invalid")
        model = self.models.read(session, self.grants[scope_id], self.owner_claim)
        if model.scope_id != scope_id or model.model_known_at > cutoff:
            raise ValueError("history_v2_forecast_model_not_known_at_T")
        selected, feature = self._feature(session, scope_id, cutoff)
        rv, group, frequencies = self._numerics(model, selected)
        feature_ref, feature_hash = self.archive.reference(feature)
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
                for name, digest in sources
            ],
        }
        source_ref, source_hash = self.archive.reference(source)
        model_row = session.get(ModelRecord, model.model_id, populate_existing=True)
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
            model_authenticated_at=now,
            model_original_ref=model_row.original_ref,
            model_original_hash=model_row.original_hash,
            cutoff_at=cutoff,
            latest_close_at=selected[-1].close_at,
            feature_known_at=max(bar.known_at for bar in selected),
            selected_closes=selected,
            feature_original_ref=feature_ref,
            feature_original_hash=feature_hash,
            original_readset=tuple(
                sorted(
                    {
                        ("archive", model_row.original_ref, model_row.original_hash),
                        *(("raw", bar.capture_ref, bar.capture_hash) for bar in selected),
                    }
                )
            ),
            realized_vol_30m=rv,
            selected_group=group,
            frequencies=frequencies,
            source_files=sources,
            source_original_ref=source_ref,
            source_original_hash=source_hash,
            produced_at=produced,
            recorded_at=produced,
            fixture_mode=self.originals.fixture_mode,
        )
        candidate = HistoryDatasetForecastPreparedV2(
            value, model, feature, source, self, threading.get_ident(), b""
        )
        result = HistoryDatasetForecastPreparedV2(
            value, model, feature, source, self, candidate.thread_id, self._auth(candidate)
        )
        self._sources(sources)
        require_snapshot(session, boundary)
        return result

    def _verify_inputs(self, session, value, *, model=None, feature=None):
        loaded = self.models.read(session, value.model_id, self.owner_claim)
        row = session.get(ModelRecord, value.model_id, populate_existing=True)
        if (
            loaded.scope_id != value.scope_id
            or (loaded.payload_hash, loaded.model_known_at, row.original_ref, row.original_hash)
            != (
                value.model_hash,
                value.model_known_at,
                value.model_original_ref,
                value.model_original_hash,
            )
            or (model is not None and loaded != model)
        ):
            raise ValueError("history_v2_forecast_model_original_changed")
        selected, actual = self._feature(session, value.scope_id, value.cutoff_at)
        if (
            selected != value.selected_closes
            or (feature is not None and encoded(actual) != encoded(feature))
            or self.archive.reference(actual)
            != (value.feature_original_ref, value.feature_original_hash)
        ):
            raise ValueError("history_v2_forecast_feature_readset_changed")
        if self._numerics(loaded, selected) != (
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
        for kind, ref, digest in value.original_readset:
            body = (
                self.originals.raw.read(ref)
                if kind == "raw"
                else self.models.archive.read(ref, digest)
            )
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError("history_v2_forecast_original_readset_changed")
        return loaded

    def record(self, session, prepared):
        boundary = snapshot_identity(session)
        if (
            prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not hmac.compare_digest(prepared.authentication, self._auth(prepared))
        ):
            raise ValueError("history_v2_forecast_preparation_changed")
        value = verify_payload(prepared.value)
        self._sources(value.source_files)
        existing = session.get(Record, value.forecast_id, populate_existing=True)
        if existing is not None:
            first = self.read(session, value.forecast_id)
            if (first.scope_id, first.model_id, first.cutoff_at) != (
                value.scope_id,
                value.model_id,
                value.cutoff_at,
            ):
                raise ValueError("history_v2_forecast_request_conflict")
            require_snapshot(session, boundary)
            return first
        self._verify_inputs(session, value, model=prepared.model, feature=prepared.feature_source)
        if self.archive.put(prepared.feature_source) != (
            value.feature_original_ref,
            value.feature_original_hash,
        ) or self.archive.put(prepared.source_original) != (
            value.source_original_ref,
            value.source_original_hash,
        ):
            raise ValueError("history_v2_forecast_source_original_changed")
        value = seal(
            HistoryDatasetProspectiveForecastV2,
            **{**value.model_dump(exclude={"payload_hash"}), "recorded_at": self.originals.now()},
        )
        ref, digest = self.archive.put(value.model_dump(mode="json"))
        self._verify_inputs(session, value, model=prepared.model, feature=prepared.feature_source)
        self._sources(value.source_files)
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
        self._sources(value.source_files)
        require_snapshot(session, (*boundary[:4], boundary[4] + 1, *boundary[5:]))
        return value

    def read(self, session, identifier):
        boundary = snapshot_identity(session)
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
            or value.fixture_mode is not self.originals.fixture_mode
        ):
            raise ValueError("history_v2_forecast_saved_identity_changed")
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
        self._verify_inputs(session, value, feature=feature)
        self._sources(value.source_files)
        require_snapshot(session, boundary)
        return value

    def producer_once(self, scope_id, request_id=None):
        self._sources()
        if scope_id not in self.grants:
            raise ValueError("history_v2_forecast_scope_not_server_granted")
        cutoff = self.originals.now().replace(second=0, microsecond=0)
        request = (
            request_id
            or "auto_" + canonical_hash([self.grants[scope_id], scope_id, cutoff.isoformat()])[:48]
        )
        identifier = forecast_id(self.owner_claim, request)
        with self.commands._read_session() as session:
            if session.get(Record, identifier) is not None:
                first = self.read(session, identifier)
                if (first.scope_id, first.model_id) != (scope_id, self.grants[scope_id]):
                    raise ValueError("history_v2_forecast_request_conflict")
                return first
            prepared = self.prepare(
                session, scope_id=scope_id, request_id=request, cutoff_at=cutoff
            )
        # Any uncertain commit escapes. No automatic replay or second insert.
        with self.commands._write_session() as session:
            value = self.record(session, prepared)
        try:
            with self.commands._read_session() as session:
                loaded = self.read(session, value.forecast_id)
            if loaded != value:
                raise ValueError("history_v2_forecast_readback_value_changed")
        except Exception as error:
            unconfirmed = HistoryDatasetForecastCommittedReadbackError(
                "history_v2_forecast_committed_readback_unconfirmed"
            )
            unconfirmed.forecast_id = value.forecast_id
            unconfirmed.request_id = value.request_id
            unconfirmed.model_id = value.model_id
            unconfirmed.scope_id = value.scope_id
            unconfirmed.cutoff_at = value.cutoff_at
            raise unconfirmed from error
        return loaded


LOADED_METHODS = tuple(
    (
        name,
        getattr(HistoryDatasetForecastV2Service, name),
        getattr(HistoryDatasetForecastV2Service, name).__code__,
    )
    for name in (
        "_sources",
        "_feature",
        "_numerics",
        "_auth",
        "prepare",
        "_verify_inputs",
        "record",
        "read",
        "producer_once",
    )
)
