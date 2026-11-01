"""Frozen readset to empirical math, with fresh-writer provenance revalidation."""

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
from statistics import median

from ..decision.path_risk import compute_path_risk
from ..persistence.db import as_utc
from ..persistence.history_dataset_model_v2_records import (
    LOADED_SOURCE_SHA256 as RECORDS_LOADED_SHA,
)
from ..persistence.history_dataset_model_v2_records import HistoryDatasetModelV2Record as ModelRow
from ..persistence.history_dataset_v2_records import HistoryDatasetV2OriginalRecord as Original
from ..ports.history_dataset_model_v2 import LOADED_SOURCE_SHA256 as PORTS_LOADED_SHA
from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_observed_training import seal, verify_payload
from ..ports.market_path import MarketCandle, canonical_hash, spot_scope
from ..ports.path_forecast import EmpiricalDistribution
from .history_dataset_v2 import HistoryDatasetV2Service
from .history_observed_dataset import (
    HistoryObservedArchive,
    encoded,
    require_snapshot,
    snapshot_identity,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MODEL_FILES = (
    "packages/kernel/decision_hub_kernel/ports/history_dataset_model_v2.py",
    "packages/kernel/decision_hub_kernel/persistence/history_dataset_model_v2_records.py",
    "packages/kernel/decision_hub_kernel/application/history_dataset_model_v2.py",
    "apps/hub_worker/history_dataset_model_v2_cli.py",
)


def model_id(owner, request, scope):
    return "hfit2_" + canonical_hash([owner, request, scope])[:48]


@dataclass(frozen=True)
class HistoryDatasetFitPreparedV2:
    value: HistoryDatasetFitModelV2
    dataset: object
    issuer: object
    thread_id: int
    authentication: bytes


class HistoryDatasetModelV2Service:
    def __init__(self, originals: HistoryDatasetV2Service):
        self.originals = originals
        self.archive = HistoryObservedArchive(originals.data_directory / "history-dataset-model-v2")
        self.key = secrets.token_bytes(32)

    def sources(self, expected=None):
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
            raise ValueError("history_v2_fit_loaded_source_changed")
        sources = (
            *self.originals.require_sources(),
            *(
                (name, hashlib.sha256((self.originals.source_root / name).read_bytes()).hexdigest())
                for name in MODEL_FILES
            ),
        )
        loaded = {
            MODEL_FILES[0]: PORTS_LOADED_SHA,
            MODEL_FILES[1]: RECORDS_LOADED_SHA,
            MODEL_FILES[2]: LOADED_SOURCE_SHA256,
        }
        if any(dict(sources)[name] != digest for name, digest in loaded.items()):
            raise ValueError("history_v2_fit_loaded_dependency_source_changed")
        if expected is not None and sources != expected:
            raise ValueError("history_v2_fit_producer_source_changed")
        return sources

    def _derive(self, session, dataset, scope_id):
        scope = next(
            (
                spot_scope(asset)
                for asset in ("BTC-USDT", "ETH-USDT")
                if spot_scope(asset).scope_id == scope_id
            ),
            None,
        )
        if scope is None or dataset.kind != "dataset":
            raise ValueError("history_v2_fit_dataset_scope_invalid")
        if dataset.provenance.get("fixture_mode") is not self.originals.fixture_mode:
            raise ValueError("history_v2_fit_fixture_authority_mismatch")
        selected = dict(dataset.provenance["selected_rows"][scope_id])
        expected_rows = {
            (table, key): digest for table, key, digest in dataset.provenance["row_readset"]
        }
        bars = {}
        values = list(selected.items())
        for offset in range(0, len(values), 700):
            batch = values[offset : offset + 700]
            keys = [info["candle_id"] for _, info in batch]
            stored = (
                session.connection()
                .exec_driver_sql(
                    "SELECT * FROM market_candles WHERE candle_id IN ("
                    + ",".join("?" for _ in keys)
                    + ")",
                    tuple(keys),
                )
                .mappings()
                .all()
            )
            by_id = {row["candle_id"]: dict(row) for row in stored}
            for stamp, info in batch:
                row = by_id.get(info["candle_id"])
                if row is None or canonical_hash(row) != expected_rows.get(
                    ("market_candles", info["candle_id"])
                ):
                    raise ValueError("history_v2_fit_selected_metadata_changed")
                bar = MarketCandle.model_validate_json(row["candle_json"])
                if (
                    bar.scope_id != scope_id
                    or int(bar.open_at.timestamp() * 1000) != stamp
                    or canonical_hash(bar.semantic_payload()) != info["semantic_revision"]
                ):
                    raise ValueError("history_v2_fit_selected_semantics_changed")
                witness = dataset.provenance["original_witnesses"][info["witness_index"]]
                if (
                    witness["scope_id"] != scope_id
                    or witness["page"]["capture_ref"] != bar.capture_ref
                    or witness["page"]["capture_hash"] != bar.capture_hash
                    or [stamp, info["semantic_revision"]] not in witness["page"]["rows"]
                ):
                    raise ValueError("history_v2_fit_selected_capture_unowned")
                bars[stamp] = bar
        windows = []
        for day in dataset.cutoffs:
            stamp = int(day.timestamp() * 1000)
            feature = [bars.get(stamp - index * 60000) for index in range(31, 0, -1)]
            label = [bars.get(stamp + index * 60000) for index in range(1, 1441)]
            reasons = []
            if any(bar is None for bar in feature):
                reasons.append("complete_31_feature_unavailable")
            if any(bar is None for bar in label):
                reasons.append("complete_1440_label_unavailable")
            actual = [bar for bar in (*feature, *label) if bar is not None]
            if any(
                bar.known_at > dataset.known_asof or bar.close_at > dataset.known_asof
                for bar in actual
            ):
                reasons.append("original_not_known_at_frozen_K")
            window = {
                "cutoff_at": day.isoformat(),
                "status": "excluded" if reasons else "joint",
                "reasons": reasons,
                "realized_vol_30m": None,
                "mae_long": None,
                "mae_short": None,
            }
            if not reasons:
                with localcontext() as context:
                    context.prec, context.rounding = 28, ROUND_HALF_EVEN
                    rv = Decimal(
                        str(
                            math.sqrt(
                                sum(
                                    math.log(float(right.close / left.close)) ** 2
                                    for left, right in zip(feature, feature[1:], strict=False)
                                )
                            )
                        )
                    )
                    path = compute_path_risk(
                        scope,
                        tuple(actual),
                        day + timedelta(minutes=1),
                        day + timedelta(days=1, minutes=1),
                        as_of=dataset.known_asof,
                    )
                if (
                    path.window_status != "complete"
                    or path.anchor_status != "verified"
                    or path.quality != "observed"
                ):
                    raise ValueError("history_v2_fit_complete_path_binding_invalid")
                window.update(
                    {
                        "realized_vol_30m": str(rv),
                        "mae_long": str(path.mae_long),
                        "mae_short": str(path.mae_short),
                        "semantic_hash": path.semantic_hash,
                        "manifest_hash": path.manifest_hash,
                    }
                )
            windows.append(window)
        joint = [item for item in windows if item["status"] == "joint"]
        middle = (
            Decimal(median([Decimal(item["realized_vol_30m"]) for item in joint]))
            if joint
            else None
        )
        low = [item for item in joint if Decimal(item["realized_vol_30m"]) <= middle]
        high = [item for item in joint if Decimal(item["realized_vol_30m"]) > middle]

        def support(items):
            return EmpiricalDistribution(
                n=len(items),
                long_mae_sorted=tuple(sorted(Decimal(item["mae_long"]) for item in items)),
                short_mae_sorted=tuple(sorted(Decimal(item["mae_short"]) for item in items)),
            )

        return {
            "windows": windows,
            "joint_days": len(joint),
            "low_days": len(low),
            "high_days": len(high),
            "rv_median": middle,
            "count_gate_met": len(joint) >= 60 and min(len(low), len(high)) >= 30,
            "baseline": support(joint),
            "low": support(low),
            "high": support(high),
        }

    def readiness(self, session, *, dataset_id, owner_claim, scope_id):
        boundary = snapshot_identity(session)
        self.sources()
        dataset = self.originals.read_original(session, dataset_id, owner_claim)
        result = self._derive(session, dataset, scope_id)
        self.sources()
        require_snapshot(session, boundary)
        return {
            key: value for key, value in result.items() if key not in {"baseline", "low", "high"}
        }

    def _auth(self, prepared):
        return hmac.digest(
            self.key,
            encoded(
                [
                    prepared.value.model_dump(mode="json"),
                    prepared.dataset.payload_hash,
                    prepared.thread_id,
                    id(self),
                ]
            ),
            "sha256",
        )

    def prepare(self, session, *, dataset_id, owner_claim, request_id, scope_id):
        boundary = snapshot_identity(session)
        sources = self.sources()
        dataset = self.originals.read_original(session, dataset_id, owner_claim)
        started = self.originals.now()
        result = self._derive(session, dataset, scope_id)
        if not result["count_gate_met"]:
            raise ValueError("history_v2_fit_60_30_30_gate_not_met")
        completed = self.originals.now()
        source_body = {
            "schema_version": "history-v2-model-source-original.v1",
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
        source_ref, source_hash = self.archive.reference(source_body)
        row = session.get(Original, dataset_id, populate_existing=True)
        value = seal(
            HistoryDatasetFitModelV2,
            model_id=model_id(owner_claim, request_id, scope_id),
            owner_claim=owner_claim,
            request_id=request_id,
            plan_id=dataset.plan_id,
            dataset_id=dataset_id,
            dataset_hash=dataset.payload_hash,
            scope_id=scope_id,
            cutoffs=dataset.cutoffs,
            data_information_asof=dataset.known_asof,
            dataset_recorded_at=dataset.recorded_at,
            fit_started_at=started,
            fit_completed_at=completed,
            model_known_at=completed,
            recorded_at=completed,
            baseline=result["baseline"],
            conditional_low=result["low"],
            conditional_high=result["high"],
            training_vol_median=result["rv_median"],
            windows=tuple(result["windows"]),
            source_files=sources,
            source_original_ref=source_ref,
            source_original_hash=source_hash,
            dataset_original_ref=row.original_ref,
            dataset_original_hash=row.original_hash,
        )
        candidate = HistoryDatasetFitPreparedV2(value, dataset, self, threading.get_ident(), b"")
        prepared = HistoryDatasetFitPreparedV2(
            value, dataset, self, candidate.thread_id, self._auth(candidate)
        )
        self.sources(sources)
        require_snapshot(session, boundary)
        return prepared

    def record(self, session, prepared):
        boundary = snapshot_identity(session)
        if (
            prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not hmac.compare_digest(prepared.authentication, self._auth(prepared))
        ):
            raise ValueError("history_v2_fit_preparation_changed")
        value = verify_payload(prepared.value)
        self.sources(value.source_files)
        dataset = self.originals.read_original(session, value.dataset_id, value.owner_claim)
        if dataset != prepared.dataset:
            raise ValueError("history_v2_fit_dataset_changed")
        result = self._derive(session, dataset, value.scope_id)
        if (
            value.baseline,
            value.conditional_low,
            value.conditional_high,
            value.training_vol_median,
            value.windows,
        ) != (
            result["baseline"],
            result["low"],
            result["high"],
            result["rv_median"],
            tuple(result["windows"]),
        ):
            raise ValueError("history_v2_fit_actual_support_changed")
        existing = session.get(ModelRow, value.model_id, populate_existing=True)
        if existing is not None:
            first = self.read(session, value.model_id, value.owner_claim)
            if (first.dataset_id, first.dataset_hash, first.scope_id) != (
                value.dataset_id,
                value.dataset_hash,
                value.scope_id,
            ):
                raise ValueError("history_v2_fit_request_conflict")
            require_snapshot(session, boundary)
            return first
        now = self.originals.now()
        value = seal(
            HistoryDatasetFitModelV2,
            **{
                **value.model_dump(exclude={"payload_hash"}),
                "model_known_at": now,
                "recorded_at": now,
            },
        )
        source_body = {
            "schema_version": "history-v2-model-source-original.v1",
            "files": [
                {
                    "path": name,
                    "sha256": digest,
                    "bytes_base64": base64.b64encode(
                        (self.originals.source_root / name).read_bytes()
                    ).decode(),
                }
                for name, digest in value.source_files
            ],
        }
        if self.archive.put(source_body) != (value.source_original_ref, value.source_original_hash):
            raise ValueError("history_v2_fit_source_original_changed")
        ref, digest = self.archive.put(value.model_dump(mode="json"))
        self.sources(value.source_files)
        self.originals.read_original(session, dataset.original_id, value.owner_claim)
        require_snapshot(session, boundary, before_write=True)
        session.add(
            ModelRow(
                model_id=value.model_id,
                owner_claim=value.owner_claim,
                request_id=value.request_id,
                scope_id=value.scope_id,
                plan_id=value.plan_id,
                dataset_id=value.dataset_id,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=ref,
                original_hash=digest,
                known_at=now,
                recorded_at=now,
            )
        )
        session.flush()
        self.sources(value.source_files)
        require_snapshot(session, (*boundary[:4], boundary[4] + 1, *boundary[5:]))
        return value

    def read(self, session, identifier, owner):
        boundary = snapshot_identity(session)
        row = session.get(ModelRow, identifier, populate_existing=True)
        if row is None or row.owner_claim != owner:
            raise ValueError("history_v2_saved_model_missing_or_owner_conflict")
        body = self.archive.read(row.original_ref, row.original_hash)
        value = verify_payload(HistoryDatasetFitModelV2.model_validate_json(body))
        if (
            row.model_id,
            row.owner_claim,
            row.request_id,
            row.scope_id,
            row.plan_id,
            row.dataset_id,
            row.payload_hash,
            row.payload_json.encode(),
            as_utc(row.known_at),
            as_utc(row.recorded_at),
        ) != (
            value.model_id,
            value.owner_claim,
            value.request_id,
            value.scope_id,
            value.plan_id,
            value.dataset_id,
            value.payload_hash,
            body,
            value.model_known_at,
            value.recorded_at,
        ) or value.model_known_at > self.originals.now():
            raise ValueError("history_v2_saved_model_identity_changed")
        self.sources(value.source_files)
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
            raise ValueError("history_v2_saved_model_source_changed")
        dataset = self.originals.read_original(session, value.dataset_id, owner)
        dataset_row = session.get(Original, value.dataset_id, populate_existing=True)
        if (dataset.payload_hash, dataset_row.original_ref, dataset_row.original_hash) != (
            value.dataset_hash,
            value.dataset_original_ref,
            value.dataset_original_hash,
        ):
            raise ValueError("history_v2_saved_model_dataset_binding_changed")
        result = self._derive(session, dataset, value.scope_id)
        if (
            value.baseline,
            value.conditional_low,
            value.conditional_high,
            value.training_vol_median,
            value.windows,
        ) != (
            result["baseline"],
            result["low"],
            result["high"],
            result["rv_median"],
            tuple(result["windows"]),
        ):
            raise ValueError("history_v2_saved_model_actual_support_changed")
        self.sources(value.source_files)
        require_snapshot(session, boundary)
        return value
