"""Selected perpetual forecast horizon, independent of the Task runtime budget."""

HORIZON_SECONDS = {"4h": 14400, "24h": 86400, "72h": 259200}
# Approved perpetual research runtime; forecast/slot publication clocks are separate.
# Legacy Pack execution budgets remain unchanged.
PERPETUAL_RESEARCH_RUNTIME_SECONDS = 3600


def require_perpetual_horizon(value):
    if not isinstance(value, str) or value not in HORIZON_SECONDS:
        raise ValueError("perpetual_research_horizon_invalid")
    return value


def perpetual_horizon_seconds(value):
    return HORIZON_SECONDS[require_perpetual_horizon(value)]


def parse_perpetual_research_command(value):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        PerpetualResearchCommandV1,
        PerpetualResearchCommandV2,
        PerpetualResearchCommandV3,
    )

    body = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    if not isinstance(body, dict):
        raise ValueError("perpetual_research_command_invalid")
    model = {
        "perpetual-research-command.v1": PerpetualResearchCommandV1,
        "perpetual-research-command.v2": PerpetualResearchCommandV2,
        "perpetual-research-command.v3": PerpetualResearchCommandV3,
    }.get(body.get("schema_version"))
    if model is None:
        raise ValueError("perpetual_research_command_version_unsupported")
    return model.model_validate(body)


def parse_perpetual_research_receipt(body):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        PerpetualResearchReceiptV1,
        PerpetualResearchReceiptV2,
        PerpetualResearchReceiptV3,
    )

    model = {
        "perpetual-research-receipt.v1": PerpetualResearchReceiptV1,
        "perpetual-research-receipt.v2": PerpetualResearchReceiptV2,
        "perpetual-research-receipt.v3": PerpetualResearchReceiptV3,
    }.get(body.get("schema_version"))
    if model is None:
        raise ValueError("perpetual_research_receipt_version_unsupported")
    return model.model_validate(body)


def perpetual_deadline_policy_original():
    """Fixed V1 policy; its complete body is included in the reviewer original."""
    from ..ports.market_path import canonical_hash
    from ..ports.perpetual_research_inputs import PerpetualResearchDeadlinePolicyV1

    body = PerpetualResearchDeadlinePolicyV1(
        schema_version="perpetual-research-deadline-policy.v1",
        runtime_seconds=3600,
        operation_margin_seconds=1800,
        candidate_basis="entry_prepared_at",
        runtime_basis="admitted_at",
        trigger_scope="new_admission_only",
    ).model_dump(mode="json")
    return body, dict(ref="decision-policy:perpetual-research-deadline.v1", hash=canonical_hash(body))


def derive_perpetual_deadlines(*, prepared_at, admitted_at=None, policy=None):
    """A frozen candidate deadline and a separate, finite accepted-Run budget."""
    from datetime import timedelta

    from ..ports.market_path import aware_utc

    from ..ports.perpetual_research_inputs import PerpetualResearchDeadlinePolicyV1

    policy = (perpetual_deadline_policy_original()[0] if policy is None
              else PerpetualResearchDeadlinePolicyV1.model_validate(policy).model_dump(mode="json"))
    values = dict(candidate_valid_until=aware_utc(prepared_at) + timedelta(
        seconds=policy["runtime_seconds"] + policy["operation_margin_seconds"]
    ))
    if admitted_at is not None:
        values["runtime_deadline_at"] = aware_utc(admitted_at) + timedelta(seconds=policy["runtime_seconds"])
    return values


def authenticate_perpetual_deadlines(admission):
    """Authenticate the frozen V3 policy; legacy originals keep their clocks."""
    from datetime import datetime

    from ..ports.market_path import aware_utc, canonical_hash
    from ..ports.perpetual_research_inputs import PerpetualResearchDeadlinePolicyV1

    version = admission["schema_version"]
    policy = admission.get("allowed_domain_policy", {}).get("deadline_policy")
    if version in {"perpetual-research-admission.v1", "perpetual-research-admission.v2"}:
        if policy is not None or "deadline_policy_ref" in admission or "deadline_prepared_at" in admission:
            raise ValueError("perpetual_research_deadline_legacy_policy_forbidden")
        return None
    if version != "perpetual-research-admission.v3":
        raise ValueError("perpetual_research_deadline_version_unsupported")
    body = PerpetualResearchDeadlinePolicyV1.model_validate(policy).model_dump(mode="json")
    expected_ref = dict(ref="decision-policy:perpetual-research-deadline.v1", hash=canonical_hash(body))
    prepared = aware_utc(datetime.fromisoformat(admission["deadline_prepared_at"]))
    admitted = aware_utc(datetime.fromisoformat(admission["admitted_at"]))
    values = derive_perpetual_deadlines(prepared_at=prepared, admitted_at=admitted, policy=body)
    if (
        admission["deadline_policy_ref"] != expected_ref
        or not prepared <= admitted < values["candidate_valid_until"]
        or aware_utc(datetime.fromisoformat(admission["valid_until"])) != values["candidate_valid_until"]
        or aware_utc(datetime.fromisoformat(admission["runtime_started_at"])) != admitted
        or aware_utc(datetime.fromisoformat(admission["runtime_deadline_at"])) != values["runtime_deadline_at"]
        or any(aware_utc(datetime.fromisoformat(value["valid_until"])) != values["candidate_valid_until"]
               for value in (admission["candidate_target"], admission["anchor_rule"], admission["allowed_domain_policy"]))
    ):
        raise ValueError("perpetual_research_deadline_original_mismatch")
    source = admission.get("automatic_source")
    if source is not None and admitted >= aware_utc(datetime.fromisoformat(source["source"]["deadline_at"])):
        raise ValueError("perpetual_schedule_slot_expired")
    return body
