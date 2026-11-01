"""Independent future-outcome originals and bounded physical-RO readers.

This service neither produces a forecast nor downloads a candle. Acceptance is
an append-only receipt issued after a saved subject is read on another physical
connection. Fast status is explicitly unverified metadata.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from time import monotonic

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ..decision.history_outcome_score_v2 import OutcomeScoreInputsV2, calculate_outcome_score_v2
from ..decision.path_risk import compute_path_risk
from ..persistence.db import as_utc
from ..persistence.history_dataset_model_v2_records import HistoryDatasetModelV2Record as ModelRow
from ..persistence.history_outcome_v2_records import (
    HistoryOutcomeObservationV2Record as Observation,
)
from ..persistence.history_outcome_v2_records import (
    HistoryOutcomeOpportunityV2Record as Opportunity,
)
from ..persistence.history_outcome_v2_records import (
    HistoryOutcomeProtocolV2Record as Protocol,
)
from ..persistence.history_outcome_v2_records import (
    HistoryOutcomePublicationV2Record as Publication,
)
from ..persistence.history_outcome_v2_records import (
    HistoryOutcomeScoreV2Record as Score,
)
from ..persistence.risk_records import MarketCandleRecord as Candle
from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_outcome_v2 import (
    LABEL_STATES,
    PUBLICATION_STATES,
    SCOPES,
    HistoryOutcomeAcceptanceV2,
    HistoryOutcomeObservationV2,
    HistoryOutcomeOpportunityV2,
    HistoryOutcomeProtocolV2,
    HistoryOutcomePublicationV2,
    HistoryOutcomeScoreV2,
    OutcomeModelGrantV2,
    OutcomeOriginalRefV2,
    seal_outcome,
    verify_outcome,
)
from ..ports.market_path import canonical_hash, spot_scope
from .history_forecast_dispatch39 import HistoryForecastDispatch39Service
from .history_forecast_transfer39 import TransferAuthority39, database_identity, native_equal39
from .history_observed_dataset import (
    HistoryObservedArchive,
    encoded,
    require_snapshot,
    snapshot_identity,
)
from .history_outcome_forecasts_v2 import HistoryOutcomeForecastsV2Authenticator
from .history_outcome_originals_v2 import HistoryOutcomeCaptureAuthorityV2, load_window

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE_FILES = (
    *tuple(
        "packages/kernel/decision_hub_kernel/" + path
        for path in (
            "ports/history_outcome_v2.py",
            "decision/history_outcome_score_v2.py",
            "decision/path_risk.py",
            "persistence/history_outcome_v2_records.py",
            "application/history_outcome_v2.py",
            "application/history_outcome_forecasts_v2.py",
            "application/history_outcome_originals_v2.py",
            "application/history_outcome_jobs_v2.py",
        )
    ),
    "packages/kernel/decision_hub_kernel/application/history_outcome_recovery_v2.py",
    "apps/hub_worker/history_outcome_runtime40.py",
    "apps/hub_api/history_outcome_bootstrap41.py",
    "apps/hub_worker/history_outcome_cli41.py",
)
TYPES = {
    "protocol": (Protocol, HistoryOutcomeProtocolV2, "protocol_id"),
    "opportunity": (Opportunity, HistoryOutcomeOpportunityV2, "opportunity_id"),
    "publication": (Publication, HistoryOutcomePublicationV2, "publication_id"),
    "observation": (Observation, HistoryOutcomeObservationV2, "observation_id"),
    "score": (Score, HistoryOutcomeScoreV2, "score_id"),
}


class HistoryOutcomeCommitUnconfirmed(ValueError):
    automatic_retry = False


@dataclass(frozen=True, slots=True)
class OutcomePreparedV2:
    issuer: object
    thread_id: int
    database: str
    source_files: tuple
    entries: tuple  # (kind, exact immutable DTO, archive ref/hash)
    dependencies: tuple  # full native outcome records, immutable tuples
    confirmation: tuple | None
    authentication: bytes


def _id(value):
    if type(value) is HistoryOutcomeAcceptanceV2:
        return value.acceptance_id
    return getattr(value, TYPES[value.schema_version.split("-")[-1].split(".")[0]][2])


def _ref(value, row):
    return OutcomeOriginalRefV2(
        identifier=_id(value),
        payload_hash=value.payload_hash,
        original_ref=row.original_ref,
        original_hash=row.original_hash,
    )


def _native(row):
    return tuple(
        (
            column.name,
            (
                None
                if getattr(row, column.name) is None
                else as_utc(getattr(row, column.name)).isoformat()
            )
            if column.type.__class__.__name__ == "DateTime"
            else getattr(row, column.name),
        )
        for column in row.__table__.columns
    )


class HistoryOutcomeV2Service:
    def __init__(self, forecasts, *, hourly=None, qualified_forecasts=None):
        if type(forecasts) is not HistoryForecastDispatch39Service:
            raise ValueError("history_outcome_actual_forecast_dispatch_required")
        if qualified_forecasts is not None:
            from .history_forecast_dispatch41 import HistoryForecastDispatch41Service

            if (
                type(qualified_forecasts) is not HistoryForecastDispatch41Service
                or qualified_forecasts.legacy is not forecasts
            ):
                raise ValueError("history_outcome_exact_qualified_sidecar_required")
        self.forecasts = forecasts
        self.qualified_forecasts = qualified_forecasts
        self.originals, self.commands, self.owner_claim = (
            forecasts.originals,
            forecasts.commands,
            forecasts.owner_claim,
        )
        self.hourly = hourly
        self.archive = HistoryObservedArchive(self.originals.data_directory / "history-outcome-v2")
        self.key = secrets.token_bytes(32)
        self._issued = {}
        self._local = threading.local()
        self._identity = (
            forecasts,
            self.originals,
            self.commands,
            self.owner_claim,
            hourly,
            qualified_forecasts,
        )

    def now(self):
        return self.originals.now()

    @contextmanager
    def _independent_read_session(self):
        # NullPool makes this a different physical DBAPI connection from both
        # the preparer and writer. A new ORM Session alone cannot prove that.
        engine = create_engine(self.commands.database.engine.url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA query_only=ON")
                connection.exec_driver_sql("BEGIN")
                with Session(bind=connection, expire_on_commit=False) as session:
                    session.connection()
                    boundary = snapshot_identity(session)
                    yield session
                    require_snapshot(session, boundary)
                connection.rollback()
        finally:
            engine.dispose()

    def _sources(self, expected=None):
        if self._identity != (
            self.forecasts,
            self.forecasts.originals,
            self.forecasts.commands,
            self.forecasts.owner_claim,
            self.hourly,
            self.qualified_forecasts,
        ):
            raise ValueError("history_outcome_dependency_changed")
        if any(
            getattr(HistoryOutcomeV2Service, name) is not fn or fn.__code__ is not code
            for name, fn, code in LOADED_METHODS
        ):
            raise ValueError("history_outcome_loaded_callable_changed")
        sources = tuple(
            dict(
                (
                    *self.forecasts._sources(),
                    *(
                        self.qualified_forecasts._sources()
                        if self.qualified_forecasts is not None
                        else ()
                    ),
                    *(
                        (
                            path,
                            hashlib.sha256(
                                (self.originals.source_root / path).read_bytes()
                            ).hexdigest(),
                        )
                        for path in SOURCE_FILES
                    ),
                )
            ).items()
        )
        if self.qualified_forecasts is not None:
            from . import history_outcome_qualified41 as bridge

            digest = hashlib.sha256(
                (self.originals.source_root / bridge.SOURCE_FILE).read_bytes()
            ).hexdigest()
            if digest != bridge.LOADED_SOURCE_SHA256:
                raise ValueError("history_outcome_qualified_bridge_loaded_source_changed")
            sources = tuple(dict((*sources, (bridge.SOURCE_FILE, digest))).items())
        if dict(sources)[SOURCE_FILES[4]] != LOADED_SOURCE_SHA256:
            raise ValueError("history_outcome_loaded_source_changed")
        for path, digest in sources:
            module = sys.modules.get(path.removesuffix(".py").replace("/", "."))
            if (
                module is not None
                and hasattr(module, "LOADED_SOURCE_SHA256")
                and module.LOADED_SOURCE_SHA256 != digest
            ):
                raise ValueError("history_outcome_loaded_dependency_source_changed")
        if expected is not None and sources != expected:
            raise ValueError("history_outcome_source_changed")
        return sources

    def _qualified_reader(self):
        from .history_outcome_qualified41 import HistoryOutcomeQualified41Authenticator

        return HistoryOutcomeQualified41Authenticator(self.qualified_forecasts)

    def _model_bytes(self, session, scope_id, model_id, check=None):
        if self.forecasts.grants.get(scope_id) != model_id:
            raise ValueError("history_outcome_exact_model_server_grant_required")
        if self.qualified_forecasts is not None:
            return self._qualified_reader().issue_model(session, scope_id, checkpoint=check)
        issued = TransferAuthority39(self.forecasts.writer).issue(session, model_id)
        if type(issued) is not tuple or len(issued) != 2:
            raise ValueError("history_outcome_actual_model_issuer_reply_required")
        proof, model = issued
        if (
            type(model) is not HistoryDatasetFitModelV2
            or proof.model_bytes != encoded(model.model_dump(mode="json"))
            or model.model_id != model_id
            or model.scope_id != scope_id
            or model.owner_claim != self.owner_claim
        ):
            raise ValueError("history_outcome_exact_issued_model_reply_changed")
        return proof.model_bytes

    def _read_forecasts(self, session, identifiers, *, check, capture_cache=None):
        reader = (
            HistoryOutcomeForecastsV2Authenticator(self.forecasts)
            if self.qualified_forecasts is None
            else self._qualified_reader()
        )
        return reader.read_many(session, identifiers, checkpoint=check, capture_cache=capture_cache)

    def _source(self, sources):
        return self.archive.put(
            {
                "schema_version": "history-outcome-source.v2",
                "files": [
                    {
                        "path": path,
                        "sha256": digest,
                        "bytes_base64": base64.b64encode(
                            (self.originals.source_root / path).read_bytes()
                        ).decode(),
                    }
                    for path, digest in sources
                ],
            }
        )

    def _common(self, request_id, sources=None, source=None):
        sources = sources or self._sources()
        source = source or self._source(sources)
        clock = self.now()
        return dict(
            owner_claim=self.owner_claim,
            request_id=request_id,
            source_files=sources,
            source_original_ref=source[0],
            source_original_hash=source[1],
            produced_at=clock,
            recorded_at=clock,
            fixture_mode=self.originals.fixture_mode,
        )

    @staticmethod
    def _check(budget_seconds, checkpoint=None):
        if type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 120:
            raise ValueError("history_outcome_maximum_120_seconds_required")
        deadline = monotonic() + budget_seconds

        def check():
            if checkpoint is not None:
                checkpoint()
            if monotonic() >= deadline:
                raise TimeoutError("history_outcome_read_deadline_elapsed")

        return check

    def _source_read(self, value):
        self._sources(value.source_files)
        source = json.loads(
            self.archive.read(value.source_original_ref, value.source_original_hash)
        )
        if (
            source.get("schema_version") != "history-outcome-source.v2"
            or tuple((item["path"], item["sha256"]) for item in source["files"])
            != value.source_files
        ):
            raise ValueError("history_outcome_source_original_identity_invalid")
        for item in source["files"]:
            if (
                hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
                != item["sha256"]
            ):
                raise ValueError("history_outcome_source_original_bytes_changed")
        self._retain(value.source_original_ref, value.source_original_hash)
        for path, digest in value.source_files:
            self._retain(str(self.originals.source_root / path), digest)

    def _retain(self, reference, digest):
        retained = getattr(self._local, "files", None)
        if retained is not None:
            previous = retained.setdefault(reference, digest)
            if previous != digest:
                raise ValueError("history_outcome_conflicting_original_leaf")

    @contextmanager
    def _files_guard(self, check):
        previous = getattr(self._local, "files", None)
        retained = {} if previous is None else previous
        self._local.files = retained
        try:
            yield
            for reference, digest in tuple(retained.items()):
                check()
                if hashlib.sha256(Path(reference).read_bytes()).hexdigest() != digest:
                    raise ValueError("history_outcome_original_changed_at_exit")
                check()
        finally:
            self._local.files = previous

    def _read(self, session, kind, identifier):
        self._schemas(session)
        record, contract, key = TYPES[kind]
        row = session.get(record, identifier, populate_existing=True)
        if row is None:
            raise KeyError(identifier)
        if kind in ("publication", "observation") and row.kind == "acceptance":
            contract = HistoryOutcomeAcceptanceV2
        value = verify_outcome(contract.model_validate_json(row.payload_json))
        body = self.archive.read(row.original_ref, row.original_hash)
        self._retain(row.original_ref, row.original_hash)
        if (
            body != row.payload_json.encode()
            or row.owner_claim != self.owner_claim
            or row.owner_claim != value.owner_claim
            or row.request_id != value.request_id
            or row.payload_hash != value.payload_hash
            or getattr(row, key) != _id(value)
            or as_utc(row.produced_at) != value.produced_at
            or as_utc(row.recorded_at) != value.recorded_at
            or row.fixture_mode is not value.fixture_mode
            or value.fixture_mode is not self.originals.fixture_mode
            or json.loads(row.source_files_json) != [list(item) for item in value.source_files]
            or row.source_original_ref != value.source_original_ref
            or row.source_original_hash != value.source_original_hash
        ):
            raise ValueError("history_outcome_saved_original_changed")
        self._source_read(value)
        expected_row = self._record(kind, value, row.original_ref, row.original_hash)
        mutable = {
            "protocol": {
                "confirmed_at",
                "confirmation_id",
                "confirmation_original_ref",
                "confirmation_original_hash",
                "confirmation_payload_hash",
            },
            "opportunity": {
                "label_cutoff_at",
                "label_anchor_basis",
                "first_publication_id",
                "first_accepted_observation_id",
                "first_acceptance_id",
                "state",
                "attempts",
                "generation",
                "lease_owner",
                "lease_expires_at",
                "next_due_at",
                "label_deadline_at",
                "error_code",
            },
        }.get(kind, set())
        if type(value) is HistoryOutcomeAcceptanceV2 and kind == "observation":
            subject = session.get(Observation, value.subject.identifier, populate_existing=True)
            if subject is None or subject.kind != "observation":
                raise ValueError("history_outcome_acceptance_subject_missing")
            for name in (
                "publication_id",
                "scope_id",
                "attempt",
                "classification",
                "reason_code",
                "label_cutoff_at",
                "window_start_at",
                "window_end_at",
                "label_deadline_at",
                "observed_at",
            ):
                setattr(expected_row, name, getattr(subject, name))
        columns = tuple(column.name for column in row.__table__.columns)
        physical_values = dict(
            zip(
                columns,
                tuple(
                    session.connection()
                    .exec_driver_sql(
                        f'SELECT {",".join(columns)} FROM "{record.__tablename__}" WHERE "{key}"=?',
                        (identifier,),
                    )
                    .one()
                ),
                strict=True,
            )
        )
        for column in row.__table__.columns:
            if column.name in mutable:
                continue
            actual, expected = getattr(row, column.name), getattr(expected_row, column.name)
            if column.type.__class__.__name__ == "DateTime":
                actual = None if actual is None else as_utc(actual).isoformat()
                expected = None if expected is None else as_utc(expected).isoformat()
            if not native_equal39(actual, expected):
                raise ValueError("history_outcome_original_metadata_projection_changed")
            # ORM coercion must not hide SQLite TEXT/REAL masquerading as an
            # integer/boolean header. Bind the physical value as well.
            physical = physical_values[column.name]
            if column.type.__class__.__name__ == "DateTime":
                expected = (
                    None
                    if getattr(expected_row, column.name) is None
                    else as_utc(getattr(expected_row, column.name))
                    .replace(tzinfo=None)
                    .strftime("%Y-%m-%d %H:%M:%S.%f")
                )
            elif column.type.__class__.__name__ == "Boolean":
                expected = int(expected)
            if not native_equal39(physical, expected):
                raise ValueError("history_outcome_complete_native_projection_changed")
        return value, _ref(value, row), row

    @staticmethod
    def _schemas(session):
        result = []
        for record, _, _ in TYPES.values():
            columns = tuple(
                tuple(row)
                for row in session.connection()
                .exec_driver_sql(f'PRAGMA table_xinfo("{record.__tablename__}")')
                .all()
            )
            if tuple(row[1] for row in columns) != tuple(
                column.name for column in record.__table__.columns
            ):
                raise ValueError("history_outcome_complete_schema_invalid")
            for physical, declared in zip(columns, record.__table__.columns, strict=True):
                if (
                    physical[2] != declared.type.compile(dialect=session.connection().dialect)
                    or physical[6] != 0
                    or physical[5] != int(declared.primary_key)
                ):
                    raise ValueError("history_outcome_complete_schema_projection_invalid")
            result.append((record.__tablename__, columns))
        return tuple(result)

    def _opportunities(self, session, protocol):
        rows = session.scalars(
            select(Opportunity)
            .where(
                Opportunity.protocol_id == protocol.protocol_id,
                Opportunity.owner_claim == self.owner_claim,
            )
            .order_by(Opportunity.hour_at, Opportunity.scope_id)
        ).all()
        expected = [
            (protocol.first_hour + timedelta(hours=i), scope) for i in range(24) for scope in SCOPES
        ]
        by_key = {(as_utc(row.hour_at), row.scope_id): row for row in rows}
        if len(rows) != 48 or set(by_key) != set(expected):
            raise ValueError("history_outcome_exact_48_enrollment_required")
        return tuple(by_key[key] for key in expected)

    def _prepare(self, session, entries, dependencies=(), confirmation=None):
        sources = self._sources()
        stored = tuple(
            (kind, value, *self.archive.put(value.model_dump(mode="json")))
            for kind, value in entries
        )
        packet = OutcomePreparedV2(
            self,
            threading.get_ident(),
            database_identity(session),
            sources,
            stored,
            tuple(dependencies),
            confirmation,
            b"",
        )
        digest = self._mac(packet)
        packet = replace(packet, authentication=digest)
        self._issued[id(packet)] = packet
        return packet

    def _mac(self, packet):
        return hmac.digest(
            self.key,
            encoded(
                [
                    packet.thread_id,
                    packet.database,
                    packet.source_files,
                    [
                        (kind, value.model_dump(mode="json"), ref, digest)
                        for kind, value, ref, digest in packet.entries
                    ],
                    [
                        [
                            table,
                            identifier,
                            [
                                [key, (value.isoformat() if hasattr(value, "isoformat") else value)]
                                for key, value in native
                            ],
                        ]
                        for table, identifier, native in packet.dependencies
                    ],
                    packet.confirmation,
                ]
            ),
            "sha256",
        )

    def _auth(self, session, packet):
        if (
            type(packet) is not OutcomePreparedV2
            or packet.issuer is not self
            or self._issued.get(id(packet)) is not packet
            or packet.thread_id != threading.get_ident()
            or packet.database != database_identity(session)
            or not hmac.compare_digest(packet.authentication, self._mac(packet))
        ):
            raise ValueError("history_outcome_issued_preparation_required")
        self._sources(packet.source_files)

    def _record(self, kind, value, ref, digest):
        record, _contract, key = TYPES[kind]
        common = dict(
            owner_claim=value.owner_claim,
            request_id=value.request_id,
            payload_json=encoded(value.model_dump(mode="json")).decode(),
            payload_hash=value.payload_hash,
            original_ref=ref,
            original_hash=digest,
            source_files_json=encoded(value.source_files).decode(),
            source_original_ref=value.source_original_ref,
            source_original_hash=value.source_original_hash,
            produced_at=value.produced_at,
            recorded_at=value.recorded_at,
            fixture_mode=value.fixture_mode,
        )
        common[key] = _id(value)
        if kind == "protocol":
            common.update(
                registered_at=value.registered_at,
                first_hour=value.first_hour,
                last_hour=value.last_hour,
                opportunity_count=48,
            )
        elif kind == "opportunity":
            common.update(
                protocol_id=value.protocol.identifier,
                scope_id=value.scope_id,
                model_id=value.model.identifier,
                hour_at=value.hour_at,
                registered_at=value.registered_at,
                dispatch_deadline_at=value.dispatch_deadline_at,
                state="pending",
                attempts=0,
                generation=0,
                next_due_at=value.dispatch_deadline_at,
            )
        elif type(value) is HistoryOutcomeAcceptanceV2:
            common.update(
                protocol_id=value.protocol.identifier,
                opportunity_id=None if value.opportunity is None else value.opportunity.identifier,
                kind="acceptance",
                accepted_kind=value.accepted_kind,
                subject_id=value.subject.identifier,
                independently_verified_at=value.independently_verified_at,
            )
        elif kind == "publication":
            common.update(
                protocol_id=value.protocol.identifier,
                opportunity_id=value.opportunity.identifier,
                scope_id=value.scope_id,
                model_id=value.model.identifier,
                hour_at=value.hour_at,
                kind="publication",
                binding_version=1,
                classification=value.classification,
                reason_code=value.reason_code,
                dispatch_slot_id=None if value.dispatch is None else value.dispatch.identifier,
                forecast_id=None if value.forecast is None else value.forecast.identifier,
                actual_cutoff_at=value.actual_cutoff_at,
                label_cutoff_at=value.label_cutoff_at,
            )
        elif kind == "observation":
            common.update(
                protocol_id=value.protocol.identifier,
                opportunity_id=value.opportunity.identifier,
                publication_id=value.publication.identifier,
                scope_id=value.scope_id,
                kind="observation",
                attempt=value.attempt,
                classification=value.classification,
                reason_code=value.reason_code,
                label_cutoff_at=value.label_cutoff_at,
                window_start_at=value.window_start_at,
                window_end_at=value.window_end_at,
                label_deadline_at=value.label_deadline_at,
                observed_at=value.observed_at,
            )
        else:
            common.update(protocol_id=value.protocol.identifier, actual_as_of=value.actual_as_of)
        return record(**common)

    def save(self, packet, *, checkpoint=None):
        """One attempt, durable unknown fence, then independent readback; no replay."""
        key = canonical_hash(
            [self.owner_claim, [value.request_id for _, value, _, _ in packet.entries]]
        )
        path = self.archive.root / "fences" / (key + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        body = encoded(
            {
                "schema_version": "history-outcome-commit-fence.v2",
                "owner": self.owner_claim,
                "entries": [
                    (kind, _id(value), digest) for kind, value, _, digest in packet.entries
                ],
            }
        )
        try:
            with path.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise HistoryOutcomeCommitUnconfirmed(
                "history_outcome_fence_requires_readonly_reconciliation"
            ) from None
        committed = False
        try:
            if checkpoint is not None:
                checkpoint()
            with self.commands._write_session() as session:
                self._auth(session, packet)
                for table, identifier, expected in packet.dependencies:
                    row = session.get(
                        dict((r.__tablename__, r) for r, _, _ in TYPES.values())[table],
                        identifier,
                        populate_existing=True,
                    )
                    if row is None or not native_equal39(_native(row), expected):
                        raise ValueError("history_outcome_fresh_dependency_changed")
                for kind, value, ref, digest in packet.entries:
                    record, _, _ = TYPES[kind]
                    if session.get(record, _id(value)) is not None:
                        raise ValueError("history_outcome_first_save_conflict")
                    if self.archive.read(ref, digest) != encoded(value.model_dump(mode="json")):
                        raise ValueError("history_outcome_prepared_original_changed")
                    self._source_read(value)
                    row = self._record(kind, value, ref, digest)
                    if type(value) is HistoryOutcomeAcceptanceV2 and kind == "observation":
                        subject = session.get(
                            Observation, value.subject.identifier, populate_existing=True
                        )
                        for name in (
                            "publication_id",
                            "scope_id",
                            "attempt",
                            "classification",
                            "reason_code",
                            "label_cutoff_at",
                            "window_start_at",
                            "window_end_at",
                            "label_deadline_at",
                            "observed_at",
                        ):
                            setattr(row, name, getattr(subject, name))
                    session.add(row)
                self._bind(session, packet)
                session.flush()
                self._sources(packet.source_files)
                if checkpoint is not None:
                    checkpoint()
            committed = True
            with self._independent_read_session() as session:
                self._auth(session, packet)
                for kind, value, _ref_value, _digest in packet.entries:
                    saved, _, _ = self._read(session, kind, _id(value))
                    if saved != value:
                        raise ValueError("history_outcome_saved_readback_changed")
                if checkpoint is not None:
                    checkpoint()
            if path.read_bytes() != body:
                raise ValueError("history_outcome_fence_changed")
            path.unlink()
            return tuple(value for _, value, _, _ in packet.entries)
        except BaseException as error:
            raise HistoryOutcomeCommitUnconfirmed(
                "history_outcome_committed_readback_unconfirmed"
                if committed
                else "history_outcome_commit_or_rollback_unconfirmed"
            ) from error
        finally:
            self._issued.pop(id(packet), None)

    def _bind(self, session, packet):
        if packet.confirmation is None:
            return
        mode, identifier, subject_id, acceptance_id = packet.confirmation
        if mode == "protocol":
            row = session.get(Protocol, identifier, populate_existing=True)
            receipt = next(
                value for _, value, _, _ in packet.entries if _id(value) == acceptance_id
            )
            original = next(
                (ref, digest) for _, value, ref, digest in packet.entries if value is receipt
            )
            if row.confirmation_id is not None or receipt.independently_verified_at >= as_utc(
                row.first_hour
            ):
                raise ValueError("history_outcome_registration_confirmation_late_or_conflict")
            row.confirmed_at = receipt.independently_verified_at
            row.confirmation_id = receipt.acceptance_id
            row.confirmation_original_ref, row.confirmation_original_hash = original
            row.confirmation_payload_hash = receipt.payload_hash
        else:
            row = session.get(Opportunity, identifier, populate_existing=True)
            if mode == "publication":
                value = next(value for _, value, _, _ in packet.entries if _id(value) == subject_id)
                if row.first_publication_id is not None:
                    raise ValueError("history_outcome_first_publication_conflict")
                row.first_publication_id = subject_id
                row.label_cutoff_at = value.label_cutoff_at
                row.label_anchor_basis = value.label_anchor_basis
                row.label_deadline_at = value.label_cutoff_at + timedelta(minutes=1, days=2)
                row.next_due_at = value.label_cutoff_at + timedelta(minutes=1, days=1, seconds=120)
                row.state = "ready"
            else:
                if row.first_acceptance_id is not None:
                    raise ValueError("history_outcome_first_label_conflict")
                row.first_accepted_observation_id, row.first_acceptance_id = (
                    subject_id,
                    acceptance_id,
                )
                row.state, row.lease_owner, row.lease_expires_at = "completed", None, None

    def register(self, request_id, *, budget_seconds=120, checkpoint=None):
        """Server-granted two models; actual next hour is derived after deep read."""
        check = self._check(budget_seconds, checkpoint)
        deadline = monotonic() + budget_seconds
        check()
        self._sources()
        with self.commands._read_session() as session:
            existing = session.scalar(
                select(Protocol).where(
                    Protocol.owner_claim == self.owner_claim, Protocol.request_id == request_id
                )
            )
            if existing is not None:
                return self._read(session, "protocol", existing.protocol_id)[0]
            grants = []
            for scope in SCOPES:
                model_id = self.forecasts.grants.get(scope)
                if model_id is None:
                    raise ValueError("history_outcome_two_trusted_models_required")
                model = HistoryDatasetFitModelV2.model_validate_json(
                    self._model_bytes(session, scope, model_id, check)
                )
                check()
                row = session.get(ModelRow, model_id, populate_existing=True)
                grants.append(
                    OutcomeModelGrantV2(
                        scope_id=scope,
                        model=OutcomeOriginalRefV2(
                            identifier=model_id,
                            payload_hash=model.payload_hash,
                            original_ref=row.original_ref,
                            original_hash=row.original_hash,
                        ),
                        dataset=OutcomeOriginalRefV2(
                            identifier=model.dataset_id,
                            payload_hash=model.dataset_hash,
                            original_ref=model.dataset_original_ref,
                            original_hash=model.dataset_original_hash,
                        ),
                        model_known_at=model.model_known_at,
                    )
                )
            clock = self.now()
            first = clock.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            if (first - clock).total_seconds() < 120:
                first += timedelta(hours=1)
            common = self._common(request_id)
            protocol = seal_outcome(
                HistoryOutcomeProtocolV2,
                **common,
                protocol_id="heval2_" + canonical_hash([self.owner_claim, request_id])[:48],
                registered_at=clock,
                first_hour=first,
                last_hour=first + timedelta(hours=23),
                model_grants=tuple(grants),
            )
            ref, digest = self.archive.reference(protocol.model_dump(mode="json"))
            protocol_ref = OutcomeOriginalRefV2(
                identifier=protocol.protocol_id,
                payload_hash=protocol.payload_hash,
                original_ref=ref,
                original_hash=digest,
            )
            entries = [("protocol", protocol)]
            for i in range(24):
                for grant in grants:
                    hour = first + timedelta(hours=i)
                    entries.append(
                        (
                            "opportunity",
                            seal_outcome(
                                HistoryOutcomeOpportunityV2,
                                **self._common(
                                    request_id
                                    + ":"
                                    + str(i)
                                    + ":"
                                    + str(SCOPES.index(grant.scope_id)),
                                    common["source_files"],
                                    (common["source_original_ref"], common["source_original_hash"]),
                                ),
                                opportunity_id="hopportunity2_"
                                + canonical_hash(
                                    [protocol.protocol_id, grant.scope_id, hour.isoformat()]
                                )[:48],
                                protocol=protocol_ref,
                                scope_id=grant.scope_id,
                                model=grant.model,
                                hour_at=hour,
                                registered_at=clock,
                                dispatch_deadline_at=hour + timedelta(minutes=5),
                            ),
                        )
                    )
            packet = self._prepare(session, tuple(entries))
        check()
        self.save(packet, checkpoint=check)
        self.confirm(
            "protocol",
            protocol.protocol_id,
            budget_seconds=deadline - monotonic(),
            checkpoint=check,
        )
        return protocol

    def confirm(self, kind, identifier, *, budget_seconds=120, checkpoint=None):
        """Issuer derives S only after full stored-subject validation on fresh RO."""
        check = self._check(budget_seconds, checkpoint)
        with self._independent_read_session() as session:
            check()
            value, reference, row = self._read(session, kind, identifier)
            if kind == "protocol":
                if row.confirmation_id is not None:
                    return self._read(session, "publication", row.confirmation_id)[0]
                for grant in value.model_grants:
                    model = HistoryDatasetFitModelV2.model_validate_json(
                        self._model_bytes(session, grant.scope_id, grant.model.identifier, check)
                    )
                    check()
                    model_row = session.get(
                        ModelRow, grant.model.identifier, populate_existing=True
                    )
                    if (
                        model.payload_hash != grant.model.payload_hash
                        or model.model_known_at != grant.model_known_at
                        or model.owner_claim != self.owner_claim
                        or model.scope_id != grant.scope_id
                        or (model_row.original_ref, model_row.original_hash)
                        != (grant.model.original_ref, grant.model.original_hash)
                        or (
                            model.dataset_id,
                            model.dataset_hash,
                            model.dataset_original_ref,
                            model.dataset_original_hash,
                        )
                        != (
                            grant.dataset.identifier,
                            grant.dataset.payload_hash,
                            grant.dataset.original_ref,
                            grant.dataset.original_hash,
                        )
                    ):
                        raise ValueError("history_outcome_registration_model_changed")
                for opportunity in self._opportunities(session, value):
                    saved, saved_ref, _ = self._read(
                        session, "opportunity", opportunity.opportunity_id
                    )
                    expected_grant = next(
                        grant for grant in value.model_grants if grant.scope_id == saved.scope_id
                    )
                    if (
                        saved.protocol != reference
                        or saved.model != expected_grant.model
                        or saved.registered_at != value.registered_at
                        or saved_ref.identifier != opportunity.opportunity_id
                    ):
                        raise ValueError("history_outcome_enrollment_original_binding_changed")
                protocol_ref, opportunity_ref = reference, None
            elif kind == "observation":
                if value.classification not in ("verified", "late_diagnostic"):
                    raise ValueError("history_outcome_incomplete_label_confirmation_forbidden")
                self.read_observation(
                    session, identifier, budget_seconds=budget_seconds, checkpoint=check
                )
                protocol_ref, opportunity_ref = value.protocol, value.opportunity
            else:
                raise ValueError("history_outcome_subject_confirmation_kind_invalid")
            evidence = {
                "schema_version": "history-outcome-independent-readback.v2",
                "subject": reference.model_dump(mode="json"),
                "owner": self.owner_claim,
                "verification_mode": "independent-physical-ro-snapshot.v1",
                "source_files": self._sources(),
            }
            observed = self.now()  # after stored subject and all dependencies passed
            check()
            evidence["independently_verified_at"] = observed.isoformat()
            evidence_ref, evidence_hash = self.archive.put(evidence)
            receipt = seal_outcome(
                HistoryOutcomeAcceptanceV2,
                **self._common("confirm:" + identifier),
                acceptance_id="hacceptance2_"
                + canonical_hash([self.owner_claim, kind, identifier])[:48],
                protocol=protocol_ref,
                opportunity=opportunity_ref,
                accepted_kind=kind,
                subject=reference,
                subject_recorded_at=value.recorded_at,
                independently_verified_at=observed,
                readback_evidence_ref=evidence_ref,
                readback_evidence_hash=evidence_hash,
            )
            dependencies = [(row.__tablename__, identifier, _native(row))]
            if kind == "observation":
                opportunity = session.get(
                    Opportunity, opportunity_ref.identifier, populate_existing=True
                )
                dependencies.append(
                    (opportunity.__tablename__, opportunity.opportunity_id, _native(opportunity))
                )
            packet = self._prepare(
                session,
                (("publication" if kind == "protocol" else "observation", receipt),),
                dependencies,
                (
                    kind,
                    identifier if kind == "protocol" else opportunity_ref.identifier,
                    identifier,
                    receipt.acceptance_id,
                ),
            )
        self.save(packet, checkpoint=check)
        return receipt

    def _acceptance(self, session, identifier, kind="observation"):
        value, reference, row = self._read(session, kind, identifier)
        if type(value) is not HistoryOutcomeAcceptanceV2:
            raise ValueError("history_outcome_separate_acceptance_original_required")
        evidence = json.loads(
            self.archive.read(value.readback_evidence_ref, value.readback_evidence_hash)
        )
        self._retain(value.readback_evidence_ref, value.readback_evidence_hash)
        if (
            evidence.get("subject") != value.subject.model_dump(mode="json")
            or evidence.get("owner") != self.owner_claim
            or evidence.get("independently_verified_at")
            != value.independently_verified_at.isoformat()
            or evidence.get("verification_mode") != value.verification_mode
            or evidence.get("source_files") != [list(item) for item in value.source_files]
        ):
            raise ValueError("history_outcome_independent_confirmation_evidence_changed")
        return value, reference, row

    def prepare_publication(self, session, opportunity_id, *, budget_seconds=120, checkpoint=None):
        check = self._check(budget_seconds, checkpoint)
        opportunity, opportunity_ref, row = self._read(session, "opportunity", opportunity_id)
        protocol, protocol_ref, registration = self._read(
            session, "protocol", opportunity.protocol.identifier
        )
        if row.first_publication_id is not None:
            return None
        if registration.confirmation_id is None:
            raise ValueError("history_outcome_enrollment_not_independently_confirmed")
        self._acceptance(session, registration.confirmation_id, "publication")
        clock = self.now()
        slot_id = (
            "hhour2_"
            + canonical_hash(
                [self.owner_claim, opportunity.scope_id, opportunity.hour_at.isoformat()]
            )[:48]
        )
        if self.hourly is None:
            raise ValueError("history_outcome_trusted_hourly_reader_required")
        pair = self.hourly._read_slot(session, slot_id)
        actual, forecast_ref, dispatch_ref, readback = None, None, None, None
        classification, reason = "missing", "history_outcome_no_hourly_dispatch"
        if pair is not None:
            slot, receipt = pair
            if slot.status not in (
                "blocked",
                "committed_verified",
                "committed_readback_unconfirmed",
                "unconfirmed",
            ):
                return None
            actual = as_utc(slot.cutoff_at)
            dispatch_original = self.archive.put(receipt)
            dispatch_ref = OutcomeOriginalRefV2(
                identifier=slot_id,
                payload_hash=slot.receipt_hash,
                original_ref=dispatch_original[0],
                original_hash=dispatch_original[1],
            )
            if not opportunity.hour_at <= actual <= opportunity.dispatch_deadline_at:
                actual, classification, reason = (
                    None,
                    "off_schedule",
                    "history_outcome_dispatch_outside_frozen_grace",
                )
            elif slot.model_id != opportunity.model.identifier:
                classification, reason = "blocked", "history_outcome_protocol_model_not_dispatched"
            elif slot.status == "committed_verified" and slot.forecast_id is not None:
                forecasts = self._read_forecasts(session, (slot.forecast_id,), check=check)
                forecast = forecasts[0]
                if (
                    forecast.cutoff_at != actual
                    or forecast.model_hash != opportunity.model.payload_hash
                    or forecast.produced_at < as_utc(registration.confirmed_at)
                ):
                    raise ValueError("history_outcome_publication_forecast_binding_invalid")
                saved_row = self.forecasts.writer._saved_identity(session, slot.forecast_id)
                from ..persistence.history_dataset_forecast_v2_records import (
                    HistoryDatasetForecastV2Record,
                )

                forecast_row = session.get(HistoryDatasetForecastV2Record, slot.forecast_id)
                forecast_ref = OutcomeOriginalRefV2(
                    identifier=forecast.forecast_id,
                    payload_hash=saved_row.payload_hash,
                    original_ref=forecast_row.original_ref,
                    original_hash=forecast_row.original_hash,
                )
                readback = as_utc(slot.completed_at)
                classification = "on_time" if readback <= actual + timedelta(seconds=60) else "late"
                reason = "history_outcome_authenticated_hourly_readback"
            else:
                classification = "blocked" if slot.status == "blocked" else "unconfirmed"
                reason = slot.reason_code or "history_outcome_hourly_unconfirmed"
        elif clock < opportunity.dispatch_deadline_at:
            return None
        source = self._common("publication:" + opportunity_id)
        value = seal_outcome(
            HistoryOutcomePublicationV2,
            **source,
            publication_id="hpublication2_"
            + canonical_hash([self.owner_claim, opportunity_id])[:48],
            protocol=protocol_ref,
            opportunity=opportunity_ref,
            scope_id=opportunity.scope_id,
            model=opportunity.model,
            hour_at=opportunity.hour_at,
            registered_at=opportunity.registered_at,
            classification=classification,
            reason_code=reason,
            dispatch=dispatch_ref,
            forecast=forecast_ref,
            actual_cutoff_at=actual,
            label_cutoff_at=opportunity.hour_at if actual is None else actual,
            label_anchor_basis="scheduled_counterfactual"
            if actual is None
            else "actual_first_dispatch",
            verified_readback_at=readback,
            diagnosed_at=clock,
        )
        return self._prepare(
            session,
            (("publication", value),),
            ((row.__tablename__, opportunity_id, _native(row)),),
            ("publication", opportunity_id, value.publication_id, ""),
        )

    def _window_refs(self, session, scope, cutoff, known):
        return tuple(
            sorted(
                set(
                    session.scalars(
                        select(Candle.capture_ref).where(
                            Candle.scope_id == scope.scope_id,
                            Candle.open_at >= cutoff - timedelta(minutes=34),
                            Candle.open_at < cutoff + timedelta(days=1, minutes=1),
                            Candle.known_at <= known,
                        )
                    ).all()
                )
            )
        )

    def _path(self, session, value, cache, check):
        check()
        asset = "BTC-USDT" if value.scope_id == SCOPES[0] else "ETH-USDT"
        bars, source = load_window(
            session, spot_scope(asset), value.label_cutoff_at, value.observed_at, cache
        )
        risk = compute_path_risk(
            spot_scope(asset),
            bars,
            value.window_start_at,
            value.window_end_at,
            anchor_policy="scheduled_bar_open",
            as_of=value.observed_at,
        )
        body = self.archive.read(value.observation_original_ref, value.observation_original_hash)
        self._retain(value.observation_original_ref, value.observation_original_hash)
        if (
            body != encoded(source)
            or any(
                getattr(value, field) != getattr(risk, field)
                for field in (
                    "anchor_status",
                    "anchor_price",
                    "observed_count",
                    "expected_count",
                    "pending_count",
                    "window_status",
                    "mae_long",
                    "mae_short",
                )
            )
            or (value.path_semantic_hash, value.path_manifest_hash)
            != (risk.semantic_hash, risk.manifest_hash)
        ):
            raise ValueError("history_outcome_complete_label_original_or_math_changed")
        check()
        return value

    def prepare_observation(
        self, session, opportunity_id, *, attempt, budget_seconds=120, checkpoint=None
    ):
        check = self._check(budget_seconds, checkpoint)
        opportunity, opportunity_ref, row = self._read(session, "opportunity", opportunity_id)
        if row.first_publication_id is None or not 1 <= attempt <= 4 or row.attempts != attempt:
            raise ValueError("history_outcome_reserved_attempt_required")
        publication, publication_ref, _ = self._read(
            session, "publication", row.first_publication_id
        )
        clock = self.now()
        start = publication.label_cutoff_at + timedelta(minutes=1)
        end, deadline = start + timedelta(days=1), start + timedelta(days=2)
        if clock < end or clock > deadline:
            raise ValueError("history_outcome_observation_outside_frozen_clock")
        scope = spot_scope("BTC-USDT" if opportunity.scope_id == SCOPES[0] else "ETH-USDT")
        authority = HistoryOutcomeCaptureAuthorityV2(self.forecasts)
        cache = authority.issue(
            session,
            self._window_refs(session, scope, publication.label_cutoff_at, clock),
            checkpoint=check,
        )
        try:
            bars, source = load_window(session, scope, publication.label_cutoff_at, clock, cache)
            risk = compute_path_risk(
                scope, bars, start, end, anchor_policy="scheduled_bar_open", as_of=clock
            )
            authority.close(session, cache)
        finally:
            authority.abort()
        original = self.archive.put(source)
        complete = risk.window_status == "complete" and risk.anchor_status == "verified"
        value = seal_outcome(
            HistoryOutcomeObservationV2,
            **self._common("observation:" + opportunity_id + ":" + str(attempt)),
            observation_id="hobservation2_" + canonical_hash([opportunity_id, attempt])[:48],
            protocol=opportunity.protocol,
            opportunity=opportunity_ref,
            publication=publication_ref,
            scope_id=opportunity.scope_id,
            attempt=attempt,
            label_cutoff_at=publication.label_cutoff_at,
            window_start_at=start,
            window_end_at=end,
            label_deadline_at=deadline,
            observed_at=clock,
            accepted_at=None,
            classification="verified" if complete else "partial",
            reason_code="history_outcome_complete_1440"
            if complete
            else "history_outcome_incomplete_1440",
            anchor_status=risk.anchor_status,
            anchor_price=risk.anchor_price,
            observed_count=risk.observed_count,
            expected_count=1440,
            pending_count=risk.pending_count,
            window_status=risk.window_status,
            mae_long=risk.mae_long,
            mae_short=risk.mae_short,
            path_semantic_hash=risk.semantic_hash,
            path_manifest_hash=risk.manifest_hash,
            observation_original_ref=original[0],
            observation_original_hash=original[1],
        )
        return self._prepare(
            session, (("observation", value),), ((row.__tablename__, opportunity_id, _native(row)),)
        )

    def read_observation(self, session, identifier, *, budget_seconds=120, checkpoint=None):
        check = self._check(budget_seconds, checkpoint)
        with self._files_guard(check):
            return self._read_observation_body(
                session, identifier, budget_seconds=budget_seconds, checkpoint=check
            )

    def _read_observation_body(self, session, identifier, *, budget_seconds=120, checkpoint=None):
        check = self._check(budget_seconds, checkpoint)
        boundary = snapshot_identity(session)
        schema = self._schemas(session)
        value, _, _ = self._read(session, "observation", identifier)
        if type(value) is not HistoryOutcomeObservationV2:
            raise ValueError("history_outcome_label_subject_required")
        self._read(session, "opportunity", value.opportunity.identifier)
        self._read(session, "publication", value.publication.identifier)
        scope = spot_scope("BTC-USDT" if value.scope_id == SCOPES[0] else "ETH-USDT")
        authority = HistoryOutcomeCaptureAuthorityV2(self.forecasts)
        cache = authority.issue(
            session,
            self._window_refs(session, scope, value.label_cutoff_at, value.observed_at),
            checkpoint=check,
        )
        try:
            self._path(session, value, cache, check)
            authority.close(session, cache)
            self._source_read(value)
            if not native_equal39(self._schemas(session), schema):
                raise ValueError("history_outcome_complete_schema_changed")
            require_snapshot(session, boundary)
            check()
            return value
        finally:
            authority.abort()

    def _score_inputs(self, session, protocol_id, check, *, frozen_cases=None, as_of=None):
        protocol, protocol_ref, registration = self._read(session, "protocol", protocol_id)
        acceptance, acceptance_ref = None, None
        if registration.confirmation_id is not None:
            acceptance, acceptance_ref, _ = self._acceptance(
                session, registration.confirmation_id, "publication"
            )
            if as_of is not None and acceptance.recorded_at > as_of:
                acceptance, acceptance_ref = None, None
        inputs, ids, label_refs = [], [], set()
        case_map = (
            None if frozen_cases is None else {case.opportunity_id: case for case in frozen_cases}
        )
        for row in self._opportunities(session, protocol):
            check()
            opportunity, opportunity_ref, _ = self._read(session, "opportunity", row.opportunity_id)
            publication = publication_ref = observation = observation_ref = confirmation = (
                confirmation_ref
            ) = None
            case = None if case_map is None else case_map[row.opportunity_id]
            publication_id = (
                row.first_publication_id
                if case is None
                else (None if case.publication is None else case.publication.identifier)
            )
            if publication_id is not None:
                publication, publication_ref, _ = self._read(session, "publication", publication_id)
                if case is not None and publication_ref != case.publication:
                    raise ValueError("history_outcome_saved_publication_reference_changed")
                self._verify_publication(session, publication)
                if publication.forecast is not None:
                    ids.append(publication.forecast.identifier)
            selected = (
                row.first_accepted_observation_id
                if case is None
                else (None if case.observation is None else case.observation.identifier)
            )
            if selected is None and case is None:
                latest = session.scalar(
                    select(Observation)
                    .where(
                        Observation.opportunity_id == row.opportunity_id,
                        Observation.kind == "observation",
                    )
                    .order_by(Observation.attempt.desc())
                    .limit(1)
                )
                selected = None if latest is None else latest.observation_id
            if selected is not None:
                observation, observation_ref, _ = self._read(session, "observation", selected)
                if case is not None and observation_ref != case.observation:
                    raise ValueError("history_outcome_saved_observation_reference_changed")
                source = json.loads(
                    self.archive.read(
                        observation.observation_original_ref, observation.observation_original_hash
                    )
                )
                label_refs.update(bar["capture_ref"] for bar in source["bars"])
                confirmation_id = (
                    row.first_acceptance_id
                    if case is None
                    else (None if case.acceptance is None else case.acceptance.identifier)
                )
                if confirmation_id is not None:
                    confirmation, confirmation_ref, _ = self._acceptance(session, confirmation_id)
                    if case is not None and confirmation_ref != case.acceptance:
                        raise ValueError("history_outcome_saved_acceptance_reference_changed")
            inputs.append(
                OutcomeScoreInputsV2(
                    opportunity,
                    opportunity_ref,
                    publication,
                    publication_ref,
                    observation,
                    observation_ref,
                    None,
                    confirmation,
                    confirmation_ref,
                )
            )
        for identifier in ids:
            value = self.forecasts.writer._saved_identity(session, identifier)
            source = json.loads(
                self.forecasts.archive.read(value.feature_original_ref, value.feature_original_hash)
            )
            label_refs.update(bar["capture_ref"] for bar in source["bars"])
        authority = HistoryOutcomeCaptureAuthorityV2(self.forecasts)
        cache = authority.issue(session, tuple(sorted(label_refs)), checkpoint=check)
        try:
            values = self._read_forecasts(
                session, tuple(dict.fromkeys(ids)), check=check, capture_cache=cache
            )
            forecasts = {value.forecast_id: value for value in values}
            verified = []
            for item in inputs:
                if item.observation is not None:
                    self._path(session, item.observation, cache, check)
                forecast = (
                    None
                    if item.publication is None or item.publication.forecast is None
                    else forecasts[item.publication.forecast.identifier]
                )
                verified.append(
                    OutcomeScoreInputsV2(
                        item.opportunity,
                        item.opportunity_ref,
                        item.publication,
                        item.publication_ref,
                        item.observation,
                        item.observation_ref,
                        forecast,
                        item.acceptance,
                        item.acceptance_ref,
                    )
                )
            authority.close(session, cache)
            check()
            return protocol, protocol_ref, tuple(verified), acceptance, acceptance_ref
        finally:
            authority.abort()

    def _verify_publication(self, session, publication):
        if publication.dispatch is None:
            if publication.classification != "missing":
                raise ValueError("history_outcome_dispatch_original_required")
            return
        if self.hourly is None:
            raise ValueError("history_outcome_trusted_hourly_reader_required")
        copied = self.archive.read(
            publication.dispatch.original_ref, publication.dispatch.original_hash
        )
        self._retain(publication.dispatch.original_ref, publication.dispatch.original_hash)
        pair = self.hourly._read_slot(session, publication.dispatch.identifier)
        if pair is None:
            raise ValueError("history_outcome_saved_dispatch_missing")
        slot, receipt = pair
        self._retain(slot.source_original_ref, slot.source_original_hash)
        if (
            copied != encoded(receipt)
            or slot.receipt_hash != publication.dispatch.payload_hash
            or slot.owner_claim != self.owner_claim
            or slot.scope_id != publication.scope_id
            or as_utc(slot.hour_at) != publication.hour_at
            or publication.actual_cutoff_at is not None
            and as_utc(slot.cutoff_at) != publication.actual_cutoff_at
        ):
            raise ValueError("history_outcome_dispatch_original_binding_changed")
        if publication.classification in ("on_time", "late") and (
            slot.status != "committed_verified"
            or slot.forecast_id != publication.forecast.identifier
            or slot.model_id != publication.model.identifier
            or as_utc(slot.completed_at) != publication.verified_readback_at
        ):
            raise ValueError("history_outcome_actual_publication_readback_evidence_changed")

    def prepare_score(
        self, session, protocol_id, request_id, *, budget_seconds=120, checkpoint=None
    ):
        check = self._check(budget_seconds, checkpoint)
        with self._files_guard(check):
            return self._prepare_score_body(
                session, protocol_id, request_id, budget_seconds=budget_seconds, checkpoint=check
            )

    def _prepare_score_body(
        self, session, protocol_id, request_id, *, budget_seconds=120, checkpoint=None
    ):
        check = self._check(budget_seconds, checkpoint)
        protocol, ref, inputs, acceptance, acceptance_ref = self._score_inputs(
            session, protocol_id, check
        )
        clock = self.now()
        values = calculate_outcome_score_v2(
            protocol,
            ref,
            inputs,
            as_of=clock,
            registration_acceptance=acceptance,
            registration_acceptance_ref=acceptance_ref,
        )
        value = seal_outcome(
            HistoryOutcomeScoreV2,
            **self._common(request_id),
            score_id="hscore2_" + canonical_hash([self.owner_claim, request_id])[:48],
            protocol=ref,
            **values,
        )
        dependencies = tuple(
            (row.__tablename__, row.opportunity_id, _native(row))
            for row in self._opportunities(session, protocol)
        )
        check()
        return self._prepare(session, (("score", value),), dependencies)

    def read_score(self, session, identifier, *, budget_seconds=120, checkpoint=None):
        check = self._check(budget_seconds, checkpoint)
        with self._files_guard(check):
            return self._read_score_body(
                session, identifier, budget_seconds=budget_seconds, checkpoint=check
            )

    def _read_score_body(self, session, identifier, *, budget_seconds=120, checkpoint=None):
        check = self._check(budget_seconds, checkpoint)
        boundary = snapshot_identity(session)
        schema = self._schemas(session)
        value, _, _ = self._read(session, "score", identifier)
        protocol, ref, inputs, acceptance, acceptance_ref = self._score_inputs(
            session,
            value.protocol.identifier,
            check,
            frozen_cases=value.cases,
            as_of=value.actual_as_of,
        )
        # A saved score binds its exact census. Later retries/acceptances cannot
        # silently revise that saved as-of result; reject if the current first
        # pointers do not reproduce its original input references.
        values = calculate_outcome_score_v2(
            protocol,
            ref,
            inputs,
            as_of=value.actual_as_of,
            registration_acceptance=acceptance,
            registration_acceptance_ref=acceptance_ref,
        )
        for name, expected in values.items():
            if getattr(value, name) != expected:
                raise ValueError("history_outcome_saved_score_census_or_math_changed")
        self._source_read(value)
        if not native_equal39(self._schemas(session), schema):
            raise ValueError("history_outcome_complete_schema_changed")
        require_snapshot(session, boundary)
        check()
        return value

    def fast_status(self, session, protocol_id, *, offset=0, limit=30):
        """Indexed metadata only: no archive/source/raw/model authentication."""
        if (
            type(offset) is not int
            or not 0 <= offset <= 47
            or type(limit) is not int
            or not 1 <= limit <= 30
        ):
            raise ValueError("history_outcome_status_page_invalid")
        protocol = session.get(Protocol, protocol_id, populate_existing=True)
        if protocol is None or protocol.owner_claim != self.owner_claim:
            raise KeyError(protocol_id)
        rows = tuple(
            session.scalars(
                select(Opportunity)
                .where(
                    Opportunity.protocol_id == protocol_id,
                    Opportunity.owner_claim == self.owner_claim,
                )
                .order_by(Opportunity.hour_at, Opportunity.scope_id)
            ).all()
        )
        order = {scope: i for i, scope in enumerate(SCOPES)}
        rows = tuple(sorted(rows, key=lambda row: (as_utc(row.hour_at), order[row.scope_id])))
        if len(rows) != 48:
            raise ValueError("history_outcome_status_incomplete_enrollment")
        publications = {
            row.publication_id: row
            for row in session.scalars(
                select(Publication).where(
                    Publication.protocol_id == protocol_id, Publication.kind == "publication"
                )
            ).all()
        }
        observations = {}
        observations_by_id = {}
        for row in session.scalars(
            select(Observation)
            .where(Observation.protocol_id == protocol_id, Observation.kind == "observation")
            .order_by(Observation.attempt)
        ).all():
            observations[row.opportunity_id] = row
            observations_by_id[row.observation_id] = row
        confirmations = {
            row.observation_id: row
            for row in session.scalars(
                select(Observation).where(
                    Observation.protocol_id == protocol_id, Observation.kind == "acceptance"
                )
            ).all()
        }
        clock = self.now()
        pubs, labels = dict.fromkeys(PUBLICATION_STATES, 0), dict.fromkeys(LABEL_STATES, 0)
        items, due_pub, due_label, paired = [], 0, 0, 0
        for row in rows:
            pub = publications.get(row.first_publication_id)
            label = (
                observations_by_id.get(row.first_accepted_observation_id)
                if row.first_accepted_observation_id is not None
                else observations.get(row.opportunity_id)
            )
            cutoff = (
                as_utc(row.label_cutoff_at)
                if row.label_cutoff_at is not None
                else as_utc(row.hour_at)
            )
            pub_state = (
                pub.classification
                if pub is not None
                else ("unconfirmed" if clock >= as_utc(row.dispatch_deadline_at) else "pending")
            )
            deadline = cutoff + timedelta(days=2, minutes=1)
            label_state = (
                "pending_maturity"
                if clock < cutoff + timedelta(days=1, minutes=1)
                else (
                    "missing_at_deadline"
                    if clock >= deadline and row.first_acceptance_id is None
                    else label.classification
                    if label is not None
                    else "partial"
                )
            )
            if label_state == "verified" and row.first_acceptance_id is None:
                label_state = "partial"
            elif label_state == "verified":
                confirmation = confirmations.get(row.first_acceptance_id)
                if confirmation is None:
                    label_state = "invalid"
                elif as_utc(confirmation.independently_verified_at) > deadline:
                    label_state = "late_diagnostic"
            pubs[pub_state] += 1
            labels[label_state] += 1
            due_pub += clock >= as_utc(row.dispatch_deadline_at)
            due_label += clock >= cutoff + timedelta(days=1, minutes=1)
            paired += pub_state == "on_time" and label_state == "verified"
            asset = "BTC" if row.scope_id == SCOPES[0] else "ETH"
            items.append(
                dict(
                    opportunity_id=row.opportunity_id,
                    asset=asset,
                    scope_id=row.scope_id,
                    asset_label="比特币" if asset == "BTC" else "以太坊",
                    hour_at=as_utc(row.hour_at).isoformat(),
                    actual_cutoff_at=None
                    if pub is None or pub.actual_cutoff_at is None
                    else as_utc(pub.actual_cutoff_at).isoformat(),
                    publication_state=pub_state,
                    label_state=label_state,
                    state=row.state,
                    attempts=row.attempts,
                    reason_code=row.error_code or (None if pub is None else pub.reason_code),
                )
            )
        latest = session.scalar(
            select(Score)
            .where(Score.protocol_id == protocol_id, Score.owner_claim == self.owner_claim)
            .order_by(Score.actual_as_of.desc())
            .limit(1)
        )
        return dict(
            schema_version="history-outcome-status.v2",
            owner_authenticated=True,
            protocol_id=protocol_id,
            state=("blocked" if clock >= as_utc(protocol.first_hour) else "prepared")
            if protocol.confirmed_at is None
            else (
                "completed"
                if all(row.state == "completed" for row in rows)
                or clock >= as_utc(protocol.last_hour) + timedelta(days=2, minutes=6)
                else "active"
            ),
            prepared_registered_at=as_utc(protocol.registered_at).isoformat(),
            independently_confirmed_registered_at=None
            if protocol.confirmed_at is None
            else as_utc(protocol.confirmed_at).isoformat(),
            first_hour=as_utc(protocol.first_hour).isoformat(),
            last_hour=as_utc(protocol.last_hour).isoformat(),
            actual_as_of=clock.isoformat(),
            expected_total=48,
            publication_due_count=due_pub,
            label_due_count=due_label,
            publication_counts=pubs,
            label_counts=labels,
            paired_count=paired,
            latest_score=None
            if latest is None
            else dict(
                identifier=latest.score_id,
                payload_hash=latest.payload_hash,
                original_ref=latest.original_ref,
                original_hash=latest.original_hash,
            ),
            opportunities=items[offset : offset + limit],
            pagination=dict(offset=offset, limit=limit, total=48),
            current_originals_verified=False,
            financial_qualified=False,
            historical_pit_verified=False,
            promotion_allowed=False,
            ci=None,
            ci_status="not_registered_for_hourly_overlapping_windows",
        )


LOADED_METHODS = tuple(
    (name, fn, fn.__code__)
    for name, fn in HistoryOutcomeV2Service.__dict__.items()
    if callable(fn) and hasattr(fn, "__code__")
)
