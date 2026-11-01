"""Additive canonical-base helper; call after canonical GenerationBindingV1 shape.

Pure field relations only. Original rows, package, method, scope, lease and
permission remain authenticated independently by SkillGenerationService.
"""
from datetime import datetime


def validate_generation_binding_relations(value):
    def time(text):
        result = datetime.fromisoformat(text)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("skill_generation_time_aware_required")
        return result

    known = time(value["method_first_known_at"])
    cutoff = time(value["producer_cutoff_at"])
    prepared = time(value["prepared_at"])
    selected = time(value["selected_at"])
    deadline = time(value["binding_deadline_at"])
    if not known <= cutoff <= prepared <= selected < deadline:
        raise ValueError("skill_generation_contract_time_order_invalid")
    original = value["original_selection_ref"]
    if (
        original["output_id"] != value["selection_id"]
        or original["role"] != "research_skill_selection"
        or original["schema_version"]
        != ("research-skill-selection.v3"
            if value["schema_version"] == "research-skill-generation-binding.v2"
            else "research-skill-selection.v2" if value["selection_origin"] == "trial_registered"
            else "research-skill-selection.v1")
        or not cutoff <= time(original["recorded_at"]) <= prepared
        or time(value["prepared_execution_ref"]["recorded_at"]) != prepared
        or value["prepared_execution_ref"]["role"]
        != "dsh_execution_request:" + str(value["generation"])
    ):
        raise ValueError("skill_generation_contract_reference_invalid")
    if value["selection_origin"] == "trial_registered" and value["target"] != value["trial_target"]:
        raise ValueError("skill_generation_contract_trial_target_invalid")
    return value
