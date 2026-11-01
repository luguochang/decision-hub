"""Display relationships from the authenticated projection; no trust grant."""
from datetime import datetime


def validate_method_relations(name, value):
    def time(text):
        result = datetime.fromisoformat(text)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("method_time_aware_required")
        return result

    if name == "ResearchMethodViewV2TrialSource":
        if value["scope_id"] != f'crypto:spot:okx:{value["instrument"]}:trade:1m':
            raise ValueError("method_trial_scope_invalid")
        if value["terminal_kind"] == "closed_episode":
            if value["outcome_status"] != "settled" or value["settled_at"] is None or value["remaining_quantity"] != "0":
                raise ValueError("method_trial_closed_episode_invalid")
        elif value["execution_status"] == "filled" or value["outcome_status"] != "unscorable":
            raise ValueError("method_trial_attempt_terminal_invalid")
        if value["outcome_status"] == "unscorable" and value["net_pnl"] is not None:
            raise ValueError("method_trial_unknown_return_required")
    elif name == "ResearchMethodViewV2BehaviorSummary":
        cases, count = value["cases"], value["case_count"]
        if (count != len(cases)
            or value["applies_case_count"] != sum(c["purpose"] == "applies" for c in cases)
            or value["excludes_case_count"] != sum(c["purpose"] == "excludes" for c in cases)
            or value["historical_pit_case_count"] != sum(c["material_kind"] == "historical_pit" for c in cases)
            or value["synthetic_control_case_count"] != sum(c["material_kind"] == "synthetic_control" for c in cases)
            or len(value["attempts"]) != count * 2 or len(value["outputs"]) != count * 2):
            raise ValueError("method_trial_behavior_census_invalid")
    elif name == "ResearchMethodTrialProvenance":
        if not time(value["admitted_at"]) <= time(value["start_at"]) < time(value["end_at"]) or value["run_ordinal"] > value["max_runs"]:
            raise ValueError("method_trial_window_or_ordinal_invalid")
        if value["parent"] == value["candidate"] or time(value["behavior"]["completed_at"]) > time(value["admitted_at"]):
            raise ValueError("method_trial_provenance_invalid")
    elif name == "ResearchMethodViewV2ItemV2":
        if (value["generation_binding_ref"] is None) != (value["original_selection_ref"] is None):
            raise ValueError("method_generation_reference_pair_invalid")
        if (value["generation"] == 1) != (value["generation_binding_ref"] is None):
            raise ValueError("method_generation_original_required")
        if (value["origin"] == "trial_registered") != (value["trial"] is not None):
            raise ValueError("method_trial_origin_mismatch")
        if value["trial"] is not None and value["skill_version_id"] != value["trial"]["candidate"]["ref"]:
            raise ValueError("method_trial_candidate_mismatch")
    return value
