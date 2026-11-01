"""Executable DESIGN reference. No network, live DB, model or production imports.

This checks selected semantics, not a predictor or a production implementation.
Production must add authenticated inputs, leases, scope admission and full DTOs.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads(Path(__file__).with_name("action.schema.json").read_text("utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class ContractError(ValueError):
    pass


def clock(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ContractError("TIMEZONE_REQUIRED")
    return result.astimezone(timezone.utc)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def validate_plan(plan: dict) -> None:
    VALIDATOR.validate(plan)
    probability = plan["probability"]["value"]
    if probability is not None and not math.isfinite(probability):
        raise ContractError("NONFINITE_NUMBER")
    if clock(plan["valid_until"]) <= clock(plan["as_of"]):
        raise ContractError("INVALID_VALIDITY_WINDOW")
    if plan["action"] in {"reduce_long", "exit_long"} and plan["position_context"] != "long":
        raise ContractError("POSITION_CONTEXT_REQUIRED")
    if plan["action"] == "enter_long" and plan["position_context"] != "flat":
        raise ContractError("FLAT_CONTEXT_REQUIRED")
    depth, leaves = expression_size(plan["condition"])
    if depth > 8 or leaves > 32:
        raise ContractError("CONDITION_COMPLEXITY_EXCEEDED")


def expression_size(expr: dict) -> tuple[int, int]:
    if expr["op"] == "compare":
        return 1, 1
    sizes = [expression_size(child) for child in expr["args"]]
    return 1 + max(depth for depth, _ in sizes), sum(count for _, count in sizes)


def evaluate(expr: dict, facts: dict, as_of: str) -> str:
    """Strong Kleene truth values. Structural errors remain errors, not FALSE."""
    if expr["op"] in {"all", "any"}:
        # Evaluate all leaves to surface malformed scope/unit bindings even if
        # another child would determine the logical truth by short circuit.
        values = [evaluate(child, facts, as_of) for child in expr["args"]]
        if expr["op"] == "all":
            return "false" if "false" in values else "unknown" if "unknown" in values else "true"
        return "true" if "true" in values else "unknown" if "unknown" in values else "false"
    fact = facts.get(expr["feature"])
    if fact is None:
        return "unknown"
    if (fact["scope_id"], fact["unit"]) != (expr["scope_id"], expr["unit"]):
        raise ContractError("FEATURE_BINDING_MISMATCH")
    cutoff = clock(as_of)
    if clock(fact["known_at"]) > cutoff or clock(fact["observed_at"]) > cutoff:
        return "unknown"
    if fact.get("quality") != "accepted" or fact.get("value") is None:
        return "unknown"
    if (cutoff - clock(fact["observed_at"])).total_seconds() > expr["max_age_seconds"]:
        return "unknown"
    left, right = Decimal(str(fact["value"])), Decimal(expr["threshold"])
    if not left.is_finite() or not right.is_finite():
        raise ContractError("NONFINITE_NUMBER")
    outcomes = {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}
    return "true" if outcomes[expr["cmp"]] else "false"


TRANSITIONS = {
    ("proposed", "publish"): "armed", ("proposed", "invalidate"): "invalidated",
    ("armed", "trigger"): "triggered", ("armed", "expire"): "expired",
    ("armed", "invalidate"): "invalidated", ("armed", "supersede"): "superseded",
    ("triggered", "settle"): "completed", ("triggered", "expire"): "completed",
    ("triggered", "invalidate"): "invalidated", ("triggered", "supersede"): "superseded",
}


def transition(state: str, event: str) -> str:
    try:
        return TRANSITIONS[state, event]
    except KeyError as error:
        raise ContractError("ILLEGAL_PLAN_TRANSITION") from error


def next_streak(previous: int, last_bar: str | None, bar: str, truth: str,
                required: int = 2, interval_seconds: int = 60) -> tuple[int, bool]:
    """Only consecutive distinct CLOSED bars can satisfy persistence."""
    if last_bar is not None and clock(bar) <= clock(last_bar):
        return previous, False
    consecutive = last_bar is not None and (clock(bar)-clock(last_bar)).total_seconds() == interval_seconds
    current = (previous if consecutive else 0) + 1 if truth == "true" else 0
    return current, current >= required


def condition_event(plan: dict, state: str, truth: str, tick_at: str) -> str | None:
    """Caller must provide already-confirmed truth; expiry wins over trigger."""
    if state not in {"armed", "triggered"}:
        return None
    if clock(tick_at) >= clock(plan["valid_until"]):
        return "expire"
    if state == "armed" and truth == "true" and plan["advice_qualification"] != "blocked":
        return "trigger"
    return None


def first_executable_quote(quotes: list[dict], *, side: str, scope_id: str,
                           visible_at: str, trigger_at: str, admitted_at: str,
                           max_age_seconds: int = 10) -> dict | None:
    if side not in {"buy", "sell"}:
        raise ContractError("INVALID_SIDE")
    eligible = max(map(clock, (visible_at, trigger_at, admitted_at)))
    field = "ask" if side == "buy" else "bid"
    ordered = sorted(quotes, key=lambda item: max(clock(item["known_at"]), clock(item["observed_at"])))
    for quote in ordered:
        if quote["scope_id"] != scope_id or quote.get("quality") != "accepted":
            continue
        observed, known = clock(quote["observed_at"]), clock(quote["known_at"])
        # Do not buy at a pre-publication historical low even if received late.
        if observed < eligible or known < observed or (known-observed).total_seconds() > max_age_seconds:
            continue
        if min(Decimal(quote["bid"]), Decimal(quote["ask"])) <= 0:
            continue
        if Decimal(quote["bid"]) > Decimal(quote["ask"]):
            continue
        return {"quote_ref": quote["ref"], "price": quote[field], "eligible_at": eligible.isoformat()}
    return None


def net_long_return(entry_ask: str, exit_bid: str, entry_fee: str, exit_fee: str) -> Decimal:
    """Unlevered unit position; fees are fractions of each leg's notional.

    Slippage must already be included in the selected executable prices.
    This deliberately does not score funding, shorts, or user transactions.
    """
    buy, sell, fee_in, fee_out = map(Decimal, (entry_ask, exit_bid, entry_fee, exit_fee))
    if buy <= 0 or sell <= 0 or not (0 <= fee_in < 1 and 0 <= fee_out < 1):
        raise ContractError("INVALID_COST_INPUT")
    return (sell * (1-fee_out) - buy*(1+fee_in)) / (buy*(1+fee_in))


def simple_ev(p: str, gain: str, loss: str, cost: str) -> Decimal:
    prob, win, lose, fee = map(Decimal, (p, gain, loss, cost))
    if not (0 <= prob <= 1) or min(win, lose, fee) < 0:
        raise ContractError("INVALID_EV_INPUT")
    return prob*win - (1-prob)*lose - fee


def same_bar_outcome(high: str, low: str, stop: str, target: str) -> str:
    if Decimal(low) <= Decimal(stop) and Decimal(high) >= Decimal(target):
        return "ambiguous"
    if Decimal(low) <= Decimal(stop):
        return "stop"
    if Decimal(high) >= Decimal(target):
        return "target"
    return "neither"


def make_store() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(Path(__file__).with_name("reference.sql").read_text("utf-8"))
    return connection


def publish(connection: sqlite3.Connection, plan: dict, request_key: str,
            expected_revision: int) -> dict:
    """Single-owner design simulator: immutable revisions + CAS + atomic outbox.

    Production scopes request keys by authenticated owner and adds lease fencing.
    """
    validate_plan(plan)
    if plan["revision"] != expected_revision + 1:
        raise ContractError("REVISION_SEQUENCE_INVALID")
    payload_hash = digest({"plan": plan, "expected_revision": expected_revision})
    connection.execute("BEGIN IMMEDIATE")
    try:
        receipt = connection.execute("SELECT payload_hash, receipt_json FROM commands WHERE request_key=?", (request_key,)).fetchone()
        if receipt:
            if receipt[0] != payload_hash:
                raise ContractError("IDEMPOTENCY_KEY_REUSED")
            connection.commit()
            return json.loads(receipt[1])
        head = connection.execute("SELECT revision FROM heads WHERE plan_id=?", (plan["plan_id"],)).fetchone()
        if (head[0] if head else 0) != expected_revision:
            raise ContractError("REVISION_CONFLICT")
        if head:
            connection.execute("UPDATE heads SET revision=? WHERE plan_id=? AND revision=?", (plan["revision"], plan["plan_id"], expected_revision))
        else:
            connection.execute("INSERT INTO heads VALUES (?,?)", (plan["plan_id"], plan["revision"]))
        connection.execute("INSERT INTO revisions VALUES (?,?,?,?)", (plan["plan_id"], plan["revision"], digest(plan), json.dumps(plan)))
        event_id = f"{plan['plan_id']}:{plan['revision']}:published"
        connection.execute("INSERT INTO outbox VALUES (?,?,?)", (event_id, plan["plan_id"], plan["revision"]))
        result = {"plan_id": plan["plan_id"], "revision": plan["revision"], "event_id": event_id, "status": "persisted", "user_seen": False}
        connection.execute("INSERT INTO commands VALUES (?,?,?)", (request_key, payload_hash, json.dumps(result)))
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
