"""Execution wire relations only; original readers retain all authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


@lru_cache(maxsize=1)
def _definitions() -> dict:
    source = (
        Path(__file__).resolve().parents[3] / "contracts/schemas/action_execution_v1.schema.yaml"
    )
    return yaml.safe_load(source.read_text(encoding="utf-8"))["$defs"]


def _wire(value):
    if isinstance(value, BaseModel):
        return _wire(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("execution_timezone_required")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _precision(value, schema, definitions):
    if "$ref" in schema:
        return _precision(value, definitions[schema["$ref"].split("/")[-1]], definitions)
    if "anyOf" in schema:
        if value is not None:
            _precision(value, schema["anyOf"][0], definitions)
    elif schema.get("type") == "object" and isinstance(value, dict):
        for key, item in value.items():
            _precision(item, schema["properties"][key], definitions)
    elif schema.get("type") == "array" and isinstance(value, (list, tuple)):
        for item in value:
            _precision(item, schema["items"], definitions)
    elif schema.get("x-decimal-precision") == 50:
        if len(Decimal(value).as_tuple().digits) > 50:
            raise ValueError("execution_decimal_precision_exceeded")


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _pairs(value):
    for name in value:
        if name.endswith("_ref") and name[:-4] + "_hash" in value:
            _require(
                (value[name] is None) == (value[name[:-4] + "_hash"] is None),
                "execution_reference_pair_invalid",
            )


def _number(value):
    return Decimal(value)


class ActionExecutionContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, serialize_by_alias=True
    )

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value):
        definitions = _definitions()
        name = next(
            (key for key, schema in definitions.items() if schema["title"] == cls.__name__), None
        )
        if name is None:
            raise ValueError("execution_definition_required")
        wire = _wire(value)
        validator = Draft202012Validator(
            {"$defs": definitions, "$ref": f"#/$defs/{name}"}, format_checker=FormatChecker()
        )
        error = next(validator.iter_errors(wire), None)
        if error:
            path = ".".join(map(str, error.absolute_path))
            raise ValueError(f"execution_shape_invalid:{error.validator}:{path}")
        _precision(wire, definitions[name], definitions)
        return value

    @field_validator("*", mode="after")
    @classmethod
    def utc(cls, value):
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def relations(self):
        value = self.model_dump(mode="python", by_alias=True)
        _pairs(value)
        kind = type(self).__name__
        if "channel" in value:
            _require(
                (value["channel"] == "pc_visible") == (value["owner_id"] is not None),
                "execution_channel_owner_invalid",
            )
        if "version" in value and "previous_ref" in value:
            _require(
                (value["version"] == 1) == (value["previous_ref"] is None),
                "execution_previous_version_invalid",
            )
        if kind == "ActionExecutionProtocolV1":
            _require(
                self.registered_at <= self.T0 < self.T1 <= self.label_deadline,
                "execution_protocol_time_invalid",
            )
            horizons = [self.primary_horizon, *self.diagnostic_horizons]
            _require(len(horizons) == len(set(horizons)), "execution_protocol_horizon_duplicate")
            _require(
                sorted(horizons) == sorted(item.horizon for item in self.targets),
                "execution_protocol_target_mismatch",
            )
            for items in (
                self.scopes,
                self.channels,
                self.delays,
                [item.ref for item in self.methods],
            ):
                _require(len(items) == len(set(items)), "execution_protocol_duplicate_dimension")
        if kind == "ActionExecutionIntentV1":
            _require(
                self.action
                == {"entry": "enter_long", "reduce": "reduce_long", "exit": "exit_long"}[
                    self.phase
                ],
                "execution_phase_action_invalid",
            )
            conditional = self.reason in {"condition_entry", "condition_exit", "invalidation"}
            quote = self.reason in {"stop", "target"}
            expiry = self.reason == "expiry"
            _require(
                self.source_kind
                == ("condition" if conditional else "quote" if quote else "deadline"),
                "execution_source_kind_invalid",
            )
            _require(
                (self.phase == "entry") == (self.reason == "condition_entry"),
                "execution_entry_reason_invalid",
            )
            for name in ("evaluation_ref", "evaluation_input_ref", "evaluation_available_at"):
                _require(
                    (value[name] is not None) == conditional, "execution_evaluation_source_required"
                )
            _require(
                (self.source_quote_ref is not None) == quote
                and (self.availability_hash is not None) == quote,
                "execution_quote_source_required",
            )
            _require(
                (self.deadline_basis is not None) == expiry, "execution_deadline_source_required"
            )
            _require(
                (self.episode_ref is not None) == (self.phase != "entry"),
                "execution_episode_source_required",
            )
            _require(
                (self.entry_valid_until is not None) == (self.phase == "entry"),
                "execution_entry_deadline_invalid",
            )
            _require(
                (self.exit_trigger_deadline is not None) == (self.phase != "entry"),
                "execution_exit_deadline_invalid",
            )
            pc = self.channel == "pc_visible"
            _require(
                pc
                == (self.display_receipt_ref is not None)
                == (self.display_received_at is not None),
                "execution_display_receipt_required",
            )
            known = [
                self.publication_available_at,
                self.trigger_input_known_at,
                self.admission_valid_at,
                self.observed_committed_at,
            ]
            known += [
                item
                for item in (self.evaluation_available_at, self.display_received_at)
                if item is not None
            ]
            _require(self.base_action_at == max(known), "execution_base_action_time_invalid")
            _require(
                self.eligible_at == self.base_action_at + timedelta(seconds=self.delay_seconds),
                "execution_delay_invalid",
            )
            _require(
                self.quote_deadline == self.eligible_at + timedelta(seconds=60),
                "execution_quote_window_invalid",
            )
            _require(
                self.created_at >= self.observed_committed_at,
                "execution_commit_observation_invalid",
            )
            _require(
                self.U_measurement_end <= self.U_knowledge_cutoff
                and self.U_known_at <= self.U_knowledge_cutoff,
                "execution_u_time_invalid",
            )
            if expiry:
                _require(
                    self.deadline_basis == self.exit_trigger_deadline
                    and self.trigger_input_known_at >= self.deadline_basis,
                    "execution_expiry_basis_invalid",
                )
        if kind == "ActionFillV1":
            _require(
                self.provider_observed_at
                <= self.received_at
                <= self.available_at
                <= self.executed_at,
                "execution_fill_time_invalid",
            )
        if kind == "ActionFillV2":
            _require(
                self.observed_local_lower
                <= self.observed_local_upper
                <= self.received_at
                <= self.available_at
                <= self.executed_at,
                "execution_fill_local_time_invalid",
            )
            _require(
                self.executed_at - self.observed_local_lower <= timedelta(seconds=10),
                "execution_fill_local_quote_stale",
            )
            _require(
                self.clock_ref == "clock_" + self.clock_hash,
                "execution_fill_clock_identity_invalid",
            )
        if kind in {"ActionFillV1", "ActionFillV2"}:
            _require(_number(self.bid) <= _number(self.ask), "execution_quote_crossed")
            _require(
                self.raw_price == (self.ask if self.side == "buy" else self.bid),
                "execution_raw_side_price_invalid",
            )
            if self.side == "buy":
                _require(
                    self.gross is self.cost is self.net is None
                    and _number(self.cash_delta) < 0
                    and _number(self.execution_price) >= _number(self.raw_price),
                    "execution_buy_accounting_invalid",
                )
            else:
                _require(
                    all(value[name] is not None for name in ("gross", "cost", "net"))
                    and _number(self.execution_price) <= _number(self.raw_price),
                    "execution_sell_accounting_invalid",
                )
        if kind == "ActionPaperAccountValuesV1":
            empty = _number(self.quantity) == 0
            _require(
                not empty
                or all(
                    _number(value[name]) == 0
                    for name in ("raw_basis", "entry_fees", "entry_slippage")
                ),
                "execution_empty_position_basis_invalid",
            )
            _require(empty or _number(self.raw_basis) > 0, "execution_position_basis_required")
        if kind == "ActionPaperCheckpointV1":
            _require(
                self.portfolio_id == self.portfolio_identity.id
                and self.identity_hash == self.portfolio_identity.identity_hash,
                "execution_checkpoint_identity_invalid",
            )
            opening = self.event_kind == "opening"
            _require(
                opening == (self.version == 1) == (self.account_before_hash is None),
                "execution_opening_version_invalid",
            )
            _require(
                (self.fill_ref is not None) == (self.event_kind == "fill"),
                "execution_checkpoint_fill_invalid",
            )
            _require((self.intent_ref is None) == opening, "execution_checkpoint_intent_invalid")
            _require(
                bool(self.opening_original_refs) == opening, "execution_opening_original_required"
            )
        if kind == "ActionExecutionEpisodeV1":
            bought = self.opening_kind == "buy_fill"
            _require(
                bought == (self.entry_intent_ref is not None) == (self.entry_fill_ref is not None),
                "execution_episode_opening_invalid",
            )
            initial, remaining = _number(self.initial_quantity), _number(self.remaining_quantity)
            _require(remaining <= initial, "execution_episode_quantity_invalid")
            _require(
                (self.status == "closed") == (remaining == 0),
                "execution_episode_closed_quantity_invalid",
            )
            if self.status == "open":
                _require(initial == remaining, "execution_episode_open_quantity_invalid")
            if self.status == "partially_closed":
                _require(0 < remaining < initial, "execution_episode_partial_quantity_invalid")
            _require(
                _number(self.stop_price)
                < _number(self.reference_price)
                < _number(self.target_price),
                "execution_episode_barriers_invalid",
            )
            _require(
                self.entry_at < self.exit_trigger_deadline and self.entry_at <= self.recorded_at,
                "execution_episode_time_invalid",
            )
            _require(_number(self.price_coverage) <= 1, "execution_coverage_invalid")
        if kind == "ActionOutcomeV2":
            _require(
                (self.episode_ref is None) == (self.episode_version is None),
                "execution_outcome_episode_invalid",
            )
            _require(_number(self.coverage) <= 1, "execution_coverage_invalid")
            if self.quantity is not None and self.remaining_quantity is not None:
                _require(
                    _number(self.remaining_quantity) <= _number(self.quantity),
                    "execution_outcome_quantity_invalid",
                )
            _require(
                (self.execution_status == "pending") == (self.execution_ref is None),
                "execution_outcome_result_required",
            )
            if self.entry_at is not None and self.exit_at is not None:
                _require(self.entry_at <= self.exit_at, "execution_outcome_time_invalid")
        if kind == "ActionExecutionPreviewV1":
            _require(
                (self.portfolio_id is None) != (self.initial_scenario is None),
                "execution_preview_identity_invalid",
            )
            _require(self.as_of < self.expires_at, "execution_preview_window_invalid")
            if self.reduce_fraction is not None:
                _require(_number(self.reduce_fraction) <= 1, "execution_reduce_fraction_invalid")
            if self.status == "available":
                _require(
                    self.quote_ref is not None
                    and self.U_basis_ref is not None
                    and self.price is not None
                    and not self.gaps,
                    "execution_preview_inputs_required",
                )
        if kind == "ActionExecutionReceiptV1":
            filled = self.status == "filled"
            _require(filled == (self.fill_ref is not None), "execution_receipt_fill_invalid")
            if filled:
                _require(
                    self.first_quote_ref is not None
                    and self.checkpoint_ref is not None
                    and self.episode_ref is not None,
                    "execution_receipt_filled_originals_required",
                )
        return self
