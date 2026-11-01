"""Finite original readers; qualified production uses the independent v3 store."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .history_observed_dataset import require_snapshot, snapshot_identity
from .history_qualified_forecast_v3 import HistoryQualifiedForecastV3Service

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryForecastDispatch41Service:
    def __init__(self, writer):
        if type(writer) is not HistoryQualifiedForecastV3Service:
            raise ValueError("history41_actual_qualified_writer_required")
        self.writer, self.legacy = writer, writer.legacy
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
        self._identity = (
            writer,
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
                self.writer,
                self.writer.legacy,
                self.writer.models,
                self.writer.originals,
                self.writer.commands,
                self.writer.owner_claim,
                self.writer.grants,
                self.writer.archive,
            )
            != self._identity
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or any(
                getattr(HistoryForecastDispatch41Service, name) is not function
                or function.__code__ is not code
                for name, function, code in LOADED_METHODS
            )
        ):
            raise ValueError("history41_dispatch_loaded_source_or_dependency_changed")
        return self.writer._sources(expected)

    def read(self, session, identifier):
        boundary = snapshot_identity(session)
        value = self.legacy.writer._saved_identity(session, identifier)
        old, release39, release41 = (
            self.legacy.legacy._sources(),
            self.legacy._sources(),
            self._sources(),
        )
        if value.source_files in (old, release39):
            result = self._read_legacy(session, identifier)
        elif value.source_files == release41:
            result = self.writer.read(session, identifier)
        else:
            raise ValueError("history41_unknown_or_mixed_producer_source")
        self._sources(release41)
        require_snapshot(session, boundary)
        return result

    def _read_legacy(self, session, identifier):
        return self.legacy.read(session, identifier)

    def authenticate_model(self, scope_id, *, checkpoint=None):
        self._sources()
        return self.writer.authenticate_model(scope_id, checkpoint=checkpoint)

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
    for name, function in HistoryForecastDispatch41Service.__dict__.items()
    if callable(function) and hasattr(function, "__code__")
)
