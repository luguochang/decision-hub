from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from packages.contracts_py.decision_hub_contracts.models import StrategyCalibrationView
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    StrategyCalibrationRecord,
    as_utc,
)

if TYPE_CHECKING:
    from packages.evals.forecast_calibration import ForecastCalibrationReport


class StrategyCalibrationService:
    """Own immutable walk-forward receipts; model output cannot mint them."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def persist(self, report: ForecastCalibrationReport) -> str:
        payload = report.model_dump(mode="json")
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        report_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with self.database.session() as session:
            existing = (
                session.query(StrategyCalibrationRecord)
                .filter_by(
                    strategy_version=report.strategy_version,
                    horizon=report.horizon,
                    dataset_hash=report.dataset_hash,
                )
                .one_or_none()
            )
            if existing is not None:
                if existing.report_hash != report_hash:
                    raise ValueError("calibration_dataset_receipt_conflict")
                return existing.receipt_id
            receipt_id = f"cal_{uuid.uuid4().hex}"
            session.add(
                StrategyCalibrationRecord(
                    receipt_id=receipt_id,
                    strategy_version=report.strategy_version,
                    horizon=report.horizon,
                    dataset_hash=report.dataset_hash,
                    sample_count=report.sample_count,
                    evaluated_sample_count=report.evaluated_sample_count,
                    fold_count=report.fold_count,
                    accuracy=report.accuracy,
                    accuracy_lower_bound=report.accuracy_lower_bound,
                    brier_score=report.brier_score,
                    baseline_brier_score=report.baseline_brier_score,
                    log_loss=report.log_loss,
                    net_return_pct=report.net_return_pct,
                    max_drawdown_pct=report.max_drawdown_pct,
                    coverage_ratio=report.coverage_ratio,
                    max_regime_share=report.max_regime_share,
                    calibration_slope=report.calibration_slope,
                    calibration_intercept=report.calibration_intercept,
                    passed=report.passed,
                    failure_reasons_json=json.dumps(report.failure_reasons),
                    report_json=canonical,
                    report_hash=report_hash,
                    evaluated_at=report.evaluated_at,
                )
            )
        return receipt_id

    def validates(
        self,
        receipt_id: str | None,
        *,
        strategy_version: str,
        horizon: str,
    ) -> bool:
        if not receipt_id:
            return False
        with self.database.session() as session:
            row = session.get(StrategyCalibrationRecord, receipt_id)
            return bool(
                row is not None
                and row.passed
                and row.strategy_version == strategy_version
                and row.horizon == horizon
            )

    def list(self, *, limit: int = 50) -> list[StrategyCalibrationView]:
        with self.database.session() as session:
            rows = (
                session.query(StrategyCalibrationRecord)
                .order_by(StrategyCalibrationRecord.evaluated_at.desc())
                .limit(limit)
                .all()
            )
        return [_view(row) for row in rows]


def _view(row: StrategyCalibrationRecord) -> StrategyCalibrationView:
    evaluated_at = as_utc(row.evaluated_at)
    if evaluated_at is None:  # pragma: no cover - non-null database invariant
        raise ValueError("calibration_evaluated_at_missing")
    return StrategyCalibrationView(
        receipt_id=row.receipt_id,
        strategy_version=row.strategy_version,
        horizon=row.horizon,
        dataset_hash=row.dataset_hash,
        sample_count=row.sample_count,
        evaluated_sample_count=row.evaluated_sample_count,
        fold_count=row.fold_count,
        accuracy=row.accuracy,
        accuracy_lower_bound=row.accuracy_lower_bound,
        brier_score=row.brier_score,
        baseline_brier_score=row.baseline_brier_score,
        log_loss=row.log_loss,
        net_return_pct=row.net_return_pct,
        max_drawdown_pct=row.max_drawdown_pct,
        coverage_ratio=row.coverage_ratio,
        max_regime_share=row.max_regime_share,
        passed=row.passed,
        failure_reasons=json.loads(row.failure_reasons_json),
        evaluated_at=evaluated_at,
    )


# Authenticated AI calibration keeps the legacy report CLI above unchanged.
import asyncio
import threading
from collections.abc import Callable
from contextlib import contextmanager
import importlib.metadata
import logging
import math
import marshal
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from time import monotonic
from types import CodeType, SimpleNamespace
from weakref import WeakKeyDictionary
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import event, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.engine import Connection, Engine

from packages.kernel.decision_hub_kernel.application.calibration_inputs import (
    MAX_COHORT, AIProbabilityScope, AuthenticatedCalibrationSample, CalibrationEvaluationPolicy,
    CalibrationPlan, CalibrationProducerIdentity, CalibrationProductionReference, CohortOriginal,
    FrozenCalibrationInputs, FrozenCalibrationProduction, KernelCalibratedValue, ProbabilityActivation,
    aware_utc, canonical_json, content_hash, stable_compiled_policy_recipe_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord, AnalyticalObservationRecord, AnalyticalOutlookRecord,
    RawCaptureRecord, RunRoleOutputRecord, StrategyCalibrationJobRecord,
    StrategyCalibrationProductionRecord, StrategyProbabilityActivationRecord, utcnow,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

_CALIBRATION_ROOT = Path(__file__).resolve().parents[4]
_LOGGER = logging.getLogger(__name__)
_LEASE_SECONDS = 1800
_DATETIME_FIELDS = {
    'registered_at', 'recorded_at', 'data_as_of', 'window_start_at', 'window_end_at',
    'next_attempt_at', 'lease_expires_at', 'evaluated_at', 'started_at', 'finished_at',
    'known_at', 'observed_at', 'completed_at', 'valid_from', 'valid_until', 'server_known_at',
}


class _CalibrationStopped(RuntimeError):
    """Cancellation aborts work; it is not a domain failure eligible for retry."""


class _CheckpointArchive(RawBodyArchive):
    """Read the same archive and check cancellation between original bodies."""

    def __init__(self, archive: RawBodyArchive, checkpoint: Callable[[], None]) -> None:
        super().__init__(archive.root)
        self._archive, self._checkpoint = archive, checkpoint

    def read(self, ref: str) -> bytes:
        self._checkpoint()
        try:
            return self._archive.read(ref)
        finally:
            self._checkpoint()


def _calibration_checkpoint(stopped: threading.Event, deadline: float | None) -> None:
    if stopped.is_set() or (deadline is not None and monotonic() >= deadline):
        raise _CalibrationStopped('calibration_deadline_or_cancelled')


def ai_probability_strategy_version(stable_recipe_hash: str) -> str:
    if len(stable_recipe_hash) != 64 or any(char not in '0123456789abcdef' for char in stable_recipe_hash):
        raise ValueError('calibration_ai_recipe_identity_invalid')
    return 'ai-graph-own-action.v2:' + stable_recipe_hash


def _row(row: Any) -> dict[str, Any]:
    return {column.key: (as_utc(value).isoformat() if isinstance(value, datetime) else value)
            for column in row.__table__.columns for value in (getattr(row, column.key),)}


def _original_object(body: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(**{key: (aware_utc(datetime.fromisoformat(value))
                              if key in _DATETIME_FIELDS and value is not None else value)
                              for key, value in body.items()})


def _require_read_snapshot(session: Session) -> None:
    connection = session.connection()
    native = connection.connection.driver_connection
    if (connection.dialect.name != 'sqlite' or not isinstance(native, sqlite3.Connection)
            or not native.in_transaction or session.new or session.dirty or session.deleted):
        raise ValueError('calibration_physical_read_scope_required')


def _producer_identity() -> CalibrationProducerIdentity:
    _require_producer_loaded()
    math_body = math_identity()
    return CalibrationProducerIdentity(
        code_files={**{path.relative_to(_CALIBRATION_ROOT).as_posix(): digest
                       for path, digest in {**_PRODUCER_FILES, **_DEPENDENCY_FILES}.items()}, **math_body['code_files']},
        dependencies={name: importlib.metadata.version(name)
                      for name in ('pydantic', 'SQLAlchemy', 'jsonschema')},
        loaded_abi={**{name: _code_hash(function) for name, (function, _code) in _PRODUCER_LOADED.items()},
                    **{name: _code_hash(function) for name, (function, _module, code) in _DEPENDENCIES.items() if code is not None},
                    **math_body['loaded_abi']},
        assessment_manifest_hash=loaded_assessment_manifest_hash(),
    )


def _read_source_scope_originals(session: Session, outlook: AnalyticalOutlookRecord, stable: str) -> tuple[dict[str, Any], dict[str, Any], Any]:
    rows = {row.role: row for row in session.scalars(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == outlook.run_id,
        RunRoleOutputRecord.role.in_(('research_compiled_policy', 'research_execution_method', 'research_graph_final')),
    ).execution_options(populate_existing=True))}
    if len(rows) != 3 or any(hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash for row in rows.values()):
        raise ValueError('calibration_source_original_missing')
    compiled = json.loads(rows['research_compiled_policy'].payload_json)['compiled']
    cached = _registration_source_originals(session, outlook.run_id)
    prepared = cached.fresh()[3] if cached is not None else read_prepared_execution_in_session(session, outlook.run_id)
    method = stable_hub_recipe(prepared.method.definition.model_dump(mode='json'), compiled)
    projection = parse_graph_final_projection_declaration(
        rows['research_graph_final'].payload_json, schema_version=rows['research_graph_final'].schema_version,
    )
    graph_recipe = authenticated_graph_recipe_identity(projection)
    if graph_recipe is None or recipe_hash({**method, 'graph_recipe': graph_recipe}) != stable:
        raise ValueError('calibration_graph_recipe_original_invalid')
    bundle = read_research_bundle_in_session(session, outlook.run_id)
    if not isinstance(bundle, FrozenResearchBundle):
        raise ValueError('calibration_frozen_bundle_unavailable')
    identities = {
        'stable_recipe_hash': stable, 'method_recipe_hash': recipe_hash(method),
        'graph_recipe_hash': content_hash(graph_recipe), 'pack_definition_hash': bundle.pack.definition_hash,
        'compiled_policy_recipe_hash': stable_compiled_policy_recipe_hash(compiled),
        'compiled_policy_hash': compiled['compiled_hash'],
    }
    result = None
    if projection.last_admission_ref is not None:
        round_projection = read_graph_round_in_session(
            session, run_id=outlook.run_id, reference=projection.last_admission_ref,
            expected_lease_owner=None, require_current_permission=False,
        )
        body = round_projection.receipt.projected_state.get('latest_result')
        result = ResearchSessionResult.model_validate(body) if body is not None else None
    return identities, _row(rows['research_graph_final']), result


def _source_scope(session: Session, outlook: AnalyticalOutlookRecord, stable: str) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    state = _registration_materials(session)
    if state is not None and state.scope_body is not None:
        _check_registration_materials(state, session, outlook.run_id)
        identities, graph, result_json = json.loads(state.scope_body)
        if identities['stable_recipe_hash'] != stable:
            raise ValueError('calibration_registration_recipe_changed')
    else:
        identities, graph, result = _read_source_scope_originals(session, outlook, stable)
        if state is not None and state.common is not None:
            _check_registration_materials(state, session, outlook.run_id)
            state.scope_body = canonical_json([identities, graph, result.model_dump_json() if result is not None else None])
            state.scope_digest = hashlib.sha256(state.scope_body.encode()).hexdigest()
    if state is not None and state.scope_body is not None:
        result_json = json.loads(state.scope_body)[2]
        result = ResearchSessionResult.model_validate_json(result_json) if result_json is not None else None
    selected_probability = None
    if result is not None:
        horizon = next((item for item in result.horizons if item.horizon == outlook.horizon), None)
        if horizon is not None:
            selected_probability = horizon.subjective_probability
    return identities, graph, selected_probability


def _cost_original(session: Session, plan: CalibrationPlan, outlook: AnalyticalOutlookRecord,
                   archive: RawBodyArchive) -> tuple[float, dict[str, Any]]:
    reference = plan.policy.cost
    if reference is None:
        raise ValueError('calibration_cost_policy_unavailable')
    row = session.get(RawCaptureRecord, reference.capture_id, populate_existing=True)
    if row is None:
        raise ValueError('calibration_cost_capture_missing')
    body_row = _row(row)
    url = urlsplit(row.url)
    if (content_hash(body_row) != reference.capture_row_hash or row.status_code != 200
            or row.error_code is not None or row.body_ref != reference.body_ref
            or row.body_hash != reference.body_hash or url.scheme != 'https'
            or url.hostname != reference.approved_host or url.username or url.password
            or as_utc(row.started_at) > as_utc(row.completed_at)
            or max(as_utc(row.completed_at), as_utc(row.recorded_at)) > as_utc(outlook.window_start_at)):
        raise ValueError('calibration_cost_original_invalid')
    raw = archive.read(reference.body_ref)
    if len(raw) > 16 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != reference.body_hash:
        raise ValueError('calibration_cost_archive_invalid')
    body = json.loads(raw)

    def pointer(path: str) -> float:
        value: Any = body
        if not path.startswith('/'):
            raise ValueError('calibration_cost_pointer_invalid')
        for part in path[1:].split('/'):
            key = part.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError('calibration_cost_value_invalid')
        result = float(value) * reference.percent_scale
        if not math.isfinite(result) or result < 0 or result > 100:
            raise ValueError('calibration_cost_value_invalid')
        return result

    fee, slippage = pointer(reference.fee_json_pointer), pointer(reference.slippage_json_pointer)
    return fee + slippage, {'policy': reference.model_dump(mode='json'), 'capture': body_row,
                            'fee_pct': fee, 'slippage_pct': slippage,
                            'meaning': 'approved_sourced_cost_model_not_realized_execution'}


def _regime_original(session: Session, frozen: dict[str, Any], archive: RawBodyArchive,
                     now: datetime) -> tuple[str, dict[str, Any]]:
    matches = [item for item in frozen['facts'] if item['payload'].get('field') in {'sma_20', 'sma_60'}]
    if {item['payload'].get('field') for item in matches} != {'sma_20', 'sma_60'} or len(matches) != 2:
        raise ValueError('calibration_regime_inputs_unavailable')
    receipts = {item['payload'].get('attributes', {}).get('daily_execution_receipt_json') for item in matches}
    if len(receipts) != 1 or None in receipts:
        raise ValueError('calibration_regime_execution_unavailable')
    receipt_json = next(iter(receipts))
    receipt = DailyExecutionReceipt.model_validate_json(receipt_json)
    evidence_ids = {item['payload']['evidence_id'] for item in matches}
    if len(evidence_ids) != 1:
        raise ValueError('calibration_regime_scope_mismatch')
    bound = read_daily_execution_in_session(
        session, run_id=receipt.run_id, evidence_id=next(iter(evidence_ids)), receipt_json=receipt_json,
        archive=archive, implementation=loaded_daily_implementation(),
        publication_at=datetime.fromisoformat(frozen['recorded_at']), assessed_at=now,
    )
    if bound is None:
        raise ValueError('calibration_regime_execution_unavailable')
    summary = json.loads(receipt.summary_json)
    for item in matches:
        if item['payload']['value'] != summary[item['payload']['field']]:
            raise ValueError('calibration_regime_value_mismatch')
    short, long = Decimal(summary['sma_20']), Decimal(summary['sma_60'])
    if not short.is_finite() or not long.is_finite() or min(short, long) <= 0:
        raise ValueError('calibration_regime_value_invalid')
    regime = 'uptrend' if short > long else 'downtrend' if short < long else 'flat'
    return regime, {'rule': 'authenticated-sma20-sma60.v1', 'facts': matches,
                    'execution_ref': {'output_id': bound.output_id, 'payload_hash': bound.payload_hash},
                    'receipt': receipt.model_dump(mode='json'),
                    'meaning': 'input_regime_only_not_ai_strategy_identity'}


def _cohort_statement(plan: CalibrationPlan, cutoff: datetime) -> Any:
    common = (AnalyticalOutlookRecord.market == plan.scope.market,
              AnalyticalOutlookRecord.instrument == plan.scope.instrument,
              AnalyticalOutlookRecord.method_family == 'ai_synthesis',
              AnalyticalOutlookRecord.horizon == plan.scope.horizon)
    candidate_studies = select(AnalyticalOutlookRecord.study_id).where(
        *common, AnalyticalOutlookRecord.recorded_at >= plan.start_at,
        AnalyticalOutlookRecord.recorded_at < plan.end_at, AnalyticalOutlookRecord.recorded_at <= cutoff,
        AnalyticalOutlookRecord.study_id.is_not(None),
    ).distinct()
    # Complete EARLIER prefix for every Study appearing in the plan, before labels.
    return (select(AnalyticalOutlookRecord).where(
        *common, AnalyticalOutlookRecord.study_id.in_(candidate_studies),
        AnalyticalOutlookRecord.recorded_at <= min(cutoff, plan.end_at),
    ).order_by(AnalyticalOutlookRecord.recorded_at, AnalyticalOutlookRecord.outlook_id)
        .limit(MAX_COHORT + 1).execution_options(populate_existing=True))


def _cohort_rows(session: Session, plan: CalibrationPlan, cutoff: datetime) -> list[AnalyticalOutlookRecord]:
    rows = session.scalars(_cohort_statement(plan, cutoff)).all()
    if len(rows) > MAX_COHORT:
        raise ValueError('calibration_cohort_prefix_incomplete')
    return list(rows)


def _cohort_upper_bound(session: Session, plan: CalibrationPlan, cutoff: datetime) -> int | None:
    """A complete raw prefix bounds samples; a capped sentinel grants no proof."""
    _require_read_snapshot(session)
    rows = session.scalars(_cohort_statement(plan, cutoff).with_only_columns(
        AnalyticalOutlookRecord.outlook_id)).all()
    return len(rows) if len(rows) <= MAX_COHORT else None


def _capture_inputs(session: Session, plan: CalibrationPlan, *, now: datetime,
                    archive: RawBodyArchive,
                    checkpoint: Callable[[], None] | None = None,
                    source_memo: dict[Any, Any] | None = None,
                    ) -> tuple[FrozenCalibrationInputs, list[dict[str, Any]]]:
    if checkpoint is not None:
        checkpoint()
    _require_read_snapshot(session)
    recorder = _ArchiveRecorder(_CheckpointArchive(archive, checkpoint) if checkpoint is not None else archive)
    try:
        rows = _cohort_rows(session, plan, now)
    except ValueError as error:
        return FrozenCalibrationInputs(plan=plan, captured_at=now, cohort=(), samples=(),
                                       reasons=(str(error),)), []
    assessed: dict[str, tuple[Any, dict[str, Any]]] = {}
    candidates, boundaries = [], []
    for row in rows:
        if checkpoint is not None:
            checkpoint()
        assessment, basis = build_assessment_basis_in_session(
            session, row.outlook_id, assessed_at=now, archive=recorder,
            source_memo=source_memo, checkpoint=checkpoint,
        )
        assessed[row.outlook_id] = assessment, basis
        candidates.append(RecipeObservationCandidate(
            outlook_id=row.outlook_id, recorded_at=as_utc(row.recorded_at), market=row.market,
            instrument=row.instrument, method_family=row.method_family, horizon=row.horizon, study_id=row.study_id,
            stable_recipe_hash=assessment.stable_recipe_hash,
            source_verified=assessment.source_status == 'verified' and assessment.method_status == 'verified',
        ))
        if basis['next_time_boundary_at']:
            boundaries.append(aware_utc(datetime.fromisoformat(basis['next_time_boundary_at'])))
    selection = select_recipe_observations(candidates, now=now)
    cohort, samples, reasons = [], [], set()
    for row in rows:
        if checkpoint is not None:
            checkpoint()
        assessment, basis = assessed[row.outlook_id]
        state = selection.statuses[row.outlook_id]
        item_reasons: list[str] = []
        included = False
        if plan.start_at <= as_utc(row.recorded_at) < plan.end_at and state in {'selected', 'unchecked'}:
            if assessment.source_status != 'verified' or assessment.method_status != 'verified':
                item_reasons.append(assessment.source_reason or 'calibration_source_unverified')
            elif assessment.stable_recipe_hash == plan.scope.stable_recipe_hash:
                try:
                    if state != 'selected':
                        raise ValueError('calibration_first_record_unchecked')
                    if not assessment.directional_usable or assessment.label_status != 'verified':
                        raise ValueError('calibration_label_or_input_unavailable')
                    identities, graph_ref, selected_probability = _source_scope(session, row, assessment.stable_recipe_hash)
                    for key in ('pack_definition_hash', 'compiled_policy_recipe_hash', 'method_recipe_hash', 'graph_recipe_hash'):
                        if identities[key] != getattr(plan.scope, key):
                            raise ValueError('calibration_recipe_scope_mismatch')
                    observation = session.get(AnalyticalObservationRecord, row.outlook_id, populate_existing=True)
                    attempt = session.get(AnalyticalObservationAttemptRecord, (row.outlook_id, observation.attempt), populate_existing=True)
                    if attempt is None or attempt.finished_at is None:
                        raise ValueError('calibration_label_known_time_missing')
                    frozen = _frozen_input(session, row)
                    verify_input_receipts(session, {**frozen, 'cutoff_at': frozen['recorded_at'],
                                                   'facts': sorted(frozen['facts'], key=lambda fact: fact['fact_version_id'])}, recorder)
                    label = verify_recorded_label(session, row, observation, recorder, now)
                    known = max(as_utc(observation.evaluated_at), as_utc(attempt.finished_at))
                    if known > now:
                        raise ValueError('calibration_label_not_yet_known')
                    cost, cost_original = _cost_original(session, plan, row, recorder)
                    regime, regime_original = _regime_original(session, frozen, recorder, now)
                    raw = json.loads(row.raw_payload_json)['horizon']['subjective_probability']
                    if row.subjective_probability is None or raw != row.subjective_probability:
                        raise ValueError('calibration_graph_probability_missing_or_changed')
                    sample = ForecastCalibrationSample(
                        sample_id=row.outlook_id, as_of=as_utc(row.recorded_at), outcome_at=known,
                        raw_probability=raw, outcome=bool(label['direction_correct']),
                        realized_return_pct=label['directional_return_pct'], trading_cost_pct=cost, regime=regime,
                    )
                    samples.append(AuthenticatedCalibrationSample(
                        sample=sample, outlook_id=row.outlook_id, run_id=row.run_id, graph_ref=graph_ref,
                        compiled_policy_hash=identities['compiled_policy_hash'], selected_probability=selected_probability,
                        observation_original=_row(observation), attempt_original=_row(attempt),
                        source_refs=tuple(ref.model_dump(mode='json') for ref in assessment.source_refs),
                        assessment_hash=content_hash(assessment.model_dump(mode='json')), basis_hash=content_hash(basis),
                        label_known_at=known, label=label, frozen_input=frozen, cost_original=cost_original,
                        regime_original=regime_original,
                    ))
                    included = True
                except (ValueError, TypeError, KeyError, OSError) as error:
                    code = str(error)
                    item_reasons.append(code if code and len(code) <= 120 and code.replace('_', '').isalnum()
                                        else 'calibration_original_unavailable')
        reasons.update(item_reasons)
        cohort.append(CohortOriginal(
            outlook_id=row.outlook_id, row_hash=content_hash(_row(row)), row=_row(row), selection=state,
            assessment_json=canonical_json(assessment.model_dump(mode='json')),
            assessment_hash=content_hash(assessment.model_dump(mode='json')),
            basis_json=canonical_json(basis), basis_hash=content_hash(basis), included=included,
            reasons=tuple(item_reasons),
        ))
    if len(samples) < plan.policy.thresholds.min_samples:
        reasons.add('calibration_samples_insufficient')
    if now < plan.end_at:
        reasons.add('calibration_sampling_interval_open')
        boundaries.append(plan.end_at)
    if plan.policy.cost is None:
        reasons.add('calibration_cost_policy_unavailable')
    result = FrozenCalibrationInputs(plan=plan, captured_at=now, cohort=tuple(cohort), samples=tuple(samples),
                                     reasons=tuple(sorted(reasons)), next_boundary_at=min(boundaries, default=None))
    if checkpoint is not None:
        checkpoint()
    return result, list(recorder.records.values())


def _verify_archives(
    records: list[dict[str, Any]], archive: RawBodyArchive,
    *, checkpoint: Callable[[], None] | None = None,
) -> None:
    if checkpoint is not None:
        checkpoint()
    recorder = _ArchiveRecorder(archive)
    for item in records:
        if checkpoint is not None:
            checkpoint()
        try:
            recorder.read(item['ref'])
        except (ValueError, OSError):
            # An unchanged missing original can only retain a waiting result.
            # It is not converted into a present body or an eligible sample.
            pass
        if recorder.records.get(item['ref']) != item:
            raise ValueError('calibration_original_archive_changed')
        if checkpoint is not None:
            checkpoint()


def _producer_failure_code(error: BaseException, phase: str) -> str:
    """Persist a bounded domain code, never raw exception data/paths/URLs."""
    if phase not in {'capture', 'identity', 'mathematics', 'writer'}:
        raise ValueError('calibration_failure_phase_invalid')
    if isinstance(error, ValidationError):
        return 'calibration_' + phase + '_input_validation_failed'
    if isinstance(error, ValueError):
        code = str(error)
        if (0 < len(code) <= 128 and code.replace('_', '').isascii()
                and code.replace('_', '').isalnum() and code.startswith((
                    'calibration_', 'probability_', 'original_preparation_',
                    'assessment_', 'research_', 'graph_', 'daily_',
                ))):
            return code
        return 'calibration_' + phase + '_original_validation_failed'
    suffix = ('archive_unavailable' if isinstance(error, OSError)
              else 'original_field_missing' if isinstance(error, KeyError)
              else 'original_type_invalid')
    return 'calibration_' + phase + '_' + suffix


def _verify_saved_inputs(session: Session, production: FrozenCalibrationProduction, archive: RawBodyArchive) -> None:
    """Recheck immutable source/attempt/archive, using the recorded mutable label copy."""
    inputs = production.inputs
    rows = _cohort_rows(session, inputs.plan, inputs.captured_at)
    if [_row(row) for row in rows] != [item.row for item in inputs.cohort]:
        raise ValueError('calibration_cohort_original_changed')
    recipes = []
    row_map = {row.outlook_id: row for row in rows}
    expected_map = {item.outlook_id: item for item in inputs.cohort}
    for row in rows:
        original = expected_map[row.outlook_id]
        if (original.row_hash != content_hash(original.row)
                or hashlib.sha256(original.assessment_json.encode()).hexdigest() != original.assessment_hash
                or hashlib.sha256(original.basis_json.encode()).hexdigest() != original.basis_hash):
            raise ValueError('calibration_original_payload_changed')
        assessment = json.loads(original.assessment_json)
        verified = assessment['source_status'] == 'verified' and assessment['method_status'] == 'verified'
        stable = None
        if verified:
            stable, refs, forward = _source(session, row, inputs.captured_at, archive=archive)
            if (stable != assessment['stable_recipe_hash']
                    or [ref.model_dump(mode='json') for ref in refs] != assessment['source_refs'] or not forward):
                raise ValueError('calibration_source_original_changed')
        recipes.append(RecipeObservationCandidate(row.outlook_id, as_utc(row.recorded_at), row.market,
                       row.instrument, row.method_family, row.horizon, row.study_id, stable, verified))
    selection = select_recipe_observations(recipes, now=inputs.captured_at)
    if any(selection.statuses[item.outlook_id] != item.selection for item in inputs.cohort):
        raise ValueError('calibration_first_record_original_changed')
    for item in inputs.samples:
        row = row_map[item.outlook_id]
        stable, _refs, _forward = _source(session, row, inputs.captured_at, archive=archive)
        identities, graph_ref, selected_probability = _source_scope(session, row, stable)
        if (graph_ref != item.graph_ref or selected_probability != item.selected_probability
                or identities['compiled_policy_hash'] != item.compiled_policy_hash
                or any(identities[key] != getattr(production.scope, key) for key in
                       ('stable_recipe_hash', 'pack_definition_hash', 'compiled_policy_recipe_hash', 'method_recipe_hash', 'graph_recipe_hash'))):
            raise ValueError('calibration_sample_recipe_changed')
        attempt = session.get(AnalyticalObservationAttemptRecord,
                              (row.outlook_id, item.attempt_original['attempt']), populate_existing=True)
        if attempt is None or _row(attempt) != item.attempt_original:
            raise ValueError('calibration_attempt_original_changed')
        observation = _original_object(item.observation_original)
        label = verify_recorded_label(session, row, observation, archive, inputs.captured_at)
        frozen = _frozen_input(session, row)
        verify_input_receipts(session, {**frozen, 'cutoff_at': frozen['recorded_at'],
                                       'facts': sorted(frozen['facts'], key=lambda fact: fact['fact_version_id'])}, archive)
        cost, cost_original = _cost_original(session, inputs.plan, row, archive)
        regime, regime_original = _regime_original(session, frozen, archive, inputs.captured_at)
        known = max(as_utc(observation.evaluated_at), as_utc(attempt.finished_at))
        if (label != item.label or frozen != item.frozen_input or known != item.label_known_at
                or cost_original != item.cost_original or regime_original != item.regime_original
                or cost != item.sample.trading_cost_pct or regime != item.sample.regime
                or item.sample.raw_probability != row.subjective_probability
                or item.sample.outcome != bool(label['direction_correct'])
                or item.sample.realized_return_pct != label['directional_return_pct']):
            raise ValueError('calibration_sample_original_changed')


_ENGINE_ARCHIVES: WeakKeyDictionary = WeakKeyDictionary()


def configure_probability_archive(database: Database, archive: RawBodyArchive) -> None:
    """Trusted composition port keyed by actual Engine; never a verdict or model input."""
    if not isinstance(database, Database) or type(archive) is not RawBodyArchive:
        raise ValueError('calibration_archive_composition_required')
    old = _ENGINE_ARCHIVES.get(database.engine)
    if old is not None and old.root.resolve() != archive.root.resolve():
        raise ValueError('calibration_archive_engine_conflict')
    _ENGINE_ARCHIVES[database.engine] = archive


def _probability_archive(session: Session) -> RawBodyArchive:
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind if isinstance(bind, Engine) else None
    archive = _ENGINE_ARCHIVES.get(engine) if engine is not None else None
    # A whole assessor read can temporarily provide its existing server archive.
    archive = session.info.get('kernel_calibration_archive', archive)
    if not isinstance(archive, RawBodyArchive):
        raise ValueError('calibration_read_archive_unavailable')
    return archive


def load_production_in_session(session: Session, production_id: str,
                               expected_payload_hash: str | None = None,
                               *, archive: RawBodyArchive | None = None) -> FrozenCalibrationProduction:
    _require_read_snapshot(session)
    _require_producer_loaded()
    row = session.get(StrategyCalibrationProductionRecord, production_id, populate_existing=True)
    if row is None or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash:
        raise ValueError('calibration_production_original_missing_or_changed')
    if expected_payload_hash is not None and row.payload_hash != expected_payload_hash:
        raise ValueError('calibration_production_binding_changed')
    value = FrozenCalibrationProduction.model_validate_json(row.payload_json)
    job = session.get(StrategyCalibrationJobRecord, value.job_id, populate_existing=True)
    plan_body = canonical_json(value.inputs.plan.model_dump(mode='json'))
    if (job is None or job.job_id != value.inputs.plan.job_id
            or job.request_id != value.inputs.plan.request_id or job.plan_json != plan_body
            or job.plan_hash != hashlib.sha256(plan_body.encode()).hexdigest()
            or as_utc(job.registered_at) != value.inputs.plan.registered_at
            or value.inputs.plan.registered_at > value.captured_at):
        raise ValueError('calibration_production_plan_original_invalid')
    if (row.payload_json != canonical_json(value.model_dump(mode='json'))
            or row.production_id != value.production_id or row.job_id != value.job_id
            or row.scope_hash != content_hash(value.scope.model_dump(mode='json'))
            or row.inputs_hash != value.inputs_hash or row.producer_hash != value.producer_hash
            or as_utc(row.captured_at) != value.captured_at or as_utc(row.server_known_at) != value.server_known_at
            or row.state != value.state or row.report_hash != value.report_hash
            or row.report_json != (canonical_json(value.report.model_dump(mode='json')) if value.report else None)
            or row.sample_dataset_hash != (value.report.dataset_hash if value.report else None)
            or value.producer != _producer_identity()):
        raise ValueError('calibration_production_original_invalid')
    if value.state == 'passed':
        _verify_saved_inputs(session, value, archive or _probability_archive(session))
        report = evaluate_authenticated_forecast_calibration(value.inputs, evaluated_at=value.report.evaluated_at)
        if report != value.report:
            raise ValueError('calibration_math_report_changed')
    return value


def transform_probability(raw_probability: float, production: FrozenCalibrationProduction) -> KernelCalibratedValue:
    """Pure numeric transform; callers must first authenticate the production originals."""
    _require_producer_loaded()
    value = FrozenCalibrationProduction.model_validate(production.model_dump(mode='json'))
    if value.state != 'passed' or value.report is None or value.producer != _producer_identity():
        raise ValueError('calibration_production_not_available')
    report = evaluate_authenticated_forecast_calibration(value.inputs, evaluated_at=value.report.evaluated_at)
    if report != value.report:
        raise ValueError('calibration_math_report_changed')
    return KernelCalibratedValue(
        production_id=value.production_id, production_payload_hash=content_hash(value.model_dump(mode='json')),
        scope_hash=content_hash(value.scope.model_dump(mode='json')), report_hash=value.report_hash,
        math_version='own-action-retain-or-abstain.v2', math_identity_hash=content_hash(math_identity()),
        raw_probability=raw_probability, calibrated_probability=calibrated_scalar(raw_probability, report),
        retain_threshold=value.inputs.plan.policy.retain_threshold,
    )


def load_probability_activation_in_session(session: Session, activation_id: str) -> ProbabilityActivation:
    _require_read_snapshot(session)
    _require_producer_loaded()
    row = session.get(StrategyProbabilityActivationRecord, activation_id, populate_existing=True)
    if row is None or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash:
        raise ValueError('probability_activation_original_missing_or_changed')
    value = ProbabilityActivation.model_validate_json(row.payload_json)
    if (canonical_json(value.model_dump(mode='json')) != row.payload_json or value.activation_id != row.activation_id
            or value.request_id != row.request_id or value.scope_market != row.market
            or value.scope_instrument != row.instrument or value.pack_definition_hash != row.pack_definition_hash
            or value.compiled_policy_recipe_hash != row.compiled_policy_recipe_hash
            or value.server_known_at != as_utc(row.server_known_at)
            or value.valid_from != as_utc(row.valid_from) or value.valid_until != as_utc(row.valid_until)):
        raise ValueError('probability_activation_original_invalid')
    for reference in value.references:
        production = load_production_in_session(session, reference.production_id, reference.payload_hash)
        if (production.state != 'passed' or production.report is None or production.server_known_at > value.server_known_at
                or production.scope.market != value.scope_market or production.scope.instrument != value.scope_instrument
                or production.scope.stable_recipe_hash != value.stable_recipe_hash
                or production.scope.strategy_version != value.strategy_version
                or production.scope.pack_definition_hash != value.pack_definition_hash
                or production.scope.compiled_policy_recipe_hash != value.compiled_policy_recipe_hash
                or production.scope.horizon != reference.horizon
                or reference.scope_hash != content_hash(production.scope.model_dump(mode='json'))
                or reference.inputs_hash != production.inputs_hash or reference.report_hash != production.report_hash
                or reference.producer_hash != production.producer_hash):
            raise ValueError('probability_activation_production_mismatch')
    return value


def resolve_probability_activation_in_session(
    session: Session, *, market: str, instrument: str, pack_definition_hash: str,
    compiled_policy_recipe_hash: str, pit_cutoff_at: datetime,
) -> ProbabilityActivation | None:
    _require_read_snapshot(session)
    _require_producer_loaded()
    cutoff = aware_utc(pit_cutoff_at)
    rows = session.scalars(select(StrategyProbabilityActivationRecord).where(
        StrategyProbabilityActivationRecord.market == market, StrategyProbabilityActivationRecord.instrument == instrument,
        StrategyProbabilityActivationRecord.pack_definition_hash == pack_definition_hash,
        StrategyProbabilityActivationRecord.compiled_policy_recipe_hash == compiled_policy_recipe_hash,
        StrategyProbabilityActivationRecord.server_known_at <= cutoff,
        StrategyProbabilityActivationRecord.valid_from <= cutoff, StrategyProbabilityActivationRecord.valid_until > cutoff,
    ).limit(2).execution_options(populate_existing=True)).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('probability_activation_scope_ambiguous')
    return load_probability_activation_in_session(session, rows[0].activation_id)



# Product commands authenticate every window before adding any job. This state
# belongs only to the caller's actual physical transaction; it grants no rights.
@dataclass(frozen=True)
class _PlanRegistrationReadset:
    session: Session
    connection: Any
    native: sqlite3.Connection
    transaction: Any
    nested: Any
    changes: int
    source_identity: str
    archive: RawBodyArchive
    source_group: tuple[str, str, str, str, str]
    queries: tuple[tuple[str, tuple[Any, ...], str], ...]
    archive_records_json: str
    plans: tuple[tuple[str, str], ...]
    archive_identity: tuple[Any, ...] | None = None
    ancestry: Any = None
    signatures: Any = None
    retained: Any = None


_PLAN_REGISTRATIONS_KEY = object()


_REGISTRATION_PREPARATION_SEAL = object()


@dataclass(frozen=True)
class _PreparedRegistrationSources:
    """Private immutable data from a completed read snapshot, never a saved pass."""

    seal: Any
    source_identity: str
    source_group: tuple[str, str, str, str, str]
    assessed_at: datetime
    queries: tuple[tuple[str, tuple[Any, ...], str], ...]
    archive_records_json: str
    windows: tuple[tuple[str, str], ...]
    archive: RawBodyArchive
    archive_identity: tuple[Any, ...]
    ancestry: Any
    signatures: Any
    retained: Any
    digest: str


def _registration_archive_identity(archive: RawBodyArchive) -> tuple[Any, ...]:
    reader = getattr(archive.read, '__func__', archive.read)
    return type(archive), archive.root, reader, getattr(reader, '__code__', None)


def _prepared_registration_digest(
    source_identity: str, source_group: tuple[str, str, str, str, str], assessed_at: datetime,
    queries: tuple[tuple[str, tuple[Any, ...], str], ...], archives: str,
    windows: tuple[tuple[str, str], ...],
    ancestry: Any, signatures: tuple[bytes, ...], retained: Any,
) -> str:
    return content_hash([source_identity, source_group, aware_utc(assessed_at).isoformat(),
        [(statement, [_registration_scalar(item) for item in parameters], digest)
         for statement, parameters, digest in queries], archives, windows,
        ancestry, [item.hex() for item in signatures], id(retained)])


def prepare_registration_sources_in_session(
    session: Session, *, requests: tuple[tuple[str, str], ...],
    policy: CalibrationEvaluationPolicy, approval_source_ref: str,
    assessed_at: datetime, archive: RawBodyArchive,
) -> _PreparedRegistrationSources:
    """Authenticate <=3 exact windows in the caller's SELECT-only snapshot.

    The temporary plans only structure authenticated scope/policy data. Their
    clocks are not registration clocks and no job is added or persisted here.
    """
    _require_producer_loaded()
    _require_read_snapshot(session)
    if (not 1 <= len(requests) <= 3 or len({item[0] for item in requests}) != len(requests)
            or len({item[1] for item in requests}) != len(requests)
            or _PLAN_REGISTRATIONS_KEY in session.info):
        raise ValueError('calibration_registration_preparation_invalid')
    now = aware_utc(assessed_at)
    try:
        with registration_originals_scope(session):
            for request_id, outlook_id in requests:
                register_plan_from_outlook_in_session(
                    session, request_id=request_id, outlook_id=outlook_id, policy=policy,
                    start_at=now, end_at=now + timedelta(days=90), registered_at=now,
                    approval_source_ref=approval_source_ref, archive=archive,
                )
            state = _registration_readset(session)
            ancestry, signatures = _original_ancestry(), _original_contract_signatures()
            retained_required, retained = capture_retained_original_identity(session, state.source_group[1])
            if signatures is None or (retained_required and retained is None):
                raise ValueError('calibration_registration_source_identity_unavailable')
        state = _registration_readset(session)
        verify_plan_registrations_in_session(session)
        digest = _prepared_registration_digest(state.source_identity, state.source_group, now,
            state.queries, state.archive_records_json, state.plans, ancestry, signatures, retained)
        return _PreparedRegistrationSources(_REGISTRATION_PREPARATION_SEAL,
            state.source_identity, state.source_group, now, state.queries,
            state.archive_records_json, state.plans, archive,
            _registration_archive_identity(archive), ancestry, signatures, retained, digest)
    finally:
        session.info.pop(_PLAN_REGISTRATIONS_KEY, None)


def bind_registration_sources_in_session(
    session: Session, *, originals: _PreparedRegistrationSources, registered_at: datetime,
    end_at: datetime, archive: RawBodyArchive,
) -> tuple[tuple[str, CalibrationPlan], ...]:
    """Re-read all originals and create a new, clean writer-bound readset.

    No Session/Connection/transaction or cached permission is transferred from
    the preparation. The enclosing writer also verifies PreparedOriginals.
    """
    _require_producer_loaded()
    _require_read_snapshot(session)
    now, end = aware_utc(registered_at), aware_utc(end_at)
    if (not isinstance(originals, _PreparedRegistrationSources)
            or originals.seal is not _REGISTRATION_PREPARATION_SEAL
            or not 1 <= len(originals.windows) <= 3
            or originals.archive is not archive
            or originals.archive_identity != _registration_archive_identity(archive)
            or originals.ancestry != _original_ancestry()
            or originals.signatures != _original_contract_signatures()
            or (originals.retained is not None
                and not originals.retained.matches(session, originals.source_group[1]))
            or originals.source_identity != _registration_source_identity()
            or now < originals.assessed_at or now >= end
            or _PLAN_REGISTRATIONS_KEY in session.info
            or originals.digest != _prepared_registration_digest(originals.source_identity,
                originals.source_group, originals.assessed_at, originals.queries,
                originals.archive_records_json, originals.windows,
                originals.ancestry, originals.signatures, originals.retained)):
        raise ValueError('calibration_registration_preparation_changed')
    connection = session.connection()
    native = connection.connection.driver_connection
    if session.get_nested_transaction() is not None:
        raise ValueError('calibration_registration_transaction_changed')
    changes = native.total_changes
    # Fresh range/absence checks, including the immutable source rows.
    for statement, parameters, digest in originals.queries:
        if _registration_query_digest(native, statement, parameters) != digest:
            raise ValueError('calibration_registration_original_changed')
    _verify_archives(json.loads(originals.archive_records_json), archive)
    plans = []
    for outlook_id, body in originals.windows:
        old = CalibrationPlan.model_validate_json(body)
        # Use the actual writer clock and run all plan/model validators again.
        plan = CalibrationPlan.model_validate_json(CalibrationPlan(
            job_id=old.job_id, request_id=old.request_id, scope=old.scope, policy=old.policy,
            registered_at=now, start_at=now, end_at=end,
            approval_source_ref=old.approval_source_ref,
        ).model_dump_json())
        plans.append((outlook_id, plan))
    if (len({plan.scope.horizon for _identity, plan in plans}) != len(plans)
            or native.total_changes != changes or not native.in_transaction
            or session.new or session.dirty or session.deleted):
        raise ValueError('calibration_registration_preparation_changed')
    session.info[_PLAN_REGISTRATIONS_KEY] = _PlanRegistrationReadset(
        session, connection, native, session.get_transaction(), session.get_nested_transaction(), changes,
        originals.source_identity, archive, originals.source_group, originals.queries,
        originals.archive_records_json,
        tuple((identity, canonical_json(plan.model_dump(mode='json'))) for identity, plan in plans),
        originals.archive_identity, originals.ancestry, originals.signatures, originals.retained,
    )
    verify_plan_registrations_in_session(session)
    return tuple(plans)


@dataclass
class _RegistrationMaterials:
    session: Session
    connection: Any
    native: sqlite3.Connection
    transaction: Any
    nested: Any
    changes: int
    thread: int
    task: Any
    invalidated: bool = False
    run_id: str | None = None
    assessed_at: datetime | None = None
    archive: RawBodyArchive | None = None
    archive_identity: tuple[Any, ...] | None = None
    source_identity: str | None = None
    ancestry: Any = None
    signatures: Any = None
    retained: Any = None
    common: Any = None
    common_digest: str | None = None
    scope_body: str | None = None
    scope_digest: str | None = None


_REGISTRATION_MATERIALS_KEY = object()


def _registration_task() -> Any:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _registration_materials(session: Session) -> _RegistrationMaterials | None:
    value = session.info.get(_REGISTRATION_MATERIALS_KEY)
    return value if isinstance(value, _RegistrationMaterials) and value.session is session else None


def _check_registration_materials(state: _RegistrationMaterials, session: Session, run_id: str) -> None:
    if (state.invalidated or state.session is not session or state.connection is not session.connection()
            or state.native is not state.connection.connection.driver_connection or not state.native.in_transaction
            or state.transaction is not session.get_transaction() or not state.transaction.is_active
            or state.nested is not session.get_nested_transaction() or state.nested is not None
            or state.native.total_changes != state.changes or session.new or session.dirty or session.deleted
            or state.thread != threading.get_ident() or state.task is not _registration_task()
            or (state.run_id is not None and state.run_id != run_id)):
        state.invalidated = True
        raise ValueError('calibration_registration_material_scope_changed')
    if state.common is not None:
        _require_source_original_methods()
        if (state.common.digest()[0] != state.common_digest
                or (state.scope_body is not None
                    and hashlib.sha256(state.scope_body.encode()).hexdigest() != state.scope_digest)):
            state.invalidated = True
            raise ValueError('calibration_registration_material_data_changed')
        archive_type, archive_root, archive_reader, archive_code = state.archive_identity
        current_reader = getattr(state.archive.read, '__func__', state.archive.read)
        if (type(state.archive) is not archive_type or state.archive.root != archive_root
                or current_reader is not archive_reader
                or getattr(getattr(current_reader, '__wrapped__', current_reader), '__code__', None) is not archive_code):
            state.invalidated = True
            raise ValueError('calibration_registration_archive_identity_changed')
        if (_registration_source_identity() != state.source_identity
                or _original_ancestry() != state.ancestry
                or _original_contract_signatures() != state.signatures
                or (state.retained is not None and not state.retained.matches(session, run_id))):
            state.invalidated = True
            raise ValueError('calibration_registration_material_identity_changed')
        existing = session.info.get(_PLAN_REGISTRATIONS_KEY)
        if existing is not None:
            readset = _registration_readset(session)
            for statement, parameters, digest in readset.queries:
                if _registration_query_digest(state.native, statement, parameters) != digest:
                    state.invalidated = True
                    raise ValueError('calibration_registration_original_changed')
            _verify_archives(json.loads(readset.archive_records_json), readset.archive)


def _registration_source_originals(session: Session, run_id: str, assessed_at: datetime | None = None,
                                   archive: RawBodyArchive | None = None) -> Any:
    state = _registration_materials(session)
    if state is None or state.common is None:
        return None
    _check_registration_materials(state, session, run_id)
    actual_archive = archive.archive if isinstance(archive, _ArchiveRecorder) else archive
    if ((assessed_at is not None and aware_utc(assessed_at) != state.assessed_at)
            or (actual_archive is not None and actual_archive is not state.archive)):
        raise ValueError('calibration_registration_material_request_changed')
    return state.common


def _store_registration_source_originals(session: Session, run_id: str, assessed_at: datetime,
                                         archive: RawBodyArchive | None, originals: Any) -> None:
    state = _registration_materials(session)
    if state is None:
        return
    _check_registration_materials(state, session, run_id)
    actual_archive = archive.archive if isinstance(archive, _ArchiveRecorder) else archive
    if not isinstance(actual_archive, RawBodyArchive):
        return
    signatures = _original_contract_signatures()
    retained_required, retained = capture_retained_original_identity(session, run_id)
    if signatures is None or (retained_required and retained is None):
        return  # Unknown old/live contracts retain the full original reader.
    state.run_id, state.assessed_at, state.archive = run_id, aware_utc(assessed_at), actual_archive
    reader = getattr(actual_archive.read, '__func__', actual_archive.read)
    state.archive_identity = (type(actual_archive), actual_archive.root, reader,
        getattr(getattr(reader, '__wrapped__', reader), '__code__', None))
    state.ancestry, state.signatures, state.retained = _original_ancestry(), signatures, retained
    _require_source_original_methods()
    digest, size = originals.digest()
    if size > 4 * 1024 * 1024:
        return  # Oversized material uses the complete original reader.
    state.source_identity = _registration_source_identity()
    state.common, state.common_digest = originals, digest
    _check_registration_materials(state, session, run_id)


@contextmanager
def registration_originals_scope(session: Session):
    """Private, at most three clean same-report prepares; no caller proof input."""
    _require_producer_loaded()
    _require_read_snapshot(session)
    if session.get_nested_transaction() is not None or _REGISTRATION_MATERIALS_KEY in session.info:
        raise ValueError('calibration_registration_material_scope_invalid')
    connection = session.connection()
    native = connection.connection.driver_connection
    state = _RegistrationMaterials(session, connection, native, session.get_transaction(),
        session.get_nested_transaction(), native.total_changes, threading.get_ident(), _registration_task())
    def invalidate(*_args: Any) -> None:
        state.invalidated = True
    def statement(_connection, _cursor, sql, _parameters, _context, executemany):
        if executemany or not sql.lstrip().upper().startswith('SELECT '):
            state.invalidated = True
    event.listen(session, 'before_flush', invalidate)
    event.listen(connection, 'before_cursor_execute', statement)
    session.info[_REGISTRATION_MATERIALS_KEY] = state
    try:
        with session.no_autoflush:
            yield
        if state.run_id is not None:
            _check_registration_materials(state, session, state.run_id)
    finally:
        state.invalidated = True
        state.common = state.scope_body = None
        session.info.pop(_REGISTRATION_MATERIALS_KEY, None)
        event.remove(connection, 'before_cursor_execute', statement)
        event.remove(session, 'before_flush', invalidate)


def _registration_scalar(value: Any) -> Any:
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return ['float', value.hex()]
    if type(value) is bytes:
        return ['bytes', value.hex()]
    raise ValueError('calibration_registration_sql_value_invalid')


def _registration_query_digest(native: sqlite3.Connection, statement: str,
                               parameters: tuple[Any, ...]) -> str:
    cursor = native.execute(statement, parameters)
    try:
        columns = tuple(item[0] for item in cursor.description or ())
        digest = hashlib.sha256(canonical_json(columns).encode())
        count = size = 0
        for row in cursor:
            encoded = canonical_json([_registration_scalar(value) for value in row]).encode()
            count += 1
            size += len(encoded)
            if count > 100_000 or size > 128 * 1024 * 1024:
                raise ValueError('calibration_registration_rows_limit')
            digest.update(b'\0' + encoded)
        digest.update(f'\0rows:{count}'.encode())
        return digest.hexdigest()
    finally:
        cursor.close()


def _registration_source_identity() -> str:
    return content_hash(_producer_identity().model_dump(mode='json'))


def _registration_readset(session: Session) -> _PlanRegistrationReadset:
    state = session.info.get(_PLAN_REGISTRATIONS_KEY)
    connection = session.connection()
    native = connection.connection.driver_connection
    if (not isinstance(state, _PlanRegistrationReadset) or state.session is not session
            or state.connection is not connection or state.native is not native
            or not isinstance(native, sqlite3.Connection) or not native.in_transaction
            or state.transaction is not session.get_transaction()
            or state.nested is not session.get_nested_transaction()
            or not state.transaction.is_active or state.nested is not None):
        raise ValueError('calibration_registration_transaction_changed')
    return state


def _registered_plan(row: StrategyCalibrationJobRecord) -> CalibrationPlan:
    plan = CalibrationPlan.model_validate_json(row.plan_json)
    body = canonical_json(plan.model_dump(mode='json'))
    if (row.plan_json != body or row.plan_hash != content_hash(plan.model_dump(mode='json'))
            or row.job_id != plan.job_id or row.request_id != plan.request_id
            or as_utc(row.registered_at) != plan.registered_at):
        raise ValueError('calibration_plan_original_invalid')
    return plan


def register_plan_from_outlook_in_session(
    session: Session, *, request_id: str, outlook_id: str,
    policy: CalibrationEvaluationPolicy, start_at: datetime, end_at: datetime,
    approval_source_ref: str, registered_at: datetime, archive: RawBodyArchive,
) -> CalibrationPlan:
    """Prepare one authenticated plan; do not add/flush/commit anything.

    The trusted command supplies its one actual server registration clock and
    exact original Outlook ID, not an API-provided scope or approval identity.
    Prepare all <=3 windows before persist_plan_in_session; the caller owns its
    BEGIN IMMEDIATE, final verification, flush and whole-command rollback.
    """
    _require_producer_loaded()
    _require_read_snapshot(session)
    if session.get_nested_transaction() is not None or not isinstance(archive, RawBodyArchive):
        raise ValueError('calibration_registration_physical_writer_required')
    now = aware_utc(registered_at)
    old_state = session.info.get(_PLAN_REGISTRATIONS_KEY)
    if old_state is not None:
        old_state = _registration_readset(session)
        if (old_state.native.total_changes != old_state.changes or old_state.archive is not archive
                or len(old_state.plans) >= 3):
            raise ValueError('calibration_registration_prepare_scope_changed')
    connection = session.connection()
    native = connection.connection.driver_connection
    transaction, nested, changes = session.get_transaction(), session.get_nested_transaction(), native.total_changes
    source_identity = _registration_source_identity()
    if old_state is not None and old_state.source_identity != source_identity:
        raise ValueError('calibration_registration_source_changed')
    queries: dict[tuple[str, str], tuple[str, tuple[Any, ...]]] = {}
    recorder = _ArchiveRecorder(archive)

    def capture(_connection: Any, _cursor: Any, statement: str, parameters: Any,
                _context: Any, executemany: bool) -> None:
        if executemany or not statement.lstrip().upper().startswith('SELECT '):
            raise ValueError('calibration_registration_select_only')
        values = tuple(parameters)
        key = statement, canonical_json([_registration_scalar(value) for value in values])
        queries[key] = statement, values
        if len(queries) > 8192:
            raise ValueError('calibration_registration_queries_limit')

    def flush_forbidden(*_args: Any) -> None:
        raise ValueError('calibration_registration_flush_forbidden')

    event.listen(connection, 'before_cursor_execute', capture)
    event.listen(session, 'before_flush', flush_forbidden)
    try:
        with session.no_autoflush:
            row = session.get(AnalyticalOutlookRecord, outlook_id, populate_existing=True)
            if row is None or row.method_family != 'ai_synthesis' or row.study_id is None:
                raise ValueError('calibration_ai_study_outlook_required')
            source_group = row.study_id, row.run_id, row.artifact_id, row.market, row.instrument
            stable, _refs, forward = _source(session, row, now, archive=recorder)
            if not forward:
                raise ValueError('calibration_prospective_source_required')
            identities, _graph, _selected = _source_scope(session, row, stable)
            scope = AIProbabilityScope(
                market=row.market, instrument=row.instrument, horizon=row.horizon,
                strategy_version=ai_probability_strategy_version(stable),
                **{key: value for key, value in identities.items() if key != 'compiled_policy_hash'},
            )
            if old_state is not None and (old_state.source_group != source_group
                    or any(original_id == outlook_id or json.loads(body)['scope']['horizon'] == scope.horizon
                           for original_id, body in old_state.plans)):
                raise ValueError('calibration_registration_windows_invalid')
            # Deep JSON validation isolates the immutable plan from caller models.
            plan = CalibrationPlan.model_validate_json(CalibrationPlan(
                job_id='caljob_' + uuid.uuid4().hex, request_id=request_id, scope=scope,
                policy=policy, registered_at=now, start_at=aware_utc(start_at),
                end_at=aware_utc(end_at), approval_source_ref=approval_source_ref,
            ).model_dump_json())
            if plan.policy.purpose != 'prospective_research_calibration':
                raise ValueError('calibration_registration_prospective_policy_required')
        captured = tuple((statement, parameters, _registration_query_digest(native, statement, parameters))
                         for statement, parameters in queries.values())
        if (native.total_changes != changes or not native.in_transaction
                or session.get_transaction() is not transaction or session.get_nested_transaction() is not nested
                or session.new or session.dirty or session.deleted
                or _registration_source_identity() != source_identity):
            raise ValueError('calibration_registration_read_scope_changed')
    finally:
        event.remove(session, 'before_flush', flush_forbidden)
        event.remove(connection, 'before_cursor_execute', capture)
    # Job lookup is outside the source readset: these are the new output rows.
    with session.no_autoflush:
        previous = session.scalar(select(StrategyCalibrationJobRecord).where(
            StrategyCalibrationJobRecord.request_id == request_id).execution_options(populate_existing=True))
    if previous is not None:
        saved = _registered_plan(previous)
        if any((saved.scope != plan.scope, saved.policy != plan.policy, saved.start_at != plan.start_at,
                saved.end_at != plan.end_at, saved.approval_source_ref != plan.approval_source_ref)):
            raise ValueError('calibration_plan_request_conflict')
        plan = saved
    if old_state is not None:
        original = CalibrationPlan.model_validate_json(old_state.plans[0][1])
        if any((plan.registered_at != original.registered_at, plan.start_at != original.start_at,
                plan.end_at != original.end_at, plan.policy != original.policy,
                plan.approval_source_ref != original.approval_source_ref)):
            raise ValueError('calibration_registration_group_changed')
    combined: dict[tuple[str, str], tuple[str, tuple[Any, ...], str]] = {}
    for statement, parameters, digest in (*(old_state.queries if old_state else ()), *captured):
        key = statement, canonical_json([_registration_scalar(value) for value in parameters])
        if key in combined and combined[key][2] != digest:
            raise ValueError('calibration_registration_original_changed')
        combined[key] = statement, parameters, digest
    if len(combined) > 8192:
        raise ValueError('calibration_registration_queries_limit')
    records = {item['ref']: item for item in json.loads(old_state.archive_records_json)} if old_state else {}
    for ref, record in recorder.records.items():
        if ref in records and records[ref] != record:
            raise ValueError('calibration_registration_archive_changed')
        records[ref] = record
    if len(records) > 4096:
        raise ValueError('calibration_registration_archives_limit')
    session.info[_PLAN_REGISTRATIONS_KEY] = _PlanRegistrationReadset(
        session, connection, native, transaction, nested, changes, source_identity, archive, source_group,
        tuple(combined.values()),
        canonical_json([records[ref] for ref in sorted(records)]),
        (*(old_state.plans if old_state else ()), (outlook_id, canonical_json(plan.model_dump(mode='json')))),
    )
    return plan


def persist_plan_in_session(session: Session, *, plan: CalibrationPlan) -> CalibrationPlan:
    """Add only a privately authenticated plan; caller owns flush and commit."""
    _require_producer_loaded()
    state = _registration_readset(session)
    body = canonical_json(plan.model_dump(mode='json'))
    if body not in {item[1] for item in state.plans} or session.dirty or session.deleted:
        raise ValueError('calibration_registration_plan_not_prepared')
    with session.no_autoflush:
        pending = [row for row in session.new if isinstance(row, StrategyCalibrationJobRecord)
                   and (row.request_id == plan.request_id or row.job_id == plan.job_id)]
        old = session.scalar(select(StrategyCalibrationJobRecord).where(
            or_(StrategyCalibrationJobRecord.request_id == plan.request_id,
                StrategyCalibrationJobRecord.job_id == plan.job_id)).execution_options(populate_existing=True))
    if len(pending) > 1 or (pending and old is not None):
        raise ValueError('calibration_plan_request_conflict')
    if pending or old is not None:
        existing = _registered_plan(pending[0] if pending else old)
        if existing != plan:
            raise ValueError('calibration_plan_request_conflict')
        return existing
    session.add(StrategyCalibrationJobRecord(
        job_id=plan.job_id, request_id=plan.request_id, plan_json=body,
        plan_hash=content_hash(plan.model_dump(mode='json')), registered_at=plan.registered_at,
        status='pending', attempt=0, next_attempt_at=plan.registered_at,
    ))
    return plan


def verify_plan_registrations_in_session(session: Session) -> None:
    """Fresh original readback before/after caller writes; never flush its UOW."""
    _require_producer_loaded()
    state = _registration_readset(session)
    before = state.native.total_changes
    if (state.archive_identity is not None
            and state.archive_identity != _registration_archive_identity(state.archive)):
        raise ValueError('calibration_registration_archive_identity_changed')
    with session.no_autoflush:
        if (state.signatures is not None and (
                state.ancestry != _original_ancestry() or state.signatures != _original_contract_signatures()
                or (state.retained is not None and not state.retained.matches(session, state.source_group[1])))):
            raise ValueError('calibration_registration_source_identity_changed')
    if _registration_source_identity() != state.source_identity:
        raise ValueError('calibration_registration_source_changed')
    with session.no_autoflush:
        for statement, parameters, digest in state.queries:
            if _registration_query_digest(state.native, statement, parameters) != digest:
                raise ValueError('calibration_registration_original_changed')
        _verify_archives(json.loads(state.archive_records_json), state.archive)
    if (state.native.total_changes != before or _registration_readset(session) is not state
            or _registration_source_identity() != state.source_identity):
        raise ValueError('calibration_registration_verify_scope_changed')


def _waiting_refresh_due_at(completed_at: datetime, next_boundary_at: datetime | None) -> datetime:
    """Refresh sampling independently of the final calibration interval."""
    refresh_at = completed_at + timedelta(minutes=5)
    if next_boundary_at is not None and next_boundary_at > completed_at:
        refresh_at = min(refresh_at, next_boundary_at)
    return refresh_at


class KernelCalibrationProducer:
    """Explicit Kernel plans/approvals and one fair persisted due-job consumer."""

    def __init__(self, database: Database, *, archive: RawBodyArchive, clock=utcnow) -> None:
        self.database, self.archive, self.clock = database, archive, clock
        configure_probability_archive(database, archive)

    def register_plan(self, *, request_id: str, scope: AIProbabilityScope,
                      policy: CalibrationEvaluationPolicy, start_at: datetime, end_at: datetime,
                      approval_source_ref: str) -> CalibrationPlan:
        _require_producer_loaded()
        now = aware_utc(self.clock())
        if scope.strategy_version != ai_probability_strategy_version(scope.stable_recipe_hash):
            raise ValueError('calibration_ai_strategy_scope_invalid')
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            old = session.scalar(select(StrategyCalibrationJobRecord).where(
                StrategyCalibrationJobRecord.request_id == request_id))
            if old is not None:
                parsed = CalibrationPlan.model_validate_json(old.plan_json)
                if old.plan_hash != content_hash(parsed.model_dump(mode='json')) or any((
                    parsed.scope != scope, parsed.policy != policy, parsed.start_at != aware_utc(start_at),
                    parsed.end_at != aware_utc(end_at), parsed.approval_source_ref != approval_source_ref,
                )):
                    raise ValueError('calibration_plan_request_conflict')
                return parsed
            plan = CalibrationPlan(job_id='caljob_' + uuid.uuid4().hex, request_id=request_id,
                                   scope=scope, policy=policy, registered_at=now, start_at=aware_utc(start_at),
                                   end_at=aware_utc(end_at), approval_source_ref=approval_source_ref)
            session.add(StrategyCalibrationJobRecord(
                job_id=plan.job_id, request_id=request_id, plan_json=canonical_json(plan.model_dump(mode='json')),
                plan_hash=content_hash(plan.model_dump(mode='json')), registered_at=now,
                status='pending', attempt=0, next_attempt_at=now,
            ))
            return plan

    def register_plan_from_outlook(self, *, request_id: str, outlook_id: str,
                                   policy: CalibrationEvaluationPolicy, start_at: datetime,
                                   end_at: datetime, approval_source_ref: str) -> CalibrationPlan:
        """Explicit ops approval of one authenticated AI recipe; never choose latest."""
        _require_producer_loaded()
        now = aware_utc(self.clock())
        def read(session: Session) -> AIProbabilityScope:
            session.info['kernel_calibration_archive'] = self.archive
            row = session.get(AnalyticalOutlookRecord, outlook_id, populate_existing=True)
            if row is None or row.method_family != 'ai_synthesis':
                raise ValueError('calibration_ai_outlook_required')
            stable, _refs, forward = _source(session, row, now, archive=self.archive)
            if not forward:
                raise ValueError('calibration_prospective_source_required')
            identities, _graph, _selected = _source_scope(session, row, stable)
            return AIProbabilityScope(
                market=row.market, instrument=row.instrument, horizon=row.horizon,
                strategy_version=ai_probability_strategy_version(stable),
                **{key: value for key, value in identities.items() if key != 'compiled_policy_hash'},
            )
        prepared = prepare_originals(self.database, read)
        # Registration is prospective and records the exact approved recipe. It
        # grants no pass; sampling will authenticate every source again.
        return self.register_plan(request_id=request_id, scope=prepared.value, policy=policy,
                                  start_at=start_at, end_at=end_at, approval_source_ref=approval_source_ref)

    def status(self) -> dict[str, Any]:
        with self.database.session() as session:
            rows = session.scalars(select(StrategyCalibrationJobRecord).order_by(
                StrategyCalibrationJobRecord.registered_at, StrategyCalibrationJobRecord.job_id).limit(257)).all()
            if len(rows) > 256:
                return {'state': 'bounded_status_incomplete', 'plans': [], 'probability_qualified': False}
            return {'state': 'no_registered_plan' if not rows else 'registered',
                    'probability_qualified': False,
                    'plans': [{'job_id': row.job_id, 'status': row.status, 'error_code': row.error_code,
                               'last_production_id': row.last_production_id,
                               'next_attempt_at': as_utc(row.next_attempt_at).isoformat()} for row in rows]}

    def _retry_claim(
        self, *, job_id: str, owner: str, attempt: int, plan_hash: str,
        error_code: str,
    ) -> bool:
        """Release only this worker's exact claim after synchronous work stopped."""
        now = aware_utc(self.clock())
        with self.database.session() as session:
            changed = session.execute(update(StrategyCalibrationJobRecord).where(
                StrategyCalibrationJobRecord.job_id == job_id,
                StrategyCalibrationJobRecord.status == 'running',
                StrategyCalibrationJobRecord.lease_owner == owner,
                StrategyCalibrationJobRecord.attempt == attempt,
                StrategyCalibrationJobRecord.plan_hash == plan_hash,
            ).values(
                status='retry', next_attempt_at=now + timedelta(minutes=5),
                lease_owner=None, lease_expires_at=None, error_code=error_code,
            ).execution_options(synchronize_session=False)).rowcount
            return changed == 1

    async def process_due(self, *, limit: int = 1, deadline_at: float | None = None) -> int:
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('calibration_due_limit_invalid')
        if deadline_at is not None and (type(deadline_at) not in (int, float) or not math.isfinite(deadline_at)):
            raise ValueError('calibration_deadline_invalid')
        # Convert the actual enclosing event-loop deadline, never grant a new budget.
        deadline = (monotonic() + (deadline_at - asyncio.get_running_loop().time())
                    if deadline_at is not None else None)
        stopped = threading.Event()

        def checkpoint() -> None:
            _calibration_checkpoint(stopped, deadline)

        task = asyncio.create_task(asyncio.to_thread(self._process_due, limit, checkpoint))
        try:
            return await asyncio.shield(task)
        except _CalibrationStopped:
            raise TimeoutError('calibration_deadline_elapsed') from None
        except asyncio.CancelledError:
            stopped.set()
            # Retain one owner and join the rollback/commit outcome; never detach.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise

    def _process_due(self, limit: int, checkpoint: Callable[[], None] | None = None) -> int:
        processed = 0
        source_memo: dict[Any, Any] = {}
        source_manifest: str | None = None
        for _ in range(limit):
            if checkpoint is not None:
                checkpoint()
            _require_producer_loaded()
            if checkpoint is not None:
                checkpoint()
            now = aware_utc(self.clock())
            owner = 'calworker_' + uuid.uuid4().hex
            with self.database.session() as session:
                if checkpoint is not None:
                    checkpoint()
                session.connection().exec_driver_sql('BEGIN IMMEDIATE')
                if checkpoint is not None:
                    checkpoint()
                job = session.scalar(select(StrategyCalibrationJobRecord).where(or_(
                    (StrategyCalibrationJobRecord.status.in_(('pending', 'waiting', 'retry')) &
                     (StrategyCalibrationJobRecord.next_attempt_at <= now)),
                    ((StrategyCalibrationJobRecord.status == 'running') &
                     (StrategyCalibrationJobRecord.lease_expires_at <= now)),
                )).order_by(StrategyCalibrationJobRecord.next_attempt_at, StrategyCalibrationJobRecord.job_id).limit(1))
                if job is None:
                    break
                plan_json, plan_hash = job.plan_json, job.plan_hash
                plan = CalibrationPlan.model_validate_json(plan_json)
                if content_hash(plan.model_dump(mode='json')) != plan_hash or job.job_id != plan.job_id:
                    raise ValueError('calibration_plan_original_invalid')
                job.status, job.lease_owner = 'running', owner
                job.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
                job.attempt += 1
                attempt = job.attempt
                if checkpoint is not None:
                    checkpoint()
            try:
                if source_manifest is None:
                    source_manifest = loaded_assessment_manifest_hash(
                        memo=source_memo, checkpoint=checkpoint,
                    )
                phase = 'capture'
                readiness = prepare_originals(self.database, lambda session: _cohort_upper_bound(
                    session, plan, now), checkpoint=checkpoint)
                upper_bound = readiness.value
                if upper_bound is not None and upper_bound < plan.policy.thresholds.min_samples:
                    # Scheduling only: no FrozenInputs, production, or qualification.
                    phase = 'writer'
                    with self.database.session() as session:
                        if checkpoint is not None:
                            checkpoint()
                        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
                        if checkpoint is not None:
                            checkpoint()
                        readiness.verify(session, checkpoint=checkpoint)
                        end = aware_utc(self.clock())
                        job = session.get(StrategyCalibrationJobRecord, plan.job_id, populate_existing=True)
                        if (job is None or job.status != 'running' or job.lease_owner != owner
                                or job.plan_json != plan_json or job.plan_hash != plan_hash
                                or job.lease_expires_at is None or as_utc(job.lease_expires_at) <= end):
                            raise ValueError('calibration_lease_lost')
                        changed = session.execute(update(StrategyCalibrationJobRecord).where(
                            StrategyCalibrationJobRecord.job_id == plan.job_id,
                            StrategyCalibrationJobRecord.status == 'running',
                            StrategyCalibrationJobRecord.lease_owner == owner,
                            StrategyCalibrationJobRecord.lease_expires_at > end,
                            StrategyCalibrationJobRecord.plan_hash == plan_hash,
                            StrategyCalibrationJobRecord.plan_json == plan_json,
                        ).values(status='waiting', next_attempt_at=_waiting_refresh_due_at(end, None),
                                 lease_owner=None, lease_expires_at=None)
                            .execution_options(synchronize_session=False)).rowcount
                        if changed != 1:
                            raise ValueError('calibration_lease_lost')
                        readiness.verify_after_writes(session, checkpoint=checkpoint)
                        if checkpoint is not None:
                            checkpoint()
                    # The return count still means actual productions, not readiness polls.
                    continue
                phase = 'capture'
                prepared = prepare_originals(self.database, lambda session: _capture_inputs(
                    session, plan, now=now, archive=self.archive, checkpoint=checkpoint,
                    source_memo=source_memo),
                    checkpoint=checkpoint)
                inputs, archives = prepared.value
                if checkpoint is not None:
                    checkpoint()
                phase = 'identity'
                identity = _producer_identity()
                if checkpoint is not None:
                    checkpoint()
                evaluated = aware_utc(self.clock())
                phase = 'mathematics'
                report = (evaluate_authenticated_forecast_calibration(inputs, evaluated_at=evaluated)
                          if not inputs.reasons else None)
                if checkpoint is not None:
                    checkpoint()
                fresh_manifest = loaded_assessment_manifest_hash(checkpoint=checkpoint)
                if fresh_manifest != source_manifest:
                    raise ValueError('assessment_loaded_verifier_source_changed')
                phase = 'writer'
                with self.database.session() as session:
                    if checkpoint is not None:
                        checkpoint()
                    session.connection().exec_driver_sql('BEGIN IMMEDIATE')
                    if checkpoint is not None:
                        checkpoint()
                    prepared.verify(session, checkpoint=checkpoint)
                    _verify_archives(archives, self.archive, checkpoint=checkpoint)
                    if identity != _producer_identity():
                        raise ValueError('calibration_producer_identity_changed')
                    if checkpoint is not None:
                        checkpoint()
                    known = aware_utc(self.clock())
                    job = session.get(StrategyCalibrationJobRecord, plan.job_id, populate_existing=True)
                    if (job is None or job.status != 'running' or job.lease_owner != owner
                            or job.plan_json != plan_json or job.plan_hash != plan_hash
                            or job.lease_expires_at is None or as_utc(job.lease_expires_at) <= known):
                        raise ValueError('calibration_lease_lost')
                    inputs_hash = content_hash(inputs.model_dump(mode='json'))
                    producer_hash = content_hash(identity.model_dump(mode='json'))
                    old = session.scalar(select(StrategyCalibrationProductionRecord).where(
                        StrategyCalibrationProductionRecord.job_id == plan.job_id,
                        StrategyCalibrationProductionRecord.inputs_hash == inputs_hash,
                        StrategyCalibrationProductionRecord.producer_hash == producer_hash))
                    if old is None:
                        value = FrozenCalibrationProduction(
                            production_id='calprod_' + uuid.uuid4().hex, job_id=plan.job_id, scope=plan.scope,
                            captured_at=inputs.captured_at, server_known_at=known, inputs=inputs, inputs_hash=inputs_hash,
                            producer=identity, producer_hash=producer_hash, report=report,
                            report_hash=content_hash(report.model_dump(mode='json')) if report else None,
                            state=('passed' if report.passed else 'failed') if report else 'waiting',
                            reasons=tuple(report.failure_reasons) if report else inputs.reasons,
                        )
                        payload = canonical_json(value.model_dump(mode='json'))
                        session.add(StrategyCalibrationProductionRecord(
                            production_id=value.production_id, job_id=plan.job_id,
                            scope_hash=content_hash(plan.scope.model_dump(mode='json')), inputs_hash=inputs_hash,
                            producer_hash=producer_hash, sample_dataset_hash=report.dataset_hash if report else None,
                            captured_at=value.captured_at, server_known_at=known, state=value.state,
                            payload_json=payload, payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                            report_json=canonical_json(report.model_dump(mode='json')) if report else None,
                            report_hash=value.report_hash,
                        ))
                        session.flush()
                    else:
                        value = FrozenCalibrationProduction.model_validate_json(old.payload_json)
                        if (hashlib.sha256(old.payload_json.encode()).hexdigest() != old.payload_hash
                                or value.inputs != inputs or value.producer != identity):
                            raise ValueError('calibration_production_idempotency_conflict')
                    prepared.verify_after_writes(session, checkpoint=checkpoint)
                    _verify_archives(archives, self.archive, checkpoint=checkpoint)
                    end = aware_utc(self.clock())
                    status = 'completed' if value.state in {'passed', 'failed'} else 'waiting'
                    next_at = (_waiting_refresh_due_at(end, inputs.next_boundary_at)
                               if status == 'waiting'
                               else max(end + timedelta(minutes=5), inputs.next_boundary_at or end))
                    changed = session.execute(update(StrategyCalibrationJobRecord).where(
                        StrategyCalibrationJobRecord.job_id == plan.job_id,
                        StrategyCalibrationJobRecord.status == 'running', StrategyCalibrationJobRecord.lease_owner == owner,
                        StrategyCalibrationJobRecord.lease_expires_at > end,
                        StrategyCalibrationJobRecord.plan_hash == plan_hash,
                    ).values(status=status, next_attempt_at=next_at, lease_owner=None, lease_expires_at=None,
                             last_production_id=value.production_id, error_code=None)
                        .execution_options(synchronize_session=False)).rowcount
                    if changed != 1:
                        raise ValueError('calibration_lease_lost')
                    if checkpoint is not None:
                        checkpoint()
                processed += 1
            except _CalibrationStopped:
                self._retry_claim(
                    job_id=plan.job_id, owner=owner, attempt=attempt,
                    plan_hash=plan_hash, error_code='calibration_deadline_elapsed',
                )
                raise
            except (ValueError, OSError, KeyError, TypeError) as error:
                failure_code = _producer_failure_code(error, phase)
                if isinstance(error, (TypeError, KeyError)):
                    _LOGGER.exception('calibration_producer_unexpected_failure', extra={
                        'job_id': plan.job_id, 'phase': phase, 'error_code': failure_code,
                    })
                with self.database.session() as session:
                    session.connection().exec_driver_sql('BEGIN IMMEDIATE')
                    end = aware_utc(self.clock())
                    session.execute(update(StrategyCalibrationJobRecord).where(
                        StrategyCalibrationJobRecord.job_id == plan.job_id,
                        StrategyCalibrationJobRecord.status == 'running', StrategyCalibrationJobRecord.lease_owner == owner,
                    ).values(status='retry', next_attempt_at=end + timedelta(minutes=5), lease_owner=None,
                              lease_expires_at=None, error_code=failure_code)
                        .execution_options(synchronize_session=False))
        return processed

    def activate(self, *, request_id: str, references: tuple[CalibrationProductionReference, ...],
                 valid_from: datetime, valid_until: datetime, approval_source_ref: str) -> ProbabilityActivation:
        _require_producer_loaded()
        start, end = aware_utc(valid_from), aware_utc(valid_until)
        def authenticate(session: Session):
            recorder = _ArchiveRecorder(self.archive)
            values = [load_production_in_session(session, ref.production_id, ref.payload_hash,
                                                 archive=recorder) for ref in references]
            if not values or len(values) > 3:
                raise ValueError('probability_activation_productions_required')
            for value, ref in zip(values, references, strict=True):
                if (value.state != 'passed' or value.report is None or value.scope.horizon != ref.horizon
                        or content_hash(value.scope.model_dump(mode='json')) != ref.scope_hash
                        or value.inputs_hash != ref.inputs_hash or value.report_hash != ref.report_hash
                        or value.producer_hash != ref.producer_hash):
                    raise ValueError('probability_activation_production_mismatch')
            first = values[0].scope
            if any(any(getattr(value.scope, key) != getattr(first, key) for key in (
                'market', 'instrument', 'strategy_version', 'stable_recipe_hash', 'pack_definition_hash',
                'compiled_policy_recipe_hash', 'method_recipe_hash', 'graph_recipe_hash', 'input_definition',
            )) for value in values):
                raise ValueError('probability_activation_recipe_scope_mismatch')
            return first, list(recorder.records.values())
        prepared = prepare_originals(self.database, authenticate)
        with self.database.session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            prepared.verify(session)
            scope, archives = prepared.value
            _verify_archives(archives, self.archive)
            known = aware_utc(self.clock())
            old = session.scalar(select(StrategyProbabilityActivationRecord).where(
                StrategyProbabilityActivationRecord.request_id == request_id))
            if old is not None:
                value = load_probability_activation_in_session(session, old.activation_id)
                if (value.references != references or value.valid_from != start or value.valid_until != end
                        or value.approval_source_ref != approval_source_ref):
                    raise ValueError('probability_activation_request_conflict')
                return value
            if known > start or start >= end:
                raise ValueError('probability_activation_not_prospective')
            overlap = session.scalar(select(StrategyProbabilityActivationRecord.activation_id).where(
                StrategyProbabilityActivationRecord.market == scope.market,
                StrategyProbabilityActivationRecord.instrument == scope.instrument,
                StrategyProbabilityActivationRecord.pack_definition_hash == scope.pack_definition_hash,
                StrategyProbabilityActivationRecord.compiled_policy_recipe_hash == scope.compiled_policy_recipe_hash,
                StrategyProbabilityActivationRecord.valid_from < end,
                StrategyProbabilityActivationRecord.valid_until > start,
            ).limit(1))
            if overlap is not None:
                raise ValueError('probability_activation_interval_overlap')
            value = ProbabilityActivation(
                activation_id='probact_' + uuid.uuid4().hex, request_id=request_id,
                scope_market=scope.market, scope_instrument=scope.instrument, strategy_version=scope.strategy_version,
                stable_recipe_hash=scope.stable_recipe_hash, pack_definition_hash=scope.pack_definition_hash,
                compiled_policy_recipe_hash=scope.compiled_policy_recipe_hash, references=references,
                server_known_at=known, valid_from=start, valid_until=end, approval_source_ref=approval_source_ref,
            )
            payload = canonical_json(value.model_dump(mode='json'))
            session.add(StrategyProbabilityActivationRecord(
                activation_id=value.activation_id, request_id=request_id, market=scope.market, instrument=scope.instrument,
                pack_definition_hash=scope.pack_definition_hash, compiled_policy_recipe_hash=scope.compiled_policy_recipe_hash,
                server_known_at=known, valid_from=start, valid_until=end, payload_json=payload,
                payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
            ))
            session.flush()
            prepared.verify_after_writes(session)
            _verify_archives(archives, self.archive)
            if known > aware_utc(self.clock()) or start < aware_utc(self.clock()):
                raise ValueError('probability_activation_clock_changed')
            return value


# These dependencies are loaded on first use AFTER Commit's module has loaded.
# Their actual callable code and exact file bytes are checked; importing this
# module from Commit's legacy service cannot import Commit back through evals.
_DEPENDENCY_MODULES = {
    'packages.evals.analytical_assessment': ('_source', '_read_source_originals', '_require_source_original_methods', 'stable_hub_recipe'),
    'packages.evals.assessment_basis': ('_ArchiveRecorder', 'build_assessment_basis_in_session', 'loaded_assessment_manifest_hash'),
    'packages.evals.authenticated_forecast_calibration': ('_code_hash', 'calibrated_scalar', 'evaluate_authenticated_forecast_calibration', 'math_identity'),
    'packages.evals.forecast_calibration': ('ForecastCalibrationSample',),
    'packages.evals.input_receipts': ('verify_input_receipts',),
    'packages.evals.prospective_observations': ('_frozen_input', 'verify_recorded_label'),
    'packages.evals.recipe_observations': ('RecipeObservationCandidate', 'select_recipe_observations'),
    'packages.kernel.decision_hub_kernel.application.daily_execution_binding': ('DailyExecutionReceipt', 'read_daily_execution_in_session'),
    'packages.kernel.decision_hub_kernel.application.research_execution_binding': ('read_prepared_execution_in_session',),
    'packages.kernel.decision_hub_kernel.application.research_graph_binding': ('authenticated_graph_recipe_identity', 'parse_graph_final_projection_declaration', 'read_graph_round_in_session', '_original_ancestry', '_original_contract_signatures'),
    'packages.kernel.decision_hub_kernel.application.research_method': ('recipe_hash',),
    'packages.kernel.decision_hub_kernel.application.research_binding': ('FrozenResearchBundle', 'read_research_bundle_in_session'),
    'packages.kernel.decision_hub_kernel.application.research_graph_history_versions': ('capture_retained_original_identity',),
    'packages.kernel.decision_hub_kernel.persistence.original_preparation': ('prepare_originals',),
    'packages.provider_adapters.historical.research': ('loaded_daily_implementation',),
    'packages.contracts_py.decision_hub_contracts': ('ResearchSessionResult',),
}
_DEPENDENCIES: dict[str, tuple[Any, str, Any]] = {}
_DEPENDENCY_FILES: dict[Path, str] = {}
_DEPENDENCY_ABI: dict[str, str] = {}


def _normalized_callable_code(code: CodeType) -> CodeType:
    return code.replace(co_filename='<sealed-calibration-source>', co_consts=tuple(
        _normalized_callable_code(value) if isinstance(value, CodeType) else value for value in code.co_consts))


def _callable_abi(code: CodeType) -> str:
    return hashlib.sha256(marshal.dumps(_normalized_callable_code(code))).hexdigest()


def _find_callable_code(module: CodeType, qualname: str) -> CodeType:
    found = [module] if module.co_qualname == qualname else []
    for value in module.co_consts:
        if isinstance(value, CodeType):
            try:
                found.append(_find_callable_code(value, qualname))
            except LookupError:
                pass
    if len(found) != 1:
        raise LookupError('calibration_dependency_callable_unavailable')
    return found[0]


def _load_dependencies() -> None:
    import importlib
    global _DEPENDENCIES, _DEPENDENCY_FILES, _DEPENDENCY_ABI
    if not _DEPENDENCIES:
        dependencies, files, abi, aliases = {}, {}, {}, {}
        for module_name, names in _DEPENDENCY_MODULES.items():
            module = importlib.import_module(module_name)
            path = Path(module.__file__).resolve()
            files[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            for name in names:
                function = getattr(module, name)
                dependencies[name] = function, module_name, getattr(function, '__code__', None)
                aliases[name] = function
                code = getattr(function, '__code__', None)
                if code is not None:
                    source = Path(code.co_filename).resolve()
                    expected = _find_callable_code(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True), code.co_qualname)
                    abi[name] = _callable_abi(expected)
                    if _callable_abi(code) != abi[name]:
                        raise ValueError('calibration_dependency_not_loaded_from_exact_source')
                    files[source] = hashlib.sha256(source.read_bytes()).hexdigest()
        # Publish only a complete, successfully authenticated dependency set.
        # Any failed import/member/source check leaves all shared maps empty.
        globals().update(aliases)
        _DEPENDENCY_FILES, _DEPENDENCY_ABI = files, abi
        _DEPENDENCIES = dependencies
    for name, (function, module_name, code) in _DEPENDENCIES.items():
        module = importlib.import_module(module_name)
        if getattr(module, name) is not function or globals().get(name) is not function or getattr(function, '__code__', None) is not code:
            raise ValueError('calibration_loaded_dependency_changed')
        if code is not None and _callable_abi(code) != _DEPENDENCY_ABI[name]:
            raise ValueError('calibration_dependency_not_loaded_from_exact_source')
    for path, digest in _DEPENDENCY_FILES.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('calibration_loaded_dependency_source_changed')


def _require_producer_loaded() -> None:
    _load_dependencies()
    if (registration_originals_scope.__wrapped__ is not _REGISTRATION_SCOPE_IMPLEMENTATION
            or registration_originals_scope.__wrapped__.__code__ is not _REGISTRATION_SCOPE_CODE):
        raise ValueError('calibration_registration_scope_implementation_changed')
    for name, (function, code) in _PRODUCER_LOADED.items():
        if '.' in name:
            owner, member = name.split('.', 1)
            current = getattr(globals()[owner], member)
        else:
            current = globals().get(name)
        if current is not function or getattr(current, '__code__', None) is not code:
            raise ValueError('calibration_producer_loaded_implementation_changed')
    for path, digest in _PRODUCER_FILES.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('calibration_producer_source_changed')


_PRODUCER_NAMES = (
    '_calibration_checkpoint',
    'ai_probability_strategy_version', '_row', '_original_object', '_require_read_snapshot',
    '_producer_identity', '_source_scope', '_read_source_scope_originals', '_cost_original', '_regime_original',
    '_cohort_statement', '_cohort_rows', '_cohort_upper_bound',
    '_capture_inputs', '_verify_archives', '_producer_failure_code', '_verify_saved_inputs', 'load_production_in_session',
    'configure_probability_archive', '_probability_archive', 'transform_probability',
    'load_probability_activation_in_session', 'resolve_probability_activation_in_session',
    '_normalized_callable_code', '_callable_abi', '_find_callable_code',
    '_load_dependencies', '_require_producer_loaded', 'stable_compiled_policy_recipe_hash',
    '_registration_scalar', '_registration_query_digest', '_registration_source_identity',
    '_registration_task', '_registration_materials', '_check_registration_materials',
    '_registration_source_originals', '_store_registration_source_originals', 'registration_originals_scope',
    '_registration_readset', '_registered_plan', 'register_plan_from_outlook_in_session',
    '_registration_archive_identity', '_prepared_registration_digest',
    'prepare_registration_sources_in_session', 'bind_registration_sources_in_session',
    'persist_plan_in_session', 'verify_plan_registrations_in_session', '_waiting_refresh_due_at',
)
_REGISTRATION_SCOPE_IMPLEMENTATION = registration_originals_scope.__wrapped__
_REGISTRATION_SCOPE_CODE = _REGISTRATION_SCOPE_IMPLEMENTATION.__code__
_PRODUCER_LOADED = {name: (globals()[name], globals()[name].__code__) for name in _PRODUCER_NAMES}
for _name in (
    '__init__', 'register_plan', 'register_plan_from_outlook', 'status',
    '_retry_claim', 'process_due', '_process_due', 'activate',
):
    _function = getattr(KernelCalibrationProducer, _name)
    _PRODUCER_LOADED['KernelCalibrationProducer.' + _name] = (_function, _function.__code__)
for _name in ('__init__', 'read'):
    _function = getattr(_CheckpointArchive, _name)
    _PRODUCER_LOADED['_CheckpointArchive.' + _name] = (_function, _function.__code__)
_PRODUCER_FILES = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in {
    Path(__file__).resolve(), Path(__file__).with_name('calibration_inputs.py'),
}}
