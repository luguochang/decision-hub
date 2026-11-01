"""Kernel first-selection and math originals; no model or trading authority.

The math input is authenticated_graph_subjective_probability.v2. A selected
model's earlier value is separately recorded, never silently relabelled.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Generator, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from .calibration_inputs import FrozenCalibrationProduction, KernelCalibratedValue, ProbabilityActivation
from .research_method import canonical_recipe, recipe_hash
from ..persistence.db import RunRoleOutputRecord, SnapshotRecord, as_utc, utcnow

ROLE = "research_probability_binding"
SCHEMA = "research-probability-binding.v2"
VALUE_SCHEMA = "research-kernel-probability-values.v2"
INPUT_DEFINITION = "authenticated_graph_subjective_probability.v2"
HORIZONS = ("30m", "24h", "72h")
MAX_BYTES = 128 * 1024
_FIRST_FREEZE_KEY = "_research_probability_first_freeze"
Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=256)]
Horizon = Literal["30m", "24h", "72h"]
Phase = Literal["finalization", "commit"]

class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class KernelProbabilitySelection(_Closed):
    run_id: Identifier
    event_id: Identifier
    task_request_hash: Digest
    compiled_policy_hash: Digest
    pack_definition_hash: Digest
    trigger_snapshot_id: Identifier
    activation: ProbabilityActivation

class FrozenProbabilityBinding(_Closed):
    schema_version: Literal["research-probability-binding.v2"] = SCHEMA
    run_id: Identifier
    event_id: Identifier
    task_request_hash: Digest
    compiled_policy_hash: Digest
    pack_definition_hash: Digest
    context_hash: Digest
    trigger_snapshot_id: Identifier
    trigger_snapshot_hash: Digest
    pit_cutoff_at: AwareDatetime
    recorded_at: AwareDatetime
    captured_at: AwareDatetime
    selection: KernelProbabilitySelection | None

class _ValueSlot(_Closed):
    horizon: Horizon
    selected_model_probability: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    graph_probability: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    selected_probability_status: Literal["calibrated", "uncalibrated"] | None
    graph_action: Literal["long", "short", "neutral", "no_trade"] | None
    kernel: KernelCalibratedValue | None
    reason: Identifier

class FrozenProbabilityValues(_Closed):
    schema_version: Literal["research-kernel-probability-values.v2"] = VALUE_SCHEMA
    input_definition: Literal["authenticated_graph_subjective_probability.v2"] = INPUT_DEFINITION
    run_id: Identifier
    phase: Phase
    market: Identifier
    instrument: Identifier
    strategy_version: Identifier
    first_binding_id: Identifier
    first_binding_payload_hash: Digest
    task_request_hash: Digest
    compiled_policy_hash: Digest
    pack_definition_hash: Digest
    activation_id: Identifier | None
    activation_payload_hash: Digest | None
    method_hash: Digest
    stable_recipe_hash: Digest
    method_recipe_hash: Digest
    graph_recipe_hash: Digest
    compiled_policy_recipe_hash: Digest
    graph_final_ref: dict[str, Any]
    graph_result_hash: Digest
    selected_claim_hash: Digest | None
    decision_at: AwareDatetime
    assessed_at: AwareDatetime
    slots: Annotated[tuple[_ValueSlot, ...], Field(min_length=3, max_length=3)]

@dataclass(frozen=True)
class ProbabilityMaterials:
    binding: FrozenProbabilityBinding
    binding_row_id: str
    binding_payload_hash: str
    productions: tuple[FrozenCalibrationProduction, ...]
    phase_originals: tuple[FrozenProbabilityValues, ...]

@dataclass(frozen=True)
class ProbabilityOutcome:
    status: Literal["pass", "fail", "not_available"]
    reason: str
    input_hash: str
    references: tuple[tuple[str, str, str], ...]

def _aware(value: datetime | str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("research_probability_external_time_invalid")
    return instant.astimezone(UTC)

def _physical(session: Session) -> None:
    connection = session.connection()
    native = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("research_probability_physical_snapshot_required")

def _row_hash(row: Any) -> str:
    # Only trusted internal SQLite DateTime values use its established codec.
    body = {}
    for column in row.__table__.columns:
        value = getattr(row, column.key)
        body[column.key] = as_utc(value).isoformat() if isinstance(value, datetime) else value
    return recipe_hash(body)

def _context(session: Session, bundle: Any) -> dict[str, Any]:
    from .research_execution_binding import read_bundle_identity_in_session
    actual, identity = read_bundle_identity_in_session(session, bundle.request.run_id)
    if actual != bundle:
        raise ValueError("research_probability_bundle_original_changed")
    trigger = session.get(SnapshotRecord, bundle.request.trigger_snapshot_id, populate_existing=True)
    if trigger is None or trigger.event_id != bundle.request.event_id:
        raise ValueError("research_probability_trigger_original_invalid")
    return dict(run_id=bundle.request.run_id, event_id=bundle.request.event_id,
        task_request_hash=bundle.task.request_hash, bundle_identity=identity.model_dump(mode="json"),
        compiled_policy_hash=bundle.task.compiled_policy_hash, pack_definition_hash=bundle.task.pack_definition_hash,
        factory_source=bundle.task.factory_source.model_dump(mode="json"),
        scope=bundle.task.scope.model_dump(mode="json"), trigger_snapshot_id=trigger.snapshot_id,
        trigger_snapshot_hash=trigger.snapshot_hash, trigger_row_hash=_row_hash(trigger),
        pit_cutoff_at=bundle.request.pit_cutoff_at.isoformat(), recorded_at=bundle.recorded_at.isoformat())

def _role_row(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    rows = session.scalars(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == role,
    ).limit(2).execution_options(populate_existing=True)).all()
    if len(rows) > 1:
        raise ValueError("research_probability_role_duplicate")
    return rows[0] if rows else None

def selection_from_activation(activation: ProbabilityActivation, *, run_id: str, event_id: str,
        task_request_hash: str, compiled_policy_hash: str, pack_definition_hash: str,
        trigger_snapshot_id: str) -> KernelProbabilitySelection:
    _require_loaded()
    return KernelProbabilitySelection(run_id=run_id, event_id=event_id,
        task_request_hash=task_request_hash, compiled_policy_hash=compiled_policy_hash,
        pack_definition_hash=pack_definition_hash, trigger_snapshot_id=trigger_snapshot_id,
        activation=ProbabilityActivation.model_validate_json(activation.model_dump_json()))

def _selection_matches(selection: KernelProbabilitySelection, context: dict[str, Any]) -> None:
    if any(getattr(selection, key) != context[key] for key in (
            "run_id", "event_id", "task_request_hash", "compiled_policy_hash",
            "pack_definition_hash", "trigger_snapshot_id")):
        raise ValueError("research_probability_selection_case_invalid")

def _selected_productions(session: Session, bundle: Any,
        selection: KernelProbabilitySelection | None) -> tuple[FrozenCalibrationProduction, ...]:
    from .calibration import load_probability_activation_in_session, load_production_in_session
    from .calibration_inputs import stable_compiled_policy_recipe_hash
    if selection is None:
        return ()
    activation = load_probability_activation_in_session(session, selection.activation.activation_id)
    if activation != selection.activation:
        raise ValueError("research_probability_activation_original_changed")
    scope = bundle.task.scope
    if (activation.scope_market != scope.market or activation.scope_instrument != scope.instrument
            or activation.pack_definition_hash != bundle.task.pack_definition_hash
            or activation.compiled_policy_recipe_hash != stable_compiled_policy_recipe_hash(bundle.compiled.compiled)
            or not activation.server_known_at <= activation.valid_from <= bundle.request.pit_cutoff_at < activation.valid_until):
        raise ValueError("research_probability_activation_scope_invalid")
    originals = []
    for ref in activation.references:
        production = load_production_in_session(session, ref.production_id, expected_payload_hash=ref.payload_hash)
        if (production.state != "passed" or production.report is None
                or recipe_hash(production.model_dump(mode="json")) != ref.payload_hash
                or recipe_hash(production.scope.model_dump(mode="json")) != ref.scope_hash
                or production.inputs_hash != ref.inputs_hash or production.report_hash != ref.report_hash
                or production.producer_hash != ref.producer_hash or production.scope.horizon != ref.horizon
                or production.scope.strategy_version != activation.strategy_version
                or production.scope.stable_recipe_hash != activation.stable_recipe_hash
                or production.scope.market != activation.scope_market
                or production.scope.instrument != activation.scope_instrument
                or production.scope.pack_definition_hash != activation.pack_definition_hash
                or production.scope.compiled_policy_recipe_hash != activation.compiled_policy_recipe_hash
                or production.server_known_at > activation.server_known_at):
            raise ValueError("research_probability_production_reference_invalid")
        originals.append(production)
    return tuple(originals)

def _freeze_probability_binding_in_session(session: Session, bundle: Any,
        selection: KernelProbabilitySelection | None = None, *, _first: object | None = None) -> FrozenProbabilityBinding:
    _require_loaded()
    _physical(session)
    if _first is None or session.info.get(_FIRST_FREEZE_KEY) is not _first:
        raise ValueError("research_probability_first_freeze_required")
    context = _context(session, bundle)
    if _role_row(session, bundle.request.run_id, ROLE) is not None:
        raise ValueError("research_probability_first_binding_already_present")
    if selection is not None:
        selection = KernelProbabilitySelection.model_validate_json(selection.model_dump_json())
        _selection_matches(selection, context)
    _selected_productions(session, bundle, selection)
    captured = _aware(utcnow())
    if captured < _aware(bundle.recorded_at) or selection is not None and selection.activation.server_known_at > captured:
        raise ValueError("research_probability_freeze_clock_invalid")
    binding = FrozenProbabilityBinding(run_id=context["run_id"], event_id=context["event_id"],
        task_request_hash=context["task_request_hash"], context_hash=recipe_hash(context),
        compiled_policy_hash=context["compiled_policy_hash"], pack_definition_hash=context["pack_definition_hash"],
        trigger_snapshot_id=context["trigger_snapshot_id"], trigger_snapshot_hash=context["trigger_snapshot_hash"],
        pit_cutoff_at=bundle.request.pit_cutoff_at, recorded_at=bundle.recorded_at, captured_at=captured,
        selection=selection)
    encoded = canonical_recipe(binding.model_dump(mode="json"))
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("research_probability_binding_limit")
    session.add(RunRoleOutputRecord(output_id="probability_" + recipe_hash({"run_id": binding.run_id}),
        run_id=binding.run_id, role=ROLE, schema_version=SCHEMA, payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(), created_at=captured))
    session.flush()
    _require_loaded()
    return binding

@contextmanager
def probability_first_freeze_in_session(session: Session, run_id: str) -> Generator[Any]:
    _require_loaded()
    _physical(session)
    connection = session.connection()
    native = connection.connection.driver_connection
    transaction, nested = session.get_transaction(), session.get_nested_transaction()
    existing = session.scalar(select(RunRoleOutputRecord.output_id).where(
        RunRoleOutputRecord.run_id == run_id,
        RunRoleOutputRecord.role.in_(("research_request_pack", "research_task_request", "research_compiled_policy", ROLE)),
    ).limit(1))
    if existing is not None or _FIRST_FREEZE_KEY in session.info:
        raise ValueError("research_probability_first_freeze_required")
    token = object()
    session.info[_FIRST_FREEZE_KEY] = token
    finished = False
    def finish(bundle: Any, selection: KernelProbabilitySelection | None) -> None:
        nonlocal finished
        if (finished or bundle.request.run_id != run_id or session.connection() is not connection
                or connection.connection.driver_connection is not native or not native.in_transaction
                or session.get_transaction() is not transaction or session.get_nested_transaction() is not nested):
            raise ValueError("research_probability_first_freeze_changed")
        _freeze_probability_binding_in_session(session, bundle, selection, _first=token)
        finished = True
    try:
        yield finish
        if (not finished or session.connection() is not connection or not native.in_transaction
                or session.get_transaction() is not transaction or session.get_nested_transaction() is not nested):
            raise ValueError("research_probability_first_freeze_incomplete")
        _require_loaded()
    finally:
        if session.info.get(_FIRST_FREEZE_KEY) is not token:
            raise ValueError("research_probability_first_freeze_changed")
        del session.info[_FIRST_FREEZE_KEY]

def _read_phase(session: Session, run_id: str, phase: Phase) -> FrozenProbabilityValues | None:
    row = _role_row(session, run_id, "research_probability_value_" + phase)
    if row is None:
        return None
    if len(row.payload_json.encode("utf-8")) > MAX_BYTES:
        raise ValueError("research_probability_value_limit")
    digest = hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
    value = FrozenProbabilityValues.model_validate_json(row.payload_json)
    if (row.schema_version != VALUE_SCHEMA or row.payload_hash != digest
            or row.output_id != "probability_value_" + digest
            or canonical_recipe(value.model_dump(mode="json")) != row.payload_json
            or len(row.payload_json.encode("utf-8")) > MAX_BYTES
            or value.run_id != run_id or value.phase != phase or as_utc(row.created_at) != value.assessed_at):
        raise ValueError("research_probability_value_original_invalid")
    return value

def read_probability_materials_in_session(session: Session, bundle: Any) -> ProbabilityMaterials:
    _require_loaded()
    _physical(session)
    context = _context(session, bundle)
    row = _role_row(session, bundle.request.run_id, ROLE)
    if row is None:
        raise ValueError("research_probability_first_binding_missing")
    if (row.output_id != "probability_" + recipe_hash({"run_id": bundle.request.run_id})
            or row.schema_version != SCHEMA or len(row.payload_json.encode("utf-8")) > MAX_BYTES
            or hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest() != row.payload_hash):
        raise ValueError("research_probability_binding_original_invalid")
    binding = FrozenProbabilityBinding.model_validate_json(row.payload_json)
    if (canonical_recipe(binding.model_dump(mode="json")) != row.payload_json
            or binding.context_hash != recipe_hash(context)
            or any(getattr(binding, key) != context[key] for key in (
                "run_id", "event_id", "task_request_hash", "compiled_policy_hash", "pack_definition_hash",
                "trigger_snapshot_id", "trigger_snapshot_hash"))
            or binding.pit_cutoff_at != bundle.request.pit_cutoff_at
            or binding.recorded_at != bundle.recorded_at or as_utc(row.created_at) != binding.captured_at
            or binding.captured_at < binding.recorded_at):
        raise ValueError("research_probability_binding_context_invalid")
    if binding.selection is not None:
        _selection_matches(binding.selection, context)
    originals = _selected_productions(session, bundle, binding.selection)
    phases = tuple(value for phase in ("finalization", "commit")
                   if (value := _read_phase(session, binding.run_id, phase)) is not None)
    _require_loaded()
    return ProbabilityMaterials(binding, row.output_id, row.payload_hash, originals, phases)

def _recipe_scope(prepared: Any, bundle: Any, original: Any) -> tuple[str, str, str, str]:
    from packages.evals.analytical_assessment import stable_hub_recipe
    from .research_graph_binding import authenticated_graph_recipe_identity
    from .calibration_inputs import stable_compiled_policy_recipe_hash
    method_recipe = stable_hub_recipe(prepared.method.definition.model_dump(mode="json"), bundle.compiled.compiled.model_dump(mode="json"))
    graph_recipe = authenticated_graph_recipe_identity(original.receipt)
    if graph_recipe is None:
        raise ValueError("research_probability_graph_recipe_unavailable")
    return (recipe_hash({**method_recipe, "graph_recipe": graph_recipe}), recipe_hash(method_recipe),
            recipe_hash(graph_recipe), stable_compiled_policy_recipe_hash(bundle.compiled.compiled))

def probability_phase(materials: ProbabilityMaterials, *, prepared: Any, bundle: Any,
        original: Any, selected: ResearchSessionResult | None, phase: Phase,
        assessed_at: datetime, allow_freeze: bool = False) -> FrozenProbabilityValues:
    from .calibration import transform_probability
    _require_loaded()
    stable, method, graph, policy = _recipe_scope(prepared, bundle, original)
    assessed, decision = _aware(assessed_at), _aware(original.receipt.decision_cutoff_at)
    if phase not in {"finalization", "commit"} or assessed < decision:
        raise ValueError("research_probability_phase_time_invalid")
    activation = materials.binding.selection.activation if materials.binding.selection else None
    if activation is not None and (activation.stable_recipe_hash != stable
            or activation.compiled_policy_recipe_hash != policy
            or not activation.server_known_at <= activation.valid_from <= decision < activation.valid_until):
        raise ValueError("research_probability_prepared_recipe_invalid")
    products = {item.scope.horizon: item for item in materials.productions}
    graph_horizons = {item.horizon: item for item in original.result.horizons}
    raw_horizons = {item.horizon: item for item in selected.horizons} if selected else {}
    slots = []
    for horizon in HORIZONS:
        item, raw, production = graph_horizons.get(horizon), raw_horizons.get(horizon), products.get(horizon)
        value, reason = None, "probability_kernel_production_missing"
        if item is None:
            reason = "probability_graph_horizon_unavailable"
        elif item.action not in {"long", "short"}:
            reason = "no_direction_claim_not_calibration_pass"
        elif production is not None:
            scope = production.scope
            if (scope.stable_recipe_hash != stable or scope.method_recipe_hash != method
                    or scope.graph_recipe_hash != graph or scope.compiled_policy_recipe_hash != policy
                    or scope.input_definition != INPUT_DEFINITION
                    or scope.strategy_version != activation.strategy_version
                    or production.server_known_at > min(materials.binding.pit_cutoff_at, decision)):
                raise ValueError("research_probability_transform_scope_invalid")
            value = transform_probability(item.subjective_probability, production)
            if (value.production_id != production.production_id
                    or value.production_payload_hash != recipe_hash(production.model_dump(mode="json"))
                    or value.scope_hash != recipe_hash(scope.model_dump(mode="json"))
                    or value.report_hash != production.report_hash
                    or value.raw_probability != item.subjective_probability):
                raise ValueError("research_probability_transform_value_invalid")
            reason = "probability_kernel_math_value_bound"
        slots.append(_ValueSlot(horizon=horizon,
            selected_model_probability=raw.subjective_probability if raw else None,
            selected_probability_status=raw.probability_status if raw else None,
            graph_probability=item.subjective_probability if item else None,
            graph_action=item.action if item else None, kernel=value, reason=reason))
    from .calibration import ai_probability_strategy_version
    strategy = ai_probability_strategy_version(stable)
    if activation is not None and activation.strategy_version != strategy:
        raise ValueError("research_probability_ai_strategy_invalid")
    value = FrozenProbabilityValues(run_id=bundle.request.run_id, phase=phase,
        market=bundle.task.scope.market, instrument=bundle.task.scope.instrument, strategy_version=strategy,
        first_binding_id=materials.binding_row_id, first_binding_payload_hash=materials.binding_payload_hash,
        task_request_hash=bundle.task.request_hash, compiled_policy_hash=bundle.task.compiled_policy_hash,
        pack_definition_hash=bundle.task.pack_definition_hash,
        activation_id=activation.activation_id if activation else None,
        activation_payload_hash=recipe_hash(activation.model_dump(mode="json")) if activation else None,
        method_hash=prepared.method.method_hash, stable_recipe_hash=stable, method_recipe_hash=method,
        graph_recipe_hash=graph, compiled_policy_recipe_hash=policy,
        graph_final_ref=original.reference.model_dump(mode="json"),
        graph_result_hash=recipe_hash(original.result.model_dump(mode="json")),
        selected_claim_hash=recipe_hash(selected.model_dump(mode="json")) if selected else None,
        decision_at=decision, assessed_at=assessed, slots=tuple(slots))
    saved = next((row for row in materials.phase_originals if row.phase == phase), None)
    if saved is not None and saved != value:
        raise ValueError("research_probability_phase_original_changed")
    if saved is None and not allow_freeze:
        raise ValueError("research_probability_phase_original_missing")
    _require_loaded()
    return value

def store_probability_values_in_session(session: Session, value: FrozenProbabilityValues) -> None:
    """Only after the no-write materials boundary, still the original writer."""
    _require_loaded()
    _physical(session)
    old = _read_phase(session, value.run_id, value.phase)
    if old is not None:
        if old != value:
            raise ValueError("research_probability_phase_original_changed")
        return
    saved_check = session.scalar(select(RunRoleOutputRecord.output_id).where(
        RunRoleOutputRecord.run_id == value.run_id,
        RunRoleOutputRecord.role.like("policy_check:" + value.phase + ":%"),
    ).limit(1))
    if saved_check is not None:
        raise ValueError("research_probability_missing_for_saved_phase")
    encoded = canonical_recipe(value.model_dump(mode="json"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("research_probability_value_limit")
    session.add(RunRoleOutputRecord(output_id="probability_value_" + digest,
        run_id=value.run_id, role="research_probability_value_" + value.phase,
        schema_version=VALUE_SCHEMA, payload_json=encoded, payload_hash=digest, created_at=value.assessed_at))
    session.flush()

def probability_phase_reference(session: Session, *, run_id: str, phase: Phase) -> dict[str, str]:
    _require_loaded()
    value = _read_phase(session, run_id, phase)
    if value is None:
        raise ValueError("research_probability_phase_original_missing")
    digest = recipe_hash(value.model_dump(mode="json"))
    return {"receipt_id": "probability_value_" + digest, "payload_hash": digest}

def probability_outcome(materials: ProbabilityMaterials, *, selected: ResearchSessionResult | None,
        result: ResearchSessionResult, decision_at: datetime, rule: Any,
        values: FrozenProbabilityValues) -> ProbabilityOutcome:
    _require_loaded()
    if (rule.params.receipt_basis != "kernel_strategy_calibration"
            or rule.params.no_direction_outcome != "no_direction_claim_not_calibration_pass"
            or values.decision_at != _aware(decision_at)):
        raise ValueError("research_probability_rule_params_invalid")
    digest = recipe_hash(values.model_dump(mode="json"))
    refs = [("policy", materials.binding_row_id, materials.binding_payload_hash),
            ("calibration", "probability_value_" + digest, digest)]
    refs.extend(("calibration", product.production_id, recipe_hash(product.model_dump(mode="json")))
                for product in materials.productions)
    body = {"values": values.model_dump(mode="json"),
            "selected": selected.model_dump(mode="json") if selected else None,
            "saved_result": result.model_dump(mode="json")}
    def outcome(status: Literal["pass", "fail", "not_available"], reason: str) -> ProbabilityOutcome:
        return ProbabilityOutcome(status, reason, recipe_hash(body), tuple(refs))
    if selected is None or not selected.horizons or not result.horizons:
        return outcome("not_available", "probability_selected_claim_unavailable")
    if (len(selected.horizons) != 3 or len(result.horizons) != 3
            or {item.horizon for item in selected.horizons} != set(HORIZONS)
            or {item.horizon for item in result.horizons} != set(HORIZONS)):
        return outcome("fail", "probability_horizon_binding_invalid")
    if any(item.probability_status == "calibrated" for item in selected.horizons if item.action in {"long", "short"}):
        return outcome("fail", "probability_model_calibration_claim_not_kernel_output")
    if any(item.probability_status == "calibrated" for item in result.horizons):
        return outcome("fail", "probability_saved_calibration_claim_not_kernel_output")
    directional = [slot for slot in values.slots if slot.graph_action in {"long", "short"}]
    if not directional:
        return outcome("not_available", "no_direction_claim_not_calibration_pass")
    if any(slot.kernel is None for slot in directional):
        return outcome("not_available", "probability_kernel_value_provenance_unavailable")
    return outcome("pass", "probability_kernel_math_value_bound")

def resolve_probability_commit_in_session(session: Session, *, run_id: str,
        decision_at: datetime, forecast_strategy_version: str | None,
        calibration_receipt_ids: dict[str, str] | None) -> tuple[str | None, dict[str, str] | None]:
    from .research_policy_checker import _binding_originals
    _require_loaded()
    _prepared, bundle, _identity, binding = _binding_originals(session, run_id)
    if binding is None:
        return forecast_strategy_version, calibration_receipt_ids
    materials = read_probability_materials_in_session(session, bundle)
    activation = materials.binding.selection.activation if materials.binding.selection else None
    strategy = activation.strategy_version if activation else None
    if (forecast_strategy_version is not None and forecast_strategy_version != strategy
            or calibration_receipt_ids not in (None, {})):
        raise ValueError("research_probability_late_selection_forbidden")
    _aware(decision_at)
    return strategy, {}

def publication_values_in_session(session: Session, *, run_id: str,
        references: object) -> FrozenProbabilityValues:
    """Integrity only; the policy reader separately authenticates actual math."""
    _require_loaded()
    if not isinstance(references, dict) or set(references) != {"finalization", "commit"}:
        raise ValueError("research_probability_publication_phases_invalid")
    phases = []
    for phase in ("finalization", "commit"):
        if references[phase] != probability_phase_reference(session, run_id=run_id, phase=phase):
            raise ValueError("research_probability_publication_reference_invalid")
        phases.append(_read_phase(session, run_id, phase))
    first, commit = phases
    if (first is None or commit is None or first.model_dump(mode="json", exclude={"phase", "assessed_at"})
            != commit.model_dump(mode="json", exclude={"phase", "assessed_at"})
            or first.assessed_at > commit.assessed_at):
        raise ValueError("research_probability_publication_value_changed")
    return commit

def prepare_probability_publication_in_session(session: Session, *, run_id: str,
        original: Any) -> FrozenProbabilityValues | None:
    """Recompute finalization math alongside the already authenticated Graph.

    This internal preparation never writes. The final writer independently
    rereads the full Graph/Task and both policy/math phases before publication.
    """
    from .research_policy_checker import _binding_originals
    from .research_graph_binding import _read_round_receipt
    _require_loaded()
    prepared, bundle, _identity, binding = _binding_originals(session, run_id)
    if binding is None:
        return None
    if original.receipt.run_id != run_id:
        raise ValueError("research_probability_publication_graph_invalid")
    materials = read_probability_materials_in_session(session, bundle)
    round_receipt = _read_round_receipt(session, original.receipt.last_admission_ref.row, run_id)
    selected = None
    if round_receipt.selected_synthesis_observation_ref is not None:
        raw = round_receipt.projected_state.get("latest_result")
        if raw is None:
            raise ValueError("research_probability_selected_original_missing")
        selected = ResearchSessionResult.model_validate(raw)
    return probability_phase(materials, prepared=prepared, bundle=bundle, original=original,
        selected=selected, phase="finalization", assessed_at=original.receipt.recorded_at)

def kernel_directional_values_attested(values: FrozenProbabilityValues | None) -> bool:
    if values is None:
        return False
    directional = [slot for slot in values.slots if slot.graph_action in {"long", "short"}]
    return bool(directional and all(slot.kernel is not None and slot.selected_probability_status == "uncalibrated"
                                   and slot.kernel.calibrated_probability >= slot.kernel.retain_threshold
                                   for slot in directional))

def _require_loaded() -> None:
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _LOADED_SHA:
        raise ValueError("research_probability_implementation_unavailable")
    if any(globals()[name] is not function or function.__code__ is not code
           for name, function, code in _LOADED_FUNCTIONS):
        raise ValueError("research_probability_implementation_unavailable")
    if (probability_first_freeze_in_session.__wrapped__ is not _LOADED_FREEZE_WRAPPED
            or _LOADED_FREEZE_WRAPPED.__code__ is not _LOADED_FREEZE_WRAPPED_CODE):
        raise ValueError("research_probability_implementation_unavailable")

_LOADED_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_LOADED_FUNCTIONS = tuple((name, globals()[name], globals()[name].__code__) for name in (
    "_aware", "_physical", "_row_hash", "_context", "_role_row", "selection_from_activation",
    "_selection_matches", "_selected_productions", "_freeze_probability_binding_in_session",
    "probability_first_freeze_in_session", "_read_phase", "read_probability_materials_in_session",
    "_recipe_scope", "probability_phase", "store_probability_values_in_session", "probability_phase_reference",
    "probability_outcome", "resolve_probability_commit_in_session", "publication_values_in_session", "_require_loaded",
    "prepare_probability_publication_in_session", "kernel_directional_values_attested",
    "as_utc", "utcnow", "canonical_recipe", "recipe_hash",
))
_LOADED_FREEZE_WRAPPED = probability_first_freeze_in_session.__wrapped__
_LOADED_FREEZE_WRAPPED_CODE = _LOADED_FREEZE_WRAPPED.__code__
