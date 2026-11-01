"""Decode explicitly authored monitoring policies, never infer one from prose.

A declared policy is not evidence admission, active monitoring or permission to
trade. The caller authenticates its complete sameRun reviewed assumption first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..ports.market_path import aware_utc, canonical_hash
from .action_policy import ActionPolicyError, validate_condition_expression


@dataclass(frozen=True)
class PerpetualEvidenceCondition:
    causal_link_id: str
    candidate_ids: tuple[str, ...]
    purpose: Literal["confirmation", "invalidation"]
    expression_json: str
    min_fresh_samples: int
    evaluation_seconds: int
    valid_until: datetime


@dataclass(frozen=True)
class ConditionDeclarationResult:
    assumption_id: str
    assumption_ref_json: str
    assumption_json: str
    status: Literal["declared", "unmonitored"]
    policy: PerpetualEvidenceCondition | None
    reason_code: str | None


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("condition_declaration_duplicate_key")
        result[key] = value
    return result


def decode_perpetual_condition(assumption: dict, *, synthesis: dict) -> ConditionDeclarationResult:
    """Return an exact typed declaration or a gap; do not mutate the forecast."""
    assumption_id = assumption.get("assumption_id", "")
    digest = canonical_hash(assumption)
    reference = _canonical({"ref": "decision_model_assumption_" + digest, "hash": digest})

    def gap(reason: str) -> ConditionDeclarationResult:
        return ConditionDeclarationResult(
            assumption_id, reference, _canonical(assumption), "unmonitored", None, reason
        )

    if assumption.get("role") != "entry_condition":
        return gap("condition_declaration_role_unsupported")
    statement = assumption.get("statement")
    if not isinstance(statement, str) or not statement.startswith("{"):
        return gap("condition_declaration_not_json")
    try:
        body = json.loads(statement, object_pairs_hook=_unique_object)
        encoded = _canonical(body)
    except (ValueError, TypeError, RecursionError):
        return gap("condition_declaration_invalid_json")
    if encoded != statement:
        return gap("condition_declaration_not_canonical")
    if not isinstance(body, dict) or set(body) != {
        "schema_version",
        "causal_link_id",
        "candidate_ids",
        "purpose",
        "expression",
        "min_fresh_samples",
        "evaluation_seconds",
        "valid_until",
    }:
        return gap("condition_declaration_fields_invalid")
    if body["schema_version"] != "perpetual-evidence-condition.v1":
        return gap("condition_declaration_version_unsupported")
    if not isinstance(body["purpose"], str) or body["purpose"] not in {
        "confirmation",
        "invalidation",
    }:
        return gap("condition_declaration_purpose_unsupported")
    samples = body["min_fresh_samples"]
    if type(samples) is not int or not 1 <= samples <= 1440:
        return gap("condition_declaration_samples_invalid")
    if type(body["evaluation_seconds"]) is not int or body["evaluation_seconds"] != 60:
        return gap("condition_declaration_cadence_unsupported")
    links = synthesis["analysis"]["main_chain"] + synthesis["analysis"]["opposite_chain"]
    if not isinstance(body["causal_link_id"], str) or body["causal_link_id"] not in {
        row["link_id"] for row in links
    }:
        return gap("condition_causal_link_unavailable")
    candidate_ids = body["candidate_ids"]
    if (
        not isinstance(candidate_ids, list)
        or not 1 <= len(candidate_ids) <= 32
        or any(not isinstance(key, str) for key in candidate_ids)
        or len(set(candidate_ids)) != len(candidate_ids)
        or not set(candidate_ids)
        <= {row["candidate_id"] for row in synthesis["candidate_set"]["candidates"]}
    ):
        return gap("condition_candidate_unavailable")
    context = synthesis["candidate_set"]["context"]
    instrument = context["instrument_id"]
    if instrument not in {"okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"}:
        return gap("condition_instrument_unsupported")
    base = instrument.removeprefix("okx:").split("-")[0]
    expected_scope = f"crypto:perp:{instrument}:trade:1m"
    if context["scope_id"] != expected_scope:
        return gap("condition_scope_binding_invalid")
    try:
        validate_condition_expression(
            body["expression"],
            allowed_features=frozenset({("close", expected_scope, f"USDT/{base}")}),
        )
    except (ActionPolicyError, TypeError) as error:
        return gap(str(error))
    try:
        valid_until = aware_utc(datetime.fromisoformat(body["valid_until"].replace("Z", "+00:00")))
        as_of = aware_utc(datetime.fromisoformat(synthesis["as_of"].replace("Z", "+00:00")))
        end = aware_utc(
            datetime.fromisoformat(synthesis["candidate_set"]["valid_until"].replace("Z", "+00:00"))
        )
    except (ValueError, TypeError, AttributeError):
        return gap("condition_validity_invalid")
    if not as_of < valid_until <= end:
        return gap("condition_validity_outside_target")
    policy = PerpetualEvidenceCondition(
        body["causal_link_id"],
        tuple(candidate_ids),
        body["purpose"],
        _canonical(body["expression"]),
        samples,
        body["evaluation_seconds"],
        valid_until,
    )
    return ConditionDeclarationResult(
        assumption_id, reference, _canonical(assumption), "declared", policy, None
    )


def perpetual_close_reference(close) -> dict:
    body = {
        "close_at": close.close_at.isoformat(),
        "price": close.price,
        "capture_id": close.capture_id,
        "capture_hash": close.capture_hash,
        "capture_metadata_hash": close.capture_metadata_hash,
        "row_index": close.row_index,
        "recorded_at": close.recorded_at.isoformat(),
    }
    digest = canonical_hash(body)
    return {"ref": "perpetual_condition_close_" + digest, "hash": digest}


def evaluate_perpetual_condition(
    declaration: ConditionDeclarationResult,
    closes: tuple,
    *,
    previous: dict | None,
    as_of: datetime,
    not_before: datetime,
) -> dict:
    """Evaluate fresh original closes once; a confirmed invalidation is sticky."""
    from decimal import Decimal

    from .action_policy import (
        ActionFeatureValue,
        confirmed_bar_streak,
        evaluate_condition_expression,
    )

    policy, cutoff = declaration.policy, aware_utc(as_of)
    result = {
        "assumption_id": declaration.assumption_id,
        "assumption_ref": json.loads(declaration.assumption_ref_json),
        "purpose": policy.purpose if policy else None,
        "text": json.loads(declaration.assumption_json)["statement"],
        "truth": "unknown",
        "status": "unmonitored" if policy is None else "unknown",
        "streak": 0,
        "required_samples": policy.min_fresh_samples if policy else None,
        "last_bar_at": None,
        "observed_at": cutoff.isoformat().replace("+00:00", "Z"),
        "reason_codes": [declaration.reason_code] if declaration.reason_code else [],
        "first_confirmed_bar_ref": None,
        "request_id": None,
        "outbox_ids": [],
    }
    if previous:
        for key in ("streak", "last_bar_at", "first_confirmed_bar_ref", "request_id", "outbox_ids"):
            result[key] = previous.get(key, result[key])
        if previous["status"] in {"invalidated", "confirmed"}:
            return {**previous, "observed_at": result["observed_at"]}
    if policy is None:
        return result
    if cutoff >= policy.valid_until:
        result["reason_codes"] = ["perpetual_condition_expired"]
        return result
    expression = json.loads(policy.expression_json)
    leaves = []

    def visit(node):
        if node["op"] == "compare":
            leaves.append(node)
        else:
            for child in node["args"]:
                visit(child)

    visit(expression)
    scope, unit = leaves[0]["scope_id"], leaves[0]["unit"]
    allowed = frozenset({("close", scope, unit)})
    last = (
        datetime.fromisoformat(result["last_bar_at"].replace("Z", "+00:00"))
        if result["last_bar_at"]
        else None
    )
    fresh = False
    for close in sorted(closes, key=lambda row: row.close_at):
        if close.close_at <= aware_utc(not_before) or (last and close.close_at <= last):
            continue
        ref = perpetual_close_reference(close)
        feature = ActionFeatureValue(
            "close",
            scope,
            unit,
            Decimal(close.price),
            close.close_at,
            close.recorded_at,
            "accepted",
            ref["ref"],
            ref["hash"],
        )
        evaluation = evaluate_condition_expression(
            expression, {("close", scope): feature}, as_of=cutoff, allowed_features=allowed
        )
        result["truth"], result["reason_codes"] = evaluation.truth, list(evaluation.reasons)
        # Unknown observations do not count as fresh confirmation samples.
        if evaluation.truth == "unknown":
            continue
        fresh = True
        count, confirmed = confirmed_bar_streak(
            result["streak"],
            last,
            close.close_at,
            evaluation.truth,
            required=policy.min_fresh_samples,
            interval_seconds=60,
        )
        result["streak"], last = count, close.close_at
        result["last_bar_at"] = close.close_at.isoformat().replace("+00:00", "Z")
        result["status"] = "waiting" if evaluation.truth == "true" else "not_confirmed"
        if confirmed:
            result["status"] = "invalidated" if policy.purpose == "invalidation" else "confirmed"
            result["first_confirmed_bar_ref"] = ref
            return result
    if not fresh and previous and closes and result["truth"] == "unknown":
        latest = max(closes, key=lambda row: row.close_at)
        if (
            last
            and latest.close_at == last
            and (cutoff - latest.close_at).total_seconds()
            <= min(row["max_age_seconds"] for row in leaves)
        ):
            result["truth"], result["status"] = previous["truth"], previous["status"]
    if not fresh and result["status"] == "unknown" and not result["reason_codes"]:
        result["reason_codes"] = ["perpetual_condition_closed_input_missing"]
    return result
