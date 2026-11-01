"""Synthetic, correctly hashed DTO factories for isolated Action tests only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionPlanHead,
    ActionPlanRevision,
    DecisionSnapshot,
    Opportunity,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

ASOF = datetime(2026, 10, 13, 4, 0, tzinfo=UTC)
HASH = "a" * 64


def seal(model, body, hash_field):
    draft = model.model_validate(body | {hash_field: "0" * 64})
    wire = draft.model_dump(mode="json")
    wire[hash_field] = canonical_hash({k: v for k, v in wire.items() if k != hash_field})
    return model.model_validate(wire)


def make_opportunity(*, asset="BTC", horizon="24h", **changes):
    return Opportunity.model_validate(
        {
            "schema_version": "decision-opportunity.v1",
            "id": "opportunity-1",
            "key": f"{asset}:{horizon}:2026-10-13T04:00:00Z",
            "scope_id": f"crypto:spot:okx:{asset}-USDT:trade:1m",
            "horizon": horizon,
            "kind": "scheduled",
            "event_ref": None,
            "registered_at": ASOF,
            "time_start": ASOF,
            "time_end": ASOF
            + timedelta(seconds={"30m": 1800, "24h": 86400, "72h": 259200}[horizon]),
            "target_spec_hash": HASH,
            "recipe_hash": HASH,
            "cohort": "prospective",
            "status": "open",
            "reason": None,
            **changes,
        }
    )


def make_snapshot(opportunity=None, **changes):
    opportunity = opportunity or make_opportunity()
    return seal(
        DecisionSnapshot,
        {
            "schema_version": "decision-snapshot.v1",
            "id": "decision-1",
            "opportunity_id": opportunity.id,
            "run_id": "run-1",
            "source_result_ref": "synthetic:test-only",
            "source_result_hash": HASH,
            "decision_cutoff_at": opportunity.time_start,
            "known_at": opportunity.time_start,
            "scope_id": opportunity.scope_id,
            "horizon": opportunity.horizon,
            "hypotheses": [
                {
                    "direction": "range",
                    "evidence_refs": ["fact-1"],
                    "counter_refs": ["fact-2"],
                    "rationale": "Synthetic comparison scenario",
                }
            ],
            "factor_refs": [],
            "direction_status": "eligible",
            "path_status": "experimental",
            "probability": {
                "target_id": "return-positive",
                "value": None,
                "kind": "unavailable",
                "provenance_ref": None,
            },
            "gaps": [],
            "skill_refs": [],
            **changes,
        },
        "snapshot_hash",
    )


def make_revision(snapshot=None, opportunity=None, **changes):
    opportunity = opportunity or make_opportunity()
    snapshot = snapshot or make_snapshot(opportunity)
    return seal(
        ActionPlanRevision,
        {
            "schema_version": "decision-action-plan-revision.v1",
            "revision_id": "revision-1",
            "plan_id": "plan-1",
            "decision_id": snapshot.id,
            "opportunity_id": opportunity.id,
            "revision_no": 1,
            "previous_id": None,
            "scope_id": opportunity.scope_id,
            "horizon": opportunity.horizon,
            "as_of": snapshot.decision_cutoff_at,
            "published_at": snapshot.known_at + timedelta(minutes=1),
            "valid_until": opportunity.time_end,
            "action": "wait",
            "position_context": "flat",
            "advice_qualification": "research_trial",
            "decision_status": "unknown",
            "entry": [],
            "exit": [],
            "invalidation": [],
            "size_policy": {"kind": "unspecified", "amount": None, "unit": None},
            "cost_policy_ref": "cost-v1",
            "cost_policy_hash": HASH,
            "target_spec_ref": "target-v1",
            "target_spec_hash": opportunity.target_spec_hash,
            "basis_refs": [{"ref": "fact-1", "hash": HASH}],
            "reason": "Synthetic action test",
            **changes,
        },
        "payload_hash",
    )


def make_head(revision=None, **changes):
    revision = revision or make_revision()
    return ActionPlanHead.model_validate(
        {
            "schema_version": "decision-action-plan-head.v1",
            "plan_id": revision.plan_id,
            "current_revision_id": revision.revision_id,
            "state_version": 1,
            "state": "armed",
            "monitor_state": "active",
            "next_due": revision.published_at,
            "valid_until": revision.valid_until,
            "lease_owner": None,
            "lease_generation": 0,
            "lease_expires_at": None,
            "last_input_watermark": None,
            "enter_streak": 0,
            **changes,
        }
    )


def seed_plan(session, *, opportunity=None, snapshot=None, revision=None, head=None):
    from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository

    opportunity = opportunity or make_opportunity()
    snapshot = snapshot or make_snapshot(opportunity)
    revision = revision or make_revision(snapshot, opportunity)
    head = head or make_head(revision)
    repository = ActionRepository(session)
    repository.register_opportunity(opportunity)
    repository.append_snapshot(snapshot)
    repository.create_plan(revision, head=head)
    return opportunity, snapshot, revision, head
