"""Authenticated store inputs for a finite numerical experiment; never fetch or backdate."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel

from ..decision.path_forecast import PARAMETERS, verify_training_window
from ..decision.path_risk import compute_path_risk
from ..persistence.db import utcnow
from ..ports.market_path import MarketCandle, MarketPathScope, aware_utc, canonical_hash
from ..ports.path_forecast import (
    FrozenPathForecastModel,
    PathCandleReference,
    PathFeatureManifest,
    PathObservationReceipt,
    PathTrainingWindow,
)
from .market_features import build_market_features
from .market_path_store import MarketPathStore


def references(bars: tuple[MarketCandle, ...]) -> tuple[PathCandleReference, ...]:
    return tuple(
        PathCandleReference(
            open_at=bar.open_at,
            revision_hash=canonical_hash(bar.semantic_payload()),
            capture_ref=bar.capture_ref,
            capture_hash=bar.capture_hash,
            row_index=bar.row_index,
            received_at=bar.received_at,
            known_at=bar.known_at,
        )
        for bar in bars
    )


def seal_payload[T: BaseModel](payload: T) -> T:
    return payload.model_copy(
        update={
            "payload_hash": canonical_hash(
                payload.model_dump(mode="json", exclude={"payload_hash"})
            )
        }
    )


class MarketPathForecastInputs:
    def __init__(self, store: MarketPathStore, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.clock = clock

    def feature_manifest(self, scope: MarketPathScope, cutoff_at: datetime) -> PathFeatureManifest:
        cutoff, now = aware_utc(cutoff_at), aware_utc(self.clock())
        if cutoff > now:
            raise ValueError("path_feature_future_cutoff")
        # 34 bars cover a 31-close window whose newest close is up to 3m old.
        bars = self.store.load(
            scope,
            start_at=cutoff - timedelta(minutes=34),
            end_at=cutoff,
            as_of=cutoff,
        )
        feature = build_market_features(scope, bars, cutoff)
        delay = (
            int((cutoff - feature.latest_close_at).total_seconds())
            if (feature.latest_close_at is not None)
            else None
        )
        return seal_payload(
            PathFeatureManifest(
                scope=scope,
                cutoff_at=cutoff,
                captured_at=aware_utc(self.clock()),
                features=feature,
                feature_delay_seconds=delay,
                originals=references(bars),
                payload_hash="0" * 64,
            )
        )

    def observe(self, scope: MarketPathScope, cutoff_at: datetime) -> PathObservationReceipt:
        cutoff, as_of = aware_utc(cutoff_at), aware_utc(self.clock())
        start, end = cutoff + timedelta(minutes=1), cutoff + timedelta(days=1, minutes=1)
        if as_of < end:
            raise ValueError("path_label_not_mature")
        bars = self.store.load(scope, start_at=start, end_at=end, as_of=as_of)
        path = compute_path_risk(scope, bars, start, end, as_of=as_of)
        return seal_payload(
            PathObservationReceipt(
                scope=scope,
                input_cutoff_at=cutoff,
                path=path,
                label_known_at=aware_utc(self.clock()),
                originals=references(bars),
                payload_hash="0" * 64,
            )
        )

    def training_windows(
        self, *, scope: MarketPathScope, known_by: datetime
    ) -> tuple[PathTrainingWindow, ...]:
        known = aware_utc(known_by)
        if known > aware_utc(self.clock()):
            raise ValueError("path_training_future_knowledge")
        day = known.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = []
        for offset in range(PARAMETERS.history_days, 0, -1):
            cutoff = day - timedelta(days=offset)
            start, end = cutoff + timedelta(minutes=1), cutoff + timedelta(days=1, minutes=1)
            if end > known:
                continue
            path_bars = self.store.load(scope, start_at=start, end_at=end, as_of=known)
            # Backfilled old bars are usable training labels, never old PIT features.
            feature_bars = self.store.load(
                scope,
                start_at=cutoff - timedelta(minutes=34),
                end_at=cutoff,
                as_of=cutoff,
            )
            path = compute_path_risk(scope, path_bars, start, end, as_of=known)
            features = build_market_features(scope, feature_bars, cutoff)
            row = PathTrainingWindow(
                input_cutoff_at=cutoff,
                path=path,
                features=features,
                label_known_at=aware_utc(self.clock()),
                label_ref=self.training_ref(scope, cutoff, path_bars),
                input_hash="0" * 64,
            )
            rows.append(
                row.model_copy(
                    update={
                        "input_hash": canonical_hash(
                            row.model_dump(mode="json", exclude={"input_hash"})
                        )
                    }
                )
            )
        return tuple(rows)

    @staticmethod
    def training_ref(scope, cutoff, bars) -> str:
        return "risk_training_path:" + canonical_hash(
            [
                scope.scope_id,
                cutoff.isoformat(),
                [ref.model_dump(mode="json") for ref in references(bars)],
            ]
        )

    def verify_training_originals(self, model: FrozenPathForecastModel) -> None:
        for window in model.accepted:
            verify_training_window(window)
            path = window.path
            bars = self.store.load(
                model.scope,
                start_at=path.window_start_at,
                end_at=path.window_end_at,
                as_of=path.as_of,
            )
            features = self.store.load(
                model.scope,
                start_at=window.input_cutoff_at - timedelta(minutes=34),
                end_at=window.input_cutoff_at,
                as_of=window.input_cutoff_at,
            )
            if (
                compute_path_risk(
                    model.scope, bars, path.window_start_at, path.window_end_at, as_of=path.as_of
                )
                != path
                or (
                    build_market_features(model.scope, features, window.input_cutoff_at)
                    != window.features
                )
                or self.training_ref(model.scope, window.input_cutoff_at, bars) != window.label_ref
            ):
                raise ValueError("path_training_original_manifest_changed")

    def verify_observation_originals(self, receipt: PathObservationReceipt) -> None:
        path = receipt.path
        bars = self.store.load(
            receipt.scope,
            start_at=path.window_start_at,
            end_at=path.window_end_at,
            as_of=path.as_of,
        )
        recomputed = compute_path_risk(
            receipt.scope,
            bars,
            path.window_start_at,
            path.window_end_at,
            as_of=path.as_of,
        )
        if references(bars) != receipt.originals or recomputed != path:
            raise ValueError("path_label_original_manifest_changed")
