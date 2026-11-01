"""Run independent golden contract fixtures. All fixtures are synthetic.

Usage: .venv/Scripts/python.exe -X utf8 -B <this file> --output <new JSON path>
No external service is contacted. Does not migrate the application database.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jsonschema.exceptions import ValidationError

import reference as ref
import workflow_reference

BASE = Path(__file__).parent
EXAMPLES = json.loads((BASE / "examples.json").read_text("utf-8"))


def patched(value, changes):
    result = copy.deepcopy(value)
    for path, replacement in changes.items():
        keys = path.split(".")
        target = result
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = replacement
    return result


def expect_error(fn, code):
    try:
        fn()
    except ref.ContractError as error:
        assert str(error) == code, (str(error), code)
    else:
        raise AssertionError(f"expected {code}")


def persistence_case(plan):
    db = ref.make_store()
    try:
        first = ref.publish(db, plan, "fixture-command-1", 0)
        assert first["user_seen"] is False  # durable publication is not a view receipt
        assert ref.publish(db, plan, "fixture-command-1", 0) == first
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
        changed = patched(plan, {"action": "wait"})
        expect_error(lambda: ref.publish(db, changed, "fixture-command-1", 0), "IDEMPOTENCY_KEY_REUSED")
        expect_error(lambda: ref.publish(db, changed, "fixture-command-conflict", 0), "REVISION_CONFLICT")
        second = patched(plan, {"revision": 2, "action": "wait"})
        ref.publish(db, second, "fixture-command-2", 1)
        assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 2
        assert json.loads(db.execute("SELECT payload_json FROM revisions WHERE revision=1").fetchone()[0])["action"] == "enter_long"
        try:
            db.execute("UPDATE revisions SET payload_json='{}' WHERE revision=1")
        except sqlite3.IntegrityError:
            db.rollback()
        else:
            raise AssertionError("old revision was mutable")
        db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox WHEN NEW.revision=3 BEGIN SELECT RAISE(ABORT,'fixture_fault'); END")
        db.commit()
        try:
            ref.publish(db, patched(plan, {"revision": 3}), "fixture-command-3", 2)
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("fault injection did not fail")
        assert db.execute("SELECT revision FROM heads").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == 2
        return "idempotent_atomic_immutable"
    finally:
        db.close()


def execute(case):
    plan, facts = copy.deepcopy(EXAMPLES["plan"]), copy.deepcopy(EXAMPLES["facts"])
    kind = case["kind"]
    if kind == "plan":
        ref.validate_plan(patched(plan, case["patch"]))
        return "valid"
    if kind == "evaluate":
        facts["fixture.close"].update(case["patch"])
        return ref.evaluate(plan["condition"], facts, plan["as_of"])
    if kind == "logic":
        leaves = []
        for index, truth in enumerate(case["values"]):
            key = f"fixture.logic.{index}"
            leaves.append({**plan["condition"], "feature": key})
            if truth != "unknown":
                facts[key] = {**facts["fixture.close"], "value": "101" if truth == "true" else "99"}
        return ref.evaluate({"op": case["op"], "args": leaves}, facts, plan["as_of"])
    if kind == "streak":
        return list(ref.next_streak(case["previous"], case["last"], case["bar"], case["truth"]))
    if kind == "transition":
        return ref.transition(case["state"], case["event"])
    if kind == "expiry":
        return ref.condition_event(plan, "armed", "true", plan["valid_until"])
    if kind == "quote":
        result = ref.first_executable_quote(EXAMPLES["quotes"], side="buy", scope_id=plan["scope_id"],
                                           visible_at="2026-01-01T08:00:02Z", trigger_at=plan["as_of"], admitted_at=plan["as_of"])
        return {key: result[key] for key in ("quote_ref", "price")} if result else None
    if kind == "ambiguous_bar":
        return ref.same_bar_outcome("103", "98", "99", "102")
    if kind == "ev":
        return str(ref.simple_ev("0.7", "1", "3", "0.05"))
    if kind == "net_return":
        return str(ref.net_long_return("100", "100", "0.001", "0.001").quantize(Decimal("0.000001")))
    if kind == "persistence":
        return persistence_case(plan)
    if kind == "complexity":
        for _ in range(8):
            plan["condition"] = {"op": "all", "args": [plan["condition"]]}
        ref.validate_plan(plan)
        return "valid"
    if kind == "blocked":
        plan["advice_qualification"] = "blocked"
        return ref.condition_event(plan, "armed", "true", plan["as_of"])
    if kind == "nonfinite_probability":
        plan["probability"]["value"] = float("nan")
        ref.validate_plan(plan)
        return "valid"
    raise ValueError(f"unknown fixture kind: {kind}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output_exists_preserve_first_result")
    ref.Draft202012Validator.check_schema(ref.SCHEMA)
    rows = []
    for case in EXAMPLES["cases"]:
        try:
            actual = execute(case)
        except ValidationError:
            actual = "schema_error"
        except ref.ContractError as error:
            actual = str(error)
        except Exception as error:
            actual = f"unexpected:{type(error).__name__}:{error}"
        rows.append({"id": case["id"], "expected": case["expected"], "actual": actual,
                     "passed": actual == case["expected"]})
    rows.extend(workflow_reference.run_checks())
    result = {"schema_version": "executable-design-check.v1", "checked_at": datetime.now(timezone.utc).isoformat(),
              "mode": "synthetic_contract_only", "production_acceptance": False, "financial_effect_proven": False,
              "passed": sum(row["passed"] for row in rows), "total": len(rows), "results": rows,
              "inputs": {p.name: ref.hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE/"reference.py", BASE/"action.schema.json", BASE/"examples.json", BASE/"reference.sql", BASE/"workflow.schema.json", BASE/"workflow.example.json", BASE/"workflow_reference.py", Path(__file__)]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({key: result[key] for key in ["mode", "passed", "total", "production_acceptance", "financial_effect_proven"]}))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
