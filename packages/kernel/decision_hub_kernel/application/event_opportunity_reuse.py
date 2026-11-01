"""Process-local witnesses for personally authenticated event opportunities.

This is not a persisted verified flag. A restart replays the native reader once;
each warm use still checks originals, finite release windows and current inputs.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import inspect
import json
from pathlib import Path
from sqlalchemy import or_, select
from ..persistence.db import ObservationRecord, SourcePollAttemptRecord, as_utc
from ..persistence.event_scenario_records import EventScenarioForecastRecord, EventScenarioOutputRecord
from ..persistence.risk_records import MarketCandleRecord
from ..ports.bls_cpi import BLS_SOURCE_ID
from ..ports.cpi_release_history import SOURCE, CpiReleaseHistoryPayload
from ..ports.event_scenario_autoproduction import CpiEventOwnedScenarioSnapshot
from ..ports.market_path import canonical_hash


def begin_opportunity_read_snapshot_in_session(session):
    """Start before native prepare's first SELECT, not halfway through replay."""
    connection = session.connection()
    if connection.dialect.name == 'sqlite':
        physical = connection.connection.driver_connection
        if physical is None:
            raise ValueError('event_opportunity_reuse_physical_snapshot_required')
        if not physical.in_transaction:
            connection.exec_driver_sql('BEGIN')


def _recipe(native, model):
    """Both installed bytes and actual loaded calculation bindings matter."""
    from ..decision import action_research_policy
    functions = (native.build_event_opportunity, native.collect_event_response_model_in_session,
                 native.read_event_owned_scenario_in_session, native._prepare_guard)
    files = {Path(native.__file__), Path(action_research_policy.__file__)}
    files.update(Path(inspect.getmodule(f).__file__) for f in functions)
    return canonical_hash({'schema': native.SCHEMA,
        'files': sorted((str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files),
        # Code objects are immutable and this witness never crosses processes.
        # marshal's reference/interning serialization flags are not a stable
        # fingerprint after first execution on all supported Python versions.
        'loaded': [(f.__module__, f.__qualname__, id(f.__code__)) for f in functions],
        'native_method': native._method(model)})


def _context(native, session, forecast_id, instrument, cutoff, archive):
    seam = getattr(native, 'opportunity_reuse_context_fingerprint_in_session', None)
    return seam(session, forecast_id, instrument, cutoff_at=cutoff, archive=archive) if seam else None


def _admissions(session, cutoff):
    # Admission is atomic with its source rows. Ignore successful empty/duplicate
    # polls, whose timestamps would otherwise cause useless periodic recompute.
    values = []
    for source in (SOURCE, BLS_SOURCE_ID):
        row = session.scalar(select(SourcePollAttemptRecord).where(
            SourcePollAttemptRecord.source_id == source,
            SourcePollAttemptRecord.status == 'succeeded',
            SourcePollAttemptRecord.completed_at <= cutoff,
            or_(SourcePollAttemptRecord.accepted_count > 0, SourcePollAttemptRecord.revision_count > 0)
        ).order_by(SourcePollAttemptRecord.completed_at.desc(), SourcePollAttemptRecord.started_at.desc(), SourcePollAttemptRecord.attempt_id).limit(1))
        values.append((source, row.attempt_id if row else None))
    return tuple(values)


def _release_inputs(session, native, cutoff):
    """Already native-read in this same preparation/reader transaction."""
    rows = session.scalars(select(ObservationRecord).where(
        ObservationRecord.source_id == SOURCE, ObservationRecord.received_at <= cutoff
    ).order_by(ObservationRecord.received_at.desc(), ObservationRecord.observation_id)).all()
    windows, guards, raw = {}, {}, set()
    for row in rows:
        value = CpiReleaseHistoryPayload.model_validate_json(row.text)
        # All registered originals, including currently excluded releases, are
        # protected. New rows are detected by source admission, not global scans.
        keys = [('observation', row.observation_id), ('source_attempt', value.attempt_id),
                ('raw_capture', value.capture_id)]
        for table, key in keys:
            original = session.get(native._GUARD_TYPES[table], key, populate_existing=True)
            if original is None:
                raise ValueError('event_opportunity_reuse_original_missing')
            guards[(table, key)] = native._row_hash(original)
        raw.add((value.raw_body_ref, value.raw_body_hash))
        windows.setdefault(value.event_key, (value.scheduled_at.replace(minute=0, second=0, microsecond=0)
                            - timedelta(hours=21), value.scheduled_at + timedelta(hours=24, seconds=60)))
    return tuple(windows.values()), tuple((t, k, h) for (t, k), h in sorted(guards.items())), tuple(sorted(raw))


def _paths(session, native, scope, windows, cutoff):
    result = []
    for start, end in windows:
        if end > cutoff:
            result.append((start.isoformat(), end.isoformat(), 'not_mature'))
            continue
        rows = session.scalars(select(MarketCandleRecord).where(
            MarketCandleRecord.scope_id == scope, MarketCandleRecord.open_at >= start,
            MarketCandleRecord.close_at <= end, MarketCandleRecord.known_at <= cutoff
        ).order_by(MarketCandleRecord.candle_id)).all()
        result.append((start.isoformat(), end.isoformat(), canonical_hash([
            (r.candle_id, native._row_hash(r)) for r in rows])))
    return tuple(result)


@dataclass(frozen=True)
class _Witness:
    value: object
    row_hash: str
    sources: tuple
    raw: tuple
    windows: tuple
    paths: tuple
    admissions: tuple
    context: object
    recipe: str


class OpportunityReuseWitnesses:
    def __init__(self):
        self._values = {}
        self._pending = {}
        self.last_miss_code = None

    @staticmethod
    def _eligible(native, value, cutoff):
        from ..decision.action_research_policy import research_action_catalog
        return (value.schema_version == native.SCHEMA and value.published_at <= cutoff
            and cutoff < min(value.model_valid_until, value.scheduled_at)
            and int(cutoff.timestamp()) // 21600 == int(value.model.as_of.timestamp()) // 21600
            and value.model.catalog_hash == canonical_hash(research_action_catalog(value.instrument)))

    def _capture(self, session, native, scenario, model, *, sources, raw, archive):
        windows, extra, more_raw = _release_inputs(session, native, model.as_of)
        return {'sources': tuple(sorted(set(sources + extra))), 'raw': tuple(sorted(set(raw + more_raw))),
            'windows': windows, 'paths': _paths(session, native, model.scope_id, windows, model.as_of),
            'admissions': _admissions(session, model.as_of),
            'context': _context(native, session, scenario.forecast_id, model.instrument, model.as_of, archive),
            'recipe': _recipe(native, model)}

    def prepared(self, session, prepared, *, archive):
        from . import event_owned_opportunity as native
        # Only personally observed, still-unconsumed native preparations qualify.
        if not isinstance(prepared, native.PreparedEventOwnedOpportunity) or not native._ISSUED.get(prepared, False):
            raise ValueError('event_opportunity_reuse_preparation_not_native')
        evidence = self._capture(session, native, prepared.scenario, prepared.model,
            sources=prepared.source_guard, raw=prepared.raw_guard, archive=archive)
        self._pending[id(prepared)] = (prepared, evidence)

    def committed(self, session, prepared, value, *, archive):
        """Call after native commit, while the same writer can still check row."""
        from . import event_owned_opportunity as native
        pending = self._pending.pop(id(prepared), None)
        if pending is None or pending[0] is not prepared or native._ISSUED.get(prepared, False):
            raise ValueError('event_opportunity_reuse_commit_not_native')
        evidence = pending[1]
        # Native commit protects selected samples. Also protect dependencies of
        # excluded releases that our witness observed, without repeating the
        # already-checked selected price windows in the writer.
        for table, ident, digest in set(evidence['sources']) - set(prepared.source_guard):
            original = session.get(native._GUARD_TYPES[table], ident, populate_existing=True)
            if original is None or native._row_hash(original) != digest:
                raise ValueError('event_opportunity_reuse_unselected_original_changed')
        for ref, digest in set(evidence['raw']) - set(prepared.raw_guard):
            if hashlib.sha256(archive.read(ref)).hexdigest() != digest:
                raise ValueError('event_opportunity_reuse_unselected_raw_changed')
        selected = {(start, end) for start, end, digest in prepared.path_guard}
        extra = tuple(w for w in evidence['windows'] if w not in selected)
        expected = tuple(v for v in evidence['paths'] if (datetime.fromisoformat(v[0]), datetime.fromisoformat(v[1])) in extra)
        if _paths(session, native, prepared.model.scope_id, extra, prepared.model.as_of) != expected:
            raise ValueError('event_opportunity_reuse_unselected_price_inputs_changed')
        if _admissions(session, prepared.model.as_of) != evidence['admissions']:
            raise ValueError('event_opportunity_reuse_admission_changed')
        row = session.get(EventScenarioOutputRecord, value.snapshot_id, populate_existing=True)
        if row is None or row.payload_json != json.dumps(value.model_dump(mode='json'), ensure_ascii=False,
                sort_keys=True, separators=(',', ':')) or row.payload_hash != canonical_hash(value.model_dump(mode='json')):
            raise ValueError('event_opportunity_reuse_commit_original_missing')
        return _Witness(value=value, row_hash=native._row_hash(row), **evidence)

    def remember(self, witness):
        # One current witness per forecast/scope; old epochs are discarded.
        cutoff = witness.value.published_at
        self._values = {k: v for k, v in self._values.items() if v.value.model_valid_until > cutoff}
        self._values[(witness.value.forecast_id, witness.value.instrument)] = witness

    def discard(self, prepared):
        self._pending.pop(id(prepared), None)

    def reusable(self, session, forecast_id, instrument, *, archive, cutoff_at):
        from . import event_owned_opportunity as native
        self.last_miss_code = None
        # Python's SQLite legacy transaction mode does not BEGIN for SELECT.
        # All warm row/admission/path checks must share one physical snapshot.
        begin_opportunity_read_snapshot_in_session(session)
        key = (forecast_id, instrument)
        witness = self._values.get(key)
        try:
            if witness is None:
                row = session.scalar(select(EventScenarioOutputRecord).where(
                    EventScenarioOutputRecord.forecast_id == forecast_id,
                    EventScenarioOutputRecord.kind == native.KIND,
                    EventScenarioOutputRecord.scope_key == instrument,
                    EventScenarioOutputRecord.published_at <= cutoff_at
                ).order_by(EventScenarioOutputRecord.published_at.desc(), EventScenarioOutputRecord.output_id).limit(1))
                if row is None or row.schema_version != native.SCHEMA:
                    return None
                # Metadata can only reject; only the native full reader accepts.
                hint = json.loads(row.payload_json)
                if int(cutoff_at.timestamp()) // 21600 != int(as_utc(datetime.fromisoformat(hint['as_of'])).timestamp()) // 21600:
                    return None
                value = native.read_event_owned_opportunity_in_session(session, row.output_id,
                    archive=archive, cutoff_at=cutoff_at)
                if not self._eligible(native, value, cutoff_at):
                    return None
                original = session.get(EventScenarioForecastRecord, forecast_id, populate_existing=True)
                scenario = CpiEventOwnedScenarioSnapshot.model_validate_json(original.payload_json)
                options = {'context': getattr(value, 'macro_context', None), 'archive': archive}
                parameters = inspect.signature(native._prepare_guard).parameters
                sources, raw = native._prepare_guard(session, scenario, value.model,
                    **{k: v for k, v in options.items() if k in parameters})
                witness = _Witness(value=value, row_hash=native._row_hash(row), **self._capture(
                    session, native, scenario, value.model, sources=sources, raw=raw, archive=archive))
            value = witness.value
            if not self._eligible(native, value, cutoff_at):
                raise ValueError('event_opportunity_reuse_expired')
            if witness.recipe != _recipe(native, value.model):
                raise ValueError('event_opportunity_reuse_method_changed')
            row = session.get(EventScenarioOutputRecord, value.snapshot_id, populate_existing=True)
            if row is None or native._row_hash(row) != witness.row_hash:
                raise ValueError('event_opportunity_reuse_output_changed')
            latest = session.scalar(select(EventScenarioOutputRecord.output_id).where(
                EventScenarioOutputRecord.forecast_id == forecast_id, EventScenarioOutputRecord.kind == native.KIND,
                EventScenarioOutputRecord.scope_key == instrument, EventScenarioOutputRecord.published_at <= cutoff_at
            ).order_by(EventScenarioOutputRecord.published_at.desc(), EventScenarioOutputRecord.output_id).limit(1))
            if latest != value.snapshot_id or _admissions(session, cutoff_at) != witness.admissions:
                raise ValueError('event_opportunity_reuse_selection_changed')
            for table, ident, digest in witness.sources:
                original = session.get(native._GUARD_TYPES[table], ident, populate_existing=True)
                if original is None or native._row_hash(original) != digest:
                    raise ValueError('event_opportunity_reuse_original_changed')
            for ref, digest in witness.raw:
                if hashlib.sha256(archive.read(ref)).hexdigest() != digest:
                    raise ValueError('event_opportunity_reuse_raw_changed')
            if witness.paths != _paths(session, native, value.model.scope_id, witness.windows, cutoff_at):
                raise ValueError('event_opportunity_reuse_price_inputs_changed')
            if witness.context != _context(native, session, forecast_id, instrument, cutoff_at, archive):
                raise ValueError('event_opportunity_reuse_context_changed')
            self._values[key] = witness
            return value
        except Exception as exc:
            # A miss re-enters the normal native path, whose concrete failure is
            # durably visible on the owned job; it never certifies stale output.
            self._values.pop(key, None)
            self.last_miss_code = str(exc)[:128] if isinstance(exc, ValueError) else type(exc).__name__
            return None
