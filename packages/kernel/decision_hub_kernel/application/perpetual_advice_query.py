"""Owner/current-settings projection of already-published perpetual originals.

No native replay, archive/network reads, research admission, math or writes.
The unique publisher's immutable command and Run-local originals select advice.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    AccountSnapshotV2,
    ActionComparisonV2,
    ActionPlanRevisionV2,
    ConditionalMarketDistributionV1,
    DecisionSettingsPartitionV1,
    DecisionSnapshotV2,
    DecisionSourceRefV1,
    EventProbabilityV1,
    FrozenForecastV1,
    PositionContextV2,
    ProbabilityEstimateV1,
    PublicationBoundTargetV1,
    UtilityPolicyV2,
)

from ..persistence.action_repository import ActionRepository, StoredCommandReceipt
from ..persistence.db import ArtifactRecord, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..ports.market_path import aware_utc, canonical_hash
from .decision_settings import DecisionSettingsService
from .perpetual_research_binding import (
    ADMISSION_ROLE,
    canonical,
    read_perpetual_admission_identity_in_session,
)

SCHEMA = "perpetual-advice-current.v1"
PUBLISHER = "system:action-publication:v1"


def _fail():
    raise ValueError("perpetual_advice_original_unavailable")


def _wire_time(value):
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _original(session, run_id, reference, schema, model=None):
    ref = DecisionSourceRefV1.model_validate(reference)
    role = "perpetual_publication_original:" + canonical_hash(ref.model_dump(mode="json"))
    row_id = "perp_pub_" + canonical_hash([run_id, role])
    row = session.get(RunRoleOutputRecord, row_id, populate_existing=True)
    if row is None:
        _fail()
    body = json.loads(row.payload_json)
    if (
        (row.run_id, row.role, row.schema_version, row.payload_hash)
        != (run_id, role, schema, ref.hash)
        or canonical(body) != row.payload_json
        or canonical_hash(body) != ref.hash
        or body.get("schema_version", schema) != schema
    ):
        _fail()
    if model is not None:
        model.model_validate(body)
    # Preserve original timestamp spellings and their content reference hash.
    return body


def _advice_response(
    owner_id: str, partition: DecisionSettingsPartitionV1, ref: DecisionSourceRefV1,
    expected_settings_state_version: int, horizon: str, at: datetime,
) -> dict[str, Any]:
    return dict(
        schema_version=SCHEMA if horizon == "4h" else "perpetual-advice-current.v2",
        owner_id=owner_id,
        partition=partition.model_dump(mode="json"),
        settings_revision=ref.model_dump(mode="json"),
        settings_state_version=expected_settings_state_version,
        horizon=horizon,
        status="not_started",
        run_id=None,
        report_id=None,
        snapshot=None,
        revision=None,
        comparison=None,
        frozen_forecast=None,
        target=None,
        cost_assumptions=None,
        cost_assumptions_ref=None,
        event_probability=None,
        market_distribution=None,
        forecast_basis=None,
        candidate_probability=None,
        event_scenario_plan=None,
        context=dict(account_snapshot=None, position_context=None, utility_policy=None),
        gaps=[],
        updated_at=_wire_time(at),
        qualification="research_trial",
        execution="paper_only",
    )


class PerpetualAdviceQuery:
    def __init__(self, database, *, now=utcnow):
        self.database, self.now = database, now

    def current(
        self,
        owner_id,
        *,
        account_kind,
        account_id,
        instrument_id,
        profile_ref,
        expected_settings_ref,
        expected_settings_hash,
        expected_settings_state_version,
        horizon="4h",
    ):
        partition, ref, at = self._query_inputs(
            account_kind=account_kind, account_id=account_id, instrument_id=instrument_id,
            profile_ref=profile_ref, expected_settings_ref=expected_settings_ref,
            expected_settings_hash=expected_settings_hash,
            expected_settings_state_version=expected_settings_state_version, horizon=horizon,
        )
        with self.database.session() as session:
            return self._current_for_settings(
                session, owner_id, partition, ref, expected_settings_state_version, horizon, at
            )

    def current_in_session(
        self,
        session: Session,
        owner_id: str,
        *,
        account_kind: str,
        account_id: str,
        instrument_id: str,
        profile_ref: str,
        expected_settings_ref: str,
        expected_settings_hash: str,
        expected_settings_state_version: int,
        horizon: str = "4h",
        at: datetime | None = None,
    ) -> dict[str, Any]:
        partition, ref, at = self._query_inputs(
            account_kind=account_kind, account_id=account_id, instrument_id=instrument_id,
            profile_ref=profile_ref, expected_settings_ref=expected_settings_ref,
            expected_settings_hash=expected_settings_hash,
            expected_settings_state_version=expected_settings_state_version, horizon=horizon, at=at,
        )
        return self._current_for_settings(
            session, owner_id, partition, ref, expected_settings_state_version, horizon, at
        )

    def _query_inputs(
        self,
        *,
        account_kind: str,
        account_id: str,
        instrument_id: str,
        profile_ref: str,
        expected_settings_ref: str,
        expected_settings_hash: str,
        expected_settings_state_version: int,
        horizon: str = "4h",
        at: datetime | None = None,
    ) -> tuple[DecisionSettingsPartitionV1, DecisionSourceRefV1, datetime]:
        partition = DecisionSettingsPartitionV1.model_validate(
            dict(
                account_kind=account_kind,
                account_id=account_id,
                instrument_id=instrument_id,
                profile_ref=profile_ref,
            )
        )
        ref = DecisionSourceRefV1(ref=expected_settings_ref, hash=expected_settings_hash)
        if (
            horizon not in {"4h", "24h", "72h"}
            or type(expected_settings_state_version) is not int
            or expected_settings_state_version < 1
        ):
            raise ValueError("perpetual_advice_query_invalid")
        at = aware_utc(self.now() if at is None else at)
        return partition, ref, at

    def _current_for_settings(
        self, session: Session, owner_id: str, partition: DecisionSettingsPartitionV1,
        ref: DecisionSourceRefV1, expected_settings_state_version: int, horizon: str, at: datetime,
    ) -> dict[str, Any]:
        response = _advice_response(
            owner_id, partition, ref, expected_settings_state_version, horizon, at)
        current = DecisionSettingsService.current_in_session(session, owner_id, partition)
        if (
            current is None
            or current.revision.operation != "upsert"
            or current.revision.values is None
            or current.state_version != expected_settings_state_version
            or (current.revision.revision_id, current.revision.payload_hash)
            != (ref.ref, ref.hash)
        ):
            response.update(
                status="settings_changed", gaps=["saved_settings_revision_changed_or_missing"]
            )
            return response
        saved = current.revision.model_dump(mode="json")
        response["context"] = {key: saved["values"][key] for key in response["context"]}
        response["updated_at"] = _wire_time(current.revision.created_at)
        from .event_scenario_publication import (
            read_latest_published_event_scenario_plan_in_session,
        )
        from .event_scenario_watch import event_scenario_advice_view_in_session
        retained_event = read_latest_published_event_scenario_plan_in_session(
            session, owner_id=owner_id, settings_ref=ref.model_dump(mode="json"),
            partition=partition.model_dump(mode="json"), horizon=horizon, at=at,
        )
        response["event_scenario_plan"] = event_scenario_advice_view_in_session(
            session, bundle=retained_event, at=at
        )
        body = RunRoleOutputRecord.payload_json
        filters = [
            RunRoleOutputRecord.role == ADMISSION_ROLE,
            func.json_extract(body, "$.context.horizon") == horizon,
            func.json_extract(body, "$.owner_id") == owner_id,
            func.json_extract(body, "$.settings_revision.revision_id") == ref.ref,
            func.json_extract(body, "$.settings_revision.payload_hash") == ref.hash,
            func.json_extract(body, "$.settings_state_version")
            == expected_settings_state_version,
        ]
        filters.extend(
            func.json_extract(body, "$.partition." + key) == value
            for key, value in partition.model_dump(mode="json").items()
        )
        run = session.scalar(
            select(RunRecord)
            .join(RunRoleOutputRecord, RunRoleOutputRecord.run_id == RunRecord.run_id)
            .where(*filters)
            .order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
            .limit(1)
        )
        if run is None:
            return response
        return self._current_run_in_session(
            session, owner_id, partition, ref, expected_settings_state_version, horizon,
            response, at, run, saved,
        )

    def _current_run_in_session(
        self, session: Session, owner_id: str, partition: DecisionSettingsPartitionV1,
        ref: DecisionSourceRefV1, expected_settings_state_version: int, horizon: str,
        response: dict[str, Any], at: datetime, run: RunRecord, saved: dict[str, Any],
    ) -> dict[str, Any]:
        admission, admission_ref = read_perpetual_admission_identity_in_session(
            session, run.run_id
        )
        if (
            admission["owner_id"] != owner_id
            or admission["partition"] != response["partition"]
            or admission["settings_revision"] != saved
            or admission["settings_state_version"] != expected_settings_state_version
            or admission["context"]["horizon"] != horizon
        ):
            _fail()
        response.update(
            run_id=run.run_id,
            report_id=run.artifact_id,
            updated_at=_wire_time(as_utc(run.updated_at)),
        )
        repo = ActionRepository(session)
        command_id = "publish_" + canonical_hash([run.run_id, admission["opportunity_id"]])
        published = repo.get_command(PUBLISHER, command_id)
        if published is None:
            if run.status in {"admitted", "running"}:
                status = "researching"
            elif run.status in {"failed", "cancelled"}:
                status = "failed"
            elif run.status in {"completed", "degraded"}:
                status = "awaiting_publication"
            else:
                _fail()
            response.update(status=status, gaps=["research_" + status])
            return response
        return self._published(
            session, repo, run, admission, admission_ref, published, response, at
        )

    @staticmethod
    def _published(
        session, repo, run, admission, admission_ref, published, response, at,
        *, frozen_only: bool = False,
    ):
        result = published.response
        artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
        if (
            published.operation != "publish"
            or result.get("status") != "published"
            or result.get("opportunity_id") != admission["opportunity_id"]
            or result.get("qualification") != "research_trial"
            or result.get("execution") != "paper_only"
            or run.status not in {"completed", "degraded"}
            or artifact is None
            or (artifact.run_id, artifact.event_id) != (run.run_id, run.event_id)
        ):
            _fail()
        snapshot = repo.get_snapshot(result["decision_id"])
        if (
            snapshot is None
            or snapshot.schema_version != "decision-snapshot.v2"
            or snapshot.snapshot_hash != result["snapshot_hash"]
            or snapshot.run_id != run.run_id
            or snapshot.opportunity_id != admission["opportunity_id"]
            or snapshot.context.model_dump(mode="json") != admission["context"]
            or snapshot.bindings.settings.model_dump(mode="json") != response["settings_revision"]
            or snapshot.known_at != published.accepted_at
            or published.accepted_at > at
        ):
            _fail()
        DecisionSnapshotV2.model_validate(snapshot.model_dump(mode="json"))
        branches = result.get("branches", [])
        if len(branches) != 1:
            _fail()
        branch = branches[0]
        revision = repo.get_revision(branch["revision_id"])
        if (
            revision is None
            or revision.schema_version != "decision-action-plan-revision.v2"
            or revision.payload_hash != branch["revision_hash"]
            or revision.plan_id != branch["plan_id"]
            or revision.plan_id != published.plan_id
            or revision.decision_id != snapshot.id
            or revision.context != snapshot.context
            or revision.bindings != snapshot.bindings
            or revision.position_context != branch["position_context"]
        ):
            _fail()
        ActionPlanRevisionV2.model_validate(revision.model_dump(mode="json"))
        bindings = snapshot.bindings.model_dump(mode="json")
        comparison = _original(
            session, run.run_id, bindings["comparison"], "action-comparison.v2", ActionComparisonV2
        )
        frozen = _original(
            session, run.run_id, bindings["frozen_forecast"], "frozen-forecast.v1", FrozenForecastV1
        )
        target = _original(
            session,
            run.run_id,
            bindings["final_target"],
            "publication-bound-target.v1",
            PublicationBoundTargetV1,
        )
        derivation = _original(
            session, run.run_id, bindings["derivation"], "perpetual-publication-derivation.v1"
        )
        materials = _original(
            session,
            run.run_id,
            derivation["research_materials_ref"],
            "perpetual-research-materials.v1",
        )
        if (
            derivation["admission_ref"] != admission_ref
            or derivation["comparison_ref"] != bindings["comparison"]
            or derivation["final_target_ref"] != bindings["final_target"]
            or materials["context"] != admission["context"]
            or frozen["final_target_ref"] != bindings["final_target"]
            or frozen["opportunity_id"] != admission["opportunity_id"]
            or comparison["target_ref"] != bindings["final_target"]
            or target["candidate_target_ref"] != bindings["candidate_target"]
            or target["anchor_rule_ref"] != bindings["anchor_rule"]
            or derivation["candidate_id_mapping"].get(comparison["selected_action_id"])
            != revision.selected_candidate.candidate_id
        ):
            _fail()
        context = {}
        from .perpetual_research_inputs import read_effective_inputs_in_session

        effective = read_effective_inputs_in_session(
            session, admission, purpose="frozen_read"
        ).effective_values.model_dump(mode="json")
        for name, model in (
            ("account_snapshot", AccountSnapshotV2),
            ("position_context", PositionContextV2),
            ("utility_policy", UtilityPolicyV2),
        ):
            value = materials[name]
            model.model_validate(value)
            if value != effective[name]:
                _fail()
            context[name] = value
        cost_ref = materials["cost_assumptions_ref"]
        costs = _original(session, run.run_id, cost_ref, "perpetual-cost-assumptions.v1")
        if costs != materials["cost_assumptions"] or cost_ref != admission["cost_assumptions_ref"]:
            _fail()
        original_probabilities = {}
        for name, schema, model in (
            ("event_probability", "event-probability.v1", EventProbabilityV1),
            (
                "market_distribution",
                "conditional-market-distribution.v1",
                ConditionalMarketDistributionV1,
            ),
            ("candidate_probability", "probability-estimate.v1", ProbabilityEstimateV1),
        ):
            original_probabilities[name] = _original(
                session, run.run_id, frozen[name + "_ref"], schema, model
            )
        response.update(
            status=(
                "expired"
                if at
                >= min(
                    revision.valid_until, PublicationBoundTargetV1.model_validate(target).window_end
                )
                else "available"
            ),
            snapshot=snapshot.model_dump(mode="json"),
            revision=revision.model_dump(mode="json"),
            comparison=comparison,
            frozen_forecast=frozen,
            target=target,
            cost_assumptions=costs,
            cost_assumptions_ref=cost_ref,
            context=context,
            gaps=list(dict.fromkeys([*snapshot.gaps, *result.get("gaps", [])])),
            updated_at=_wire_time(published.accepted_at),
            **original_probabilities,
        )
        from .perpetual_forecast_basis import forecast_basis

        response["forecast_basis"] = forecast_basis(
            run_id=run.run_id, horizon=target["horizon"], materials=materials,
            materials_ref=derivation["research_materials_ref"], derivation=derivation,
            target=target, market=original_probabilities["market_distribution"],
            model_ref=frozen["market_distribution_ref"],
        )
        if frozen_only:
            return dict(response, derivation=derivation)
        from .perpetual_condition_monitor import condition_new_exposure_blocked_in_session
        if condition_new_exposure_blocked_in_session(
            session, owner_id=admission["owner_id"], plan_id=revision.plan_id,
            revision_ref={"ref": revision.revision_id, "hash": revision.payload_hash},
        ):
            response["status"] = "invalidated"
            response["gaps"] = list(dict.fromkeys([
                *response["gaps"], "perpetual_condition_invalidated_new_exposure",
            ]))
        from .event_scenario_publication import read_published_event_scenario_plan_in_session
        from .event_scenario_watch import event_scenario_advice_view_in_session

        event_bundle = read_published_event_scenario_plan_in_session(
            session, run_id=run.run_id, owner_id=admission["owner_id"], at=at
        )
        if event_bundle is not None:
            response["event_scenario_plan"] = event_scenario_advice_view_in_session(
                session, bundle=event_bundle, at=at
            )
        if response.get("event_scenario_plan") is not None:
            event_view = response["event_scenario_plan"]
            if (response["status"] == "invalidated" and event_bundle is not None
                    and revision.plan_id in event_bundle["plan_ids"]):
                event_view["current_state"] = "invalidated"
            if event_view["selection_original"] is not None:
                response["updated_at"] = max(
                    response["updated_at"], event_view["selection_original"]["recorded_at"]
                )
        return response


def read_perpetual_published_originals_in_session(
    session: Session, *, run: RunRecord, admission: dict[str, JsonValue],
    admission_ref: dict[str, JsonValue], published: StoredCommandReceipt,
    at: datetime, frozen_only: bool = False,
) -> object:
    """Reuse the exact Advice publication reader without a current-settings lookup."""
    settings = admission["settings_revision"]
    response = {
        "settings_revision": {
            "ref": settings["revision_id"],
            "hash": settings["payload_hash"],
        }
    }
    return PerpetualAdviceQuery._published(
        session,
        ActionRepository(session),
        run,
        admission,
        admission_ref,
        published,
        response,
        aware_utc(at),
        frozen_only=frozen_only,
    )
