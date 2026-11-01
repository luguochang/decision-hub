"""Finite exact-source dispatch; retained source bytes are never executable."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .history_dataset_forecast_v2 import HistoryDatasetForecastV2Service
from .history_forecast_write39 import HistoryForecastWrite39Service
from .history_observed_dataset import require_snapshot, snapshot_identity

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryForecastDispatch39Service:
    def __init__(self, writer):
        if type(writer) is not HistoryForecastWrite39Service:
            raise ValueError("history39_actual_writer_required")
        self.writer = writer
        self.models, self.originals, self.commands = (
            writer.models,
            writer.originals,
            writer.commands,
        )
        self.owner_claim, self.grants, self.archive = (
            writer.owner_claim,
            writer.grants,
            writer.archive,
        )
        self.legacy = HistoryDatasetForecastV2Service(
            self.models, owner_claim=self.owner_claim, grants=dict(self.grants)
        )
        self._identity = (
            writer,
            self.models,
            self.originals,
            self.commands,
            self.owner_claim,
            self.grants,
            self.archive,
            self.legacy,
        )

    def _sources(self, expected=None):
        if (
            self.writer,
            self.writer.models,
            self.writer.originals,
            self.writer.commands,
            self.writer.owner_claim,
            self.writer.grants,
            self.writer.archive,
            self.legacy,
        ) != self._identity:
            raise ValueError("history39_dispatch_dependency_changed")
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
            raise ValueError("history39_dispatch_loaded_source_changed")
        if any(
            getattr(HistoryForecastDispatch39Service, name) is not function
            or function.__code__ is not code
            for name, function, code in LOADED_METHODS
        ):
            raise ValueError("history39_dispatch_loaded_callable_changed")
        return self.writer._sources(expected)

    def read(self, session, identifier):
        boundary = snapshot_identity(session)
        # Authenticate row/archive/payload identity before interpreting version.
        value = self.writer._saved_identity(session, identifier)
        old_sources = self.legacy._sources()
        new_sources = self._sources()
        if value.source_files == old_sources:
            result = self.legacy.read(session, identifier)
        elif value.source_files == new_sources:
            result = self.writer.read(session, identifier)
        else:
            raise ValueError("history39_unknown_or_mixed_producer_source")
        self._sources(new_sources)
        require_snapshot(session, boundary)
        return result

    def authenticate_model(self, scope_id):
        self._sources()
        return self.writer.authenticate_model(scope_id)

    def prepare(self, session, **kwargs):
        self._sources()
        return self.writer.prepare(session, **kwargs)

    def record(self, session, prepared):
        self._sources()
        return self.writer.record(session, prepared)

    def save_prepared(self, prepared, **kwargs):
        self._sources()
        return self.writer.save_prepared(prepared, **kwargs)

    def producer_once(self, scope_id, request_id=None):
        self._sources()
        return self.writer.producer_once(scope_id, request_id)

    @property
    def last_phase_timing(self):
        return self.writer.last_phase_timing


LOADED_METHODS = tuple(
    (name, function, function.__code__)
    for name, function in HistoryForecastDispatch39Service.__dict__.items()
    if callable(function) and hasattr(function, "__code__")
)
