"""Engineering trial contracts and deterministic gates, no activation privilege.

Registration and selection need the identity-only original service described in
PLAN. These pure DTO checks must never be treated as authenticated receipts.
"""
from packages.kernel.decision_hub_kernel.ports.research_learning import MethodTrialProtocol, MethodTrialReceipt
from datetime import datetime
from typing import Literal
from packages.kernel.decision_hub_kernel.ports.research_learning import BehaviorReceipt, Frozen, BehaviorOriginalRef as Ref
from packages.kernel.decision_hub_kernel.application.research_skill_behavior import require_normal_trial_behavior
from pydantic import AwareDatetime, Field, model_validator
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

def derive_trial_receipt(protocol: MethodTrialProtocol, behavior: BehaviorReceipt, *, now: datetime):
    """Pure gate after service-owned original reads; it supplies no permission."""
    require_normal_trial_behavior(behavior)
    if behavior.protocol != protocol.behavior_protocol or behavior.package_stage != protocol.package_stage:
        raise ValueError('trial_behavior_binding_mismatch')
    if not protocol.registered_at <= behavior.completed_at <= now <= protocol.start_at:
        raise ValueError('trial_behavior_or_admission_time_invalid')
    body = dict(schema_version='research-method-trial-receipt.v1', id='method_trial_' + protocol.payload_hash, protocol=Ref(ref=protocol.id, hash=protocol.payload_hash), behavior=Ref(ref=behavior.id, hash=behavior.payload_hash), package_stage=protocol.package_stage, candidate=protocol.candidate, parent=protocol.parent, admitted_at=now, status='trial_registered', financial_qualified=False, payload_hash='0' * 64)
    parsed = MethodTrialReceipt.model_validate(body)
    wire = parsed.model_dump(mode='json', exclude={'payload_hash'})
    return MethodTrialReceipt.model_validate(wire | {'payload_hash': canonical_hash(wire)})

def require_future_trial_selection(*, protocol: MethodTrialProtocol, receipt: MethodTrialReceipt, run_id: str, generation: int, turn_kind: str, cutoff: datetime, scope_id: str, horizon: str, target: Ref, actual_selected_runs: tuple[str, ...], now: datetime):
    """Run metadata and census must come from the same authenticated readset/CAS."""
    if canonical_hash(receipt.model_dump(mode='json', exclude={'payload_hash'})) != receipt.payload_hash:
        raise ValueError('trial_receipt_hash_mismatch')
    if receipt.protocol != Ref(ref=protocol.id, hash=protocol.payload_hash) or receipt.candidate != protocol.candidate or receipt.parent != protocol.parent or (receipt.package_stage != protocol.package_stage):
        raise ValueError('trial_receipt_protocol_mismatch')
    if (scope_id, horizon, target) != (protocol.scope_id, protocol.horizon, protocol.target):
        raise ValueError('trial_run_scope_or_target_mismatch')
    if run_id in actual_selected_runs:
        raise ValueError('trial_original_selection_read_required')
    if generation != 1 or turn_kind != 'research_round':
        raise ValueError('trial_first_dispatch_required')
    if not receipt.admitted_at <= protocol.start_at <= cutoff < protocol.end_at or cutoff > now:
        raise ValueError('trial_run_outside_prospective_window')
    if len(set(actual_selected_runs)) != len(actual_selected_runs):
        raise ValueError('trial_selection_census_invalid')
    if len(actual_selected_runs) >= protocol.max_runs:
        raise ValueError('trial_run_quota_exhausted')
    return protocol.candidate
