"""Actual mixed SQLite registry; shape fixtures grant no financial authority."""

import json
from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionPlanRevisionV2,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionPlanHead,
    ActionPlanRevision,
    DecisionSnapshot,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.contracts.test_d2_candidate_contract import publication_revision


def test_legacy_monitor_claims_v1_but_never_expired_paused_v2(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database("sqlite:///" + (tmp_path / "mixed.sqlite3").as_posix())
    db.create_all()
    raw = publication_revision()
    raw["payload_hash"] = canonical_hash({k: v for k, v in raw.items() if k != "payload_hash"})
    v2 = ActionPlanRevisionV2.model_validate(raw)
    body = dict(
        schema_version="decision-action-plan-revision.v1",
        revision_id="legacy-revision",
        plan_id="legacy-plan",
        decision_id="legacy-decision",
        opportunity_id="legacy-opportunity",
        revision_no=1,
        previous_id=None,
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        horizon="24h",
        as_of="2026-01-01T00:00:00Z",
        published_at="2026-01-01T00:01:00Z",
        valid_until="2026-01-01T01:00:00Z",
        action="wait",
        position_context="flat",
        advice_qualification="research_trial",
        decision_status="unknown",
        entry=[],
        exit=[],
        invalidation=[],
        size_policy=dict(kind="unspecified", amount=None, unit=None),
        cost_policy_ref="synthetic:cost",
        cost_policy_hash="a" * 64,
        target_spec_ref="synthetic:target",
        target_spec_hash="b" * 64,
        basis_refs=[dict(ref="synthetic:basis", hash="c" * 64)],
        reason="Typed registry fixture only",
    )
    body["payload_hash"] = canonical_hash(body)
    v1 = ActionPlanRevision.model_validate(body)
    snapshot_body = dict(
        schema_version="decision-snapshot.v1",
        id=v1.decision_id,
        opportunity_id=v1.opportunity_id,
        run_id="legacy-run",
        source_result_ref="synthetic:result",
        source_result_hash="d" * 64,
        decision_cutoff_at="2026-01-01T00:00:00Z",
        known_at="2026-01-01T00:00:00Z",
        scope_id=v1.scope_id,
        horizon=v1.horizon,
        hypotheses=[
            dict(
                direction="range",
                evidence_refs=[],
                counter_refs=[],
                rationale="Synthetic registry fixture",
            )
        ],
        factor_refs=[],
        direction_status="unknown",
        path_status="unknown",
        probability=dict(
            target_id="synthetic:target", value=None, kind="unavailable", provenance_ref=None
        ),
        gaps=[],
        skill_refs=[],
    )
    snapshot_body["snapshot_hash"] = canonical_hash(snapshot_body)
    snapshot = DecisionSnapshot.model_validate(snapshot_body)
    now = datetime(2026, 1, 1, 2, tzinfo=UTC)
    try:
        with db.session() as session:
            begin_immediate(session)
            for revision in (v1, v2):
                session.add(
                    DecisionOpportunityRecord(
                        opportunity_id=revision.opportunity_id,
                        schema_version="decision-opportunity.v1"
                        if revision is v1
                        else "decision-opportunity.v2",
                        key=revision.opportunity_id,
                        scope_id=revision.scope_id,
                        horizon=revision.horizon,
                        kind="event",
                        event_ref=None,
                        recipe_hash="e" * 64,
                        target_hash="f" * 64,
                        registered_at=revision.as_of,
                        start_at=revision.as_of,
                        end_at=now,
                        cohort="diagnostic",
                        status="decided",
                        reason=None,
                    )
                )
                session.add(
                    ActionPlanRevisionRecord(
                        revision_id=revision.revision_id,
                        plan_id=revision.plan_id,
                        revision_no=revision.revision_no,
                        decision_id=revision.decision_id,
                        opportunity_id=revision.opportunity_id,
                        previous_id=None,
                        position_context=revision.position_context,
                        published_at=revision.published_at,
                        valid_until=revision.valid_until,
                        payload_json=json.dumps(
                            revision.model_dump(mode="json"),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                        payload_hash=revision.payload_hash,
                    )
                )
                head = ActionPlanHead.model_validate(
                    dict(
                        schema_version="decision-action-plan-head.v1",
                        plan_id=revision.plan_id,
                        current_revision_id=revision.revision_id,
                        state_version=1,
                        state="proposed",
                        monitor_state="paused",
                        next_due=None,
                        valid_until=revision.valid_until,
                        lease_owner=None,
                        lease_expires_at=None,
                        lease_generation=0,
                        last_input_watermark=None,
                        enter_streak=0,
                    )
                )
                data = head.model_dump(mode="python", exclude={"schema_version"})
                data["next_due_at"] = data.pop("next_due")
                session.add(ActionPlanHeadRecord(**data))
            session.flush()
            session.get(
                DecisionOpportunityRecord, v1.opportunity_id
            ).target_hash = v1.target_spec_hash
            ActionRepository(session).append_snapshot(snapshot)
        with db.session() as session:
            repo = ActionRepository(session)
            assert repo.list_due_monitor_plan_ids(now=now) == (v1.plan_id,)
            before = dict(session.get(ActionPlanHeadRecord, v2.plan_id).__dict__)
            before.pop("_sa_instance_state")
        with db.session() as session:
            begin_immediate(session)
            repo = ActionRepository(session)
            assert repo.claim_monitor(v2.plan_id, owner="test-monitor", now=now) is None
            after = dict(session.get(ActionPlanHeadRecord, v2.plan_id).__dict__)
            after.pop("_sa_instance_state")
            assert after == before
            claim = repo.claim_monitor(v1.plan_id, owner="test-monitor", now=now)
            assert (
                claim is not None
                and claim.revision.schema_version == "decision-action-plan-revision.v1"
            )
            assert claim.head.lease_owner == "test-monitor"
    finally:
        db.engine.dispose()
