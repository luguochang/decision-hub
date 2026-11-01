"""Finite, preregistered engineering behavior cases; never financial labels."""
from datetime import datetime

from jsonschema import Draft202012Validator

from ..ports.market_path import canonical_hash
from ..ports.research_learning import LearningBehaviorSuite, OriginalRef, aware


BEHAVIORS = (
    "trigger_action_consistency.v1",
    "position_protection_distinction.v1",
    "information_availability.v1",
)


def case_reference(case):
    wire = case.model_dump(mode="json") if hasattr(case, "model_dump") else case
    digest = canonical_hash(wire)
    return OriginalRef(ref="behavior_case:" + digest, hash=digest)


def _expected(*, applies, actions, required_refs=(), missing=None):
    properties = {
        "applies": {"const": applies},
        "action": {"enum": list(actions)},
        "missing_inputs": {"type": "array", "items": {"type": "string"}},
        "used_refs": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string", "minLength": 1},
    }
    if missing is not None:
        properties["missing_inputs"]["contains"] = {"const": missing}
    else:
        properties["missing_inputs"]["maxItems"] = 0
    if required_refs:
        properties["used_refs"]["allOf"] = [
            {"contains": {"const": ref}} for ref in required_refs
        ]
    value = {"type": "object", "additionalProperties": False, "properties": properties,
             "required": list(properties)}
    Draft202012Validator.check_schema(value)
    return value


def freeze_behavior_suite(*, scope_id: str, frozen_at: datetime) -> LearningBehaviorSuite:
    """The suite is included in saved context before the first model dispatch."""
    stamp = aware(frozen_at).isoformat()
    common = {"schema_version": "research-method-control-input.v1", "scope_id": scope_id,
              "horizon": "24h", "as_of": stamp, "synthetic_control": True,
              "future_return": None, "financial_qualification": False}
    specifications = (
        ("confirmed-entry", "applies", BEHAVIORS[0],
         {"position": "flat", "instruction": "Decide the action under the supplied admitted conditional plan.",
          "facts": [{"ref": "entry-trigger", "available_at": stamp, "trigger_confirmed": True},
                    {"ref": "entry-risk", "available_at": stamp, "risk_budget_defined": True}],
          "plan": {"entry": "enter if trigger_confirmed and risk_budget_defined", "expired": False}},
         _expected(applies=True, actions=("enter",), required_refs=("entry-trigger", "entry-risk"))),
        ("entry-with-missing-risk", "excludes", BEHAVIORS[0],
         {"position": "flat", "facts": [{"ref": "entry-trigger", "available_at": stamp, "trigger_confirmed": True}],
          "plan": {"entry": "enter only with a defined risk budget", "risk_budget": None}},
         _expected(applies=False, actions=("wait", "unknown"), missing="risk_budget")),
        ("held-long-protection", "applies", BEHAVIORS[1],
         {"position": "long", "facts": [{"ref": "protective-trigger", "available_at": stamp, "invalidation_confirmed": True}],
          "plan": {"protection": "exit or reduce held exposure when invalidation is confirmed", "new_entry": "forbidden"}},
         _expected(applies=True, actions=("reduce", "exit"), required_refs=("protective-trigger",))),
        ("flat-has-no-position-to-exit", "excludes", BEHAVIORS[1],
         {"position": "flat", "facts": [{"ref": "protective-trigger", "available_at": stamp, "invalidation_confirmed": True}],
          "plan": {"protection": "exit existing exposure only", "new_entry": "forbidden"}},
         _expected(applies=False, actions=("wait", "analyze"))),
        ("available-facts-support-analysis", "applies", BEHAVIORS[2],
         {"position": "flat", "facts": [{"ref": "available-price", "available_at": stamp, "value": "100"}],
          "instruction": "Analyze only using known facts; no entry or exit plan has been supplied."},
         _expected(applies=True, actions=("analyze",), required_refs=("available-price",))),
        ("unknown-availability-is-not-current", "excludes", BEHAVIORS[2],
         {"position": "flat", "facts": [{"ref": "undated-price", "available_at": None, "value": "100"}],
          "instruction": "A current entry decision requires authenticated information availability."},
         _expected(applies=False, actions=("wait", "unknown"), missing="availability_time")),
    )
    cases = [dict(id=name, purpose=purpose, input_body=common | inputs,
                  expected_schema=expected, material_kind="synthetic_control",
                  known_at=stamp, tests_behaviors=[behavior])
             for name, purpose, behavior, inputs, expected in specifications]
    body = dict(schema_version="research-learning-behavior-suite.v1",
                id="spot-method-engineering.v1", registered_at=stamp,
                scope_ids=[scope_id], horizon="24h", cases=cases,
                supported_behavior_changes=list(BEHAVIORS))
    normalized = LearningBehaviorSuite.model_validate(body | {"payload_hash": "0" * 64}).model_dump(mode="json", exclude={"payload_hash"})
    return LearningBehaviorSuite.model_validate(normalized | {"payload_hash": canonical_hash(normalized)})


def read_context_suite(context):
    suite = LearningBehaviorSuite.model_validate(context["behavior_suite"])
    wire = suite.model_dump(mode="json", exclude={"payload_hash"})
    protocol = context["protocol"]
    if (canonical_hash(wire) != suite.payload_hash
            or suite.scope_ids != [protocol["scope_id"]]
            or suite.horizon != protocol["horizon"]
            or suite.registered_at < datetime.fromisoformat(context["binding"]["frozen_at"])
            or any(case.known_at > suite.registered_at for case in suite.cases)):
        raise ValueError("learning_behavior_suite_binding_invalid")
    expected = freeze_behavior_suite(scope_id=protocol["scope_id"], frozen_at=suite.registered_at)
    if expected != suite:
        raise ValueError("learning_behavior_suite_not_registered")
    return suite


def validate_reflection_cases(delta, context):
    if delta.outcome != "candidate":
        return
    suite = read_context_suite(context)
    cases = {(case_reference(case).ref, case_reference(case).hash): case for case in suite.cases}
    for hypothesis in delta.hypotheses:
        behavior = hypothesis.expected_behavior_change
        if behavior not in suite.supported_behavior_changes:
            raise ValueError("learning_behavior_change_not_covered")
        for refs, purpose in ((hypothesis.positive_case_refs, "applies"),
                              (hypothesis.negative_case_refs, "excludes")):
            if not refs:
                raise ValueError("learning_named_behavior_case_required")
            for ref in refs:
                case = cases.get((ref.ref, ref.hash))
                if case is None or case.purpose != purpose or behavior not in case.tests_behaviors:
                    raise ValueError("learning_behavior_case_binding_invalid")
