"""Immutable event sidecar of the unique authenticated action publisher.

The publisher supplies an already replayed native packet. Readers authenticate
its original command and stored bodies; they do not infer review from a hash.
"""

import json
from dataclasses import replace

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from ..decision.action_comparison import _utc
from ..persistence.action_repository import ActionRepository
from ..persistence.db import RunRoleOutputRecord, as_utc
from ..ports.event_scenario_manifest import unpack_event_scenario_manifest
from ..ports.event_scenario_preparation import canonical, derived_ref
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_action_synthesis import (
    primary_perpetual_authorization,
    primary_perpetual_review_request,
    primary_perpetual_synthesis,
    validate_action_review_binding_v2,
)

ROLE = "perpetual_event_scenario_publication_v1"
SCHEMA = "event-scenario-publication.v1"
PUBLISHER = "system:action-publication:v1"


def primary_publication_trusted(trusted):
    """Project the exact nested bodies actually authorized by the V3 child."""
    synthesis = json.loads(trusted.synthesis_json)
    if synthesis["schema_version"] != "research-decision-synthesis.v3":
        return trusted
    request = json.loads(trusted.review_request_json)
    authorization = json.loads(trusted.authorization_json)
    validate_action_review_binding_v2(request, authorization)
    return replace(
        trusted,
        synthesis_json=canonical(primary_perpetual_synthesis(synthesis)),
        review_request_json=canonical(primary_perpetual_review_request(request)),
        authorization_json=canonical(primary_perpetual_authorization(authorization)),
    )


def store_event_scenario_publication_in_session(
    session,
    *,
    trusted,
    publishing_request_id,
    publishing_payload_hash,
    plan_ids,
    decision_id,
    snapshot_hash,
    at,
):
    synthesis = json.loads(trusted.synthesis_json)
    if synthesis["schema_version"] != "research-decision-synthesis.v3":
        return None
    from ..decision.perpetual_publication import PerpetualPublicationOriginal
    from .perpetual_action_publication import _store_original

    request = json.loads(trusted.review_request_json)
    authorization = json.loads(trusted.authorization_json)
    validate_action_review_binding_v2(request, authorization)
    materials = json.loads(trusted.materials_json)
    rows = materials["review_originals"]["originals"]
    event_rows = [
        row
        for row in rows
        if row["source_kind"] == "server_material" and row["role"] == "event_scenario_plan"
    ]
    if len(event_rows) != 1:
        raise ValueError("event_publication_native_manifest_missing")
    prepared = json.loads(event_rows[0]["body_json"])
    manifest = prepared["manifest"]
    expanded = unpack_event_scenario_manifest(manifest)
    plan = expanded["event_plan"]
    if (
        derived_ref("event_scenario_action_plan", plan) != synthesis["event_plan_ref"]
        or derived_ref("event_scenario_preparation_manifest", manifest)
        != synthesis["event_plan_manifest_ref"]
        or derived_ref("event_scenario_action_plan_draft", expanded["normalized_event_plan_draft"])
        != synthesis["event_plan_draft_ref"]
    ):
        raise ValueError("event_publication_native_manifest_mismatch")

    def store(body, reference, role):
        _store_original(
            session,
            trusted.run_id,
            PerpetualPublicationOriginal(
                role=role,
                schema_version=body["schema_version"],
                reference=DecisionSourceRefV1.model_validate(reference),
                body_json=canonical(body),
            ),
            at,
        )

    store(manifest, synthesis["event_plan_manifest_ref"], "event_scenario_manifest")
    store(synthesis, request["synthesis_ref"], "event_scenario_synthesis")
    store(request, authorization["review_request_ref"], "event_scenario_review_request")
    authorization_ref = derived_ref("event_scenario_review_authorization", authorization)
    store(authorization, authorization_ref, "event_scenario_review_authorization")
    method_rows = [
        row
        for row in rows
        if row["source_kind"] == "server_material" and row["role"] == "event_scenario_method"
    ]
    if len(method_rows) != 1 or method_rows[0]["original_ref"] != plan["method_ref"]:
        raise ValueError("event_publication_native_method_missing")
    store(json.loads(method_rows[0]["body_json"]), plan["method_ref"], "event_scenario_method")
    body = dict(
        schema_version=SCHEMA,
        owner_id=trusted.owner_id,
        origin_run_id=trusted.run_id,
        opportunity_id=trusted.opportunity_id,
        plan_ref=synthesis["event_plan_ref"],
        manifest_ref=synthesis["event_plan_manifest_ref"],
        event_plan_draft_ref=synthesis["event_plan_draft_ref"],
        native_synthesis_ref=request["synthesis_ref"],
        native_review_request_ref=authorization["review_request_ref"],
        review_authorization_ref=authorization_ref,
        source_emission_ref=prepared["source_emission_ref"],
        source_emission_context=prepared["source_emission_context"],
        provider_finalize_emission_ref=trusted.provider_emission_ref.model_dump(mode="json"),
        mapped_ref=trusted.mapped_ref.model_dump(mode="json"),
        review_scope=authorization["event_plan_scope"],
        plan_ids=list(plan_ids),
        decision_id=decision_id,
        snapshot_hash=snapshot_hash,
        publishing_request_id=publishing_request_id,
        publishing_payload_hash=publishing_payload_hash,
        published_at=aware_utc(at).isoformat(),
    )
    output_id = "eventpub_" + canonical_hash([trusted.run_id, trusted.owner_id, body["plan_ref"]])
    payload, digest = canonical(body), canonical_hash(body)
    old = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
    if old is not None:
        if (old.run_id, old.role, old.schema_version, old.payload_hash, old.payload_json) != (
            trusted.run_id,
            ROLE,
            SCHEMA,
            digest,
            payload,
        ):
            raise ValueError("event_publication_sidecar_conflict")
    else:
        session.add(
            RunRoleOutputRecord(
                output_id=output_id,
                run_id=trusted.run_id,
                role=ROLE,
                schema_version=SCHEMA,
                payload_hash=digest,
                payload_json=payload,
                created_at=at,
            )
        )
    return dict(ref=output_id, hash=digest)


def _load_original(session, run_id, reference):
    role = "perpetual_publication_original:" + canonical_hash(reference)
    row = session.get(
        RunRoleOutputRecord, "perp_pub_" + canonical_hash([run_id, role]), populate_existing=True
    )
    if row is None:
        raise ValueError("event_publication_original_missing")
    body = json.loads(row.payload_json)
    if (
        (row.run_id, row.role, row.payload_hash, row.schema_version)
        != (run_id, role, reference["hash"], body["schema_version"])
        or canonical(body) != row.payload_json
        or canonical_hash(body) != reference["hash"]
    ):
        raise ValueError("event_publication_original_mismatch")
    return body


def _read(session, row, *, owner_id, at):
    body = json.loads(row.payload_json)
    published_at = body["published_at"]
    # The native metadata writer uses datetime.isoformat(); financial target
    # clocks retain their existing strict Z contract and parser unchanged.
    if isinstance(published_at, str) and published_at.endswith("+00:00"):
        published_at = published_at[:-6] + "Z"
    publication_ref = dict(ref=row.output_id, hash=row.payload_hash)
    if (
        row.role != ROLE
        or row.schema_version != SCHEMA
        or body["schema_version"] != SCHEMA
        or body["owner_id"] != owner_id
        or body["origin_run_id"] != row.run_id
        or row.output_id != "eventpub_" + canonical_hash([row.run_id, owner_id, body["plan_ref"]])
        or canonical_hash(body) != row.payload_hash
        or canonical(body) != row.payload_json
        or as_utc(row.created_at) != _utc(published_at)
        or as_utc(row.created_at) > aware_utc(at)
    ):
        raise ValueError("event_publication_sidecar_binding_invalid")
    receipt = ActionRepository(session).get_command(PUBLISHER, body["publishing_request_id"])
    result = receipt.response if receipt else {}
    if (
        receipt is None
        or receipt.operation != "publish"
        or receipt.payload_hash != body["publishing_payload_hash"]
        or receipt.accepted_at != as_utc(row.created_at)
        or receipt.plan_id not in body["plan_ids"]
        or result.get("status") != "published"
        or result.get("execution") != "paper_only"
        or result.get("opportunity_id") != body["opportunity_id"]
        or result.get("decision_id") != body["decision_id"]
        or result.get("snapshot_hash") != body["snapshot_hash"]
        or result.get("event_scenario_publication_ref") != publication_ref
        or result.get("event_scenario_plan_ref") != body["plan_ref"]
        or [item["plan_id"] for item in result.get("branches", [])] != body["plan_ids"]
    ):
        raise ValueError("event_publication_command_binding_invalid")
    synthesis = _load_original(session, row.run_id, body["native_synthesis_ref"])
    request = _load_original(session, row.run_id, body["native_review_request_ref"])
    authorization = _load_original(session, row.run_id, body["review_authorization_ref"])
    validate_action_review_binding_v2(request, authorization)
    manifest = _load_original(session, row.run_id, body["manifest_ref"])
    expanded = unpack_event_scenario_manifest(manifest)
    plan = expanded["event_plan"]
    if (
        synthesis["event_plan_ref"] != body["plan_ref"]
        or request["synthesis"] != synthesis
        or request["synthesis_ref"] != body["native_synthesis_ref"]
        or authorization["review_request_ref"] != body["native_review_request_ref"]
        or synthesis["event_plan_manifest_ref"] != body["manifest_ref"]
        or synthesis["event_plan_draft_ref"] != body["event_plan_draft_ref"]
        or derived_ref("event_scenario_action_plan", plan) != body["plan_ref"]
        or derived_ref("event_scenario_action_plan_draft", expanded["normalized_event_plan_draft"])
        != body["event_plan_draft_ref"]
        or authorization["event_plan_scope"] != body["review_scope"]
        or authorization["verdict"] != "approved"
        or plan["context"]["owner_id"] != owner_id
        or synthesis["opportunity_id"] != body["opportunity_id"]
    ):
        raise ValueError("event_publication_plan_binding_invalid")
    originals = {}
    for original in expanded["declared_originals"]:
        value = json.loads(original["body_json"])
        if (
            canonical(value) != original["body_json"]
            or canonical_hash(value) != original["source_ref"]["hash"]
        ):
            raise ValueError("event_publication_declared_original_mismatch")
        originals[original["source_ref"]["ref"]] = value
    originals[plan["method_ref"]["ref"]] = _load_original(session, row.run_id, plan["method_ref"])
    return dict(
        plan=plan,
        plan_ref=body["plan_ref"],
        review_scope=body["review_scope"],
        review_authorization_ref=body["review_authorization_ref"],
        publication_ref=publication_ref,
        originals=originals,
        owner_id=owner_id,
        origin_run_id=row.run_id,
        opportunity_id=body["opportunity_id"],
        native_synthesis_ref=body["native_synthesis_ref"],
        native_review_request_ref=body["native_review_request_ref"],
        plan_ids=body["plan_ids"],
    )


def read_published_event_scenario_plan_in_session(session, *, run_id, owner_id, at):
    rows = session.scalars(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == ROLE,
            RunRoleOutputRecord.created_at <= aware_utc(at),
        )
    ).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("event_publication_run_sidecar_ambiguous")
    return _read(session, rows[0], owner_id=owner_id, at=at)


def read_latest_published_event_scenario_plan_in_session(
    session, *, owner_id, settings_ref, partition, horizon, at
):
    partition = partition.model_dump(mode="json") if hasattr(partition, "model_dump") else partition
    settings_ref = (
        settings_ref.model_dump(mode="json")
        if hasattr(settings_ref, "model_dump")
        else settings_ref
    )
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(RunRoleOutputRecord.role == ROLE, RunRoleOutputRecord.created_at <= aware_utc(at))
        .order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id.desc())
    ).all()
    for row in rows:
        body = json.loads(row.payload_json)
        if body.get("owner_id") != owner_id:
            continue
        bundle = _read(session, row, owner_id=owner_id, at=at)
        context = bundle["plan"]["context"]
        if (
            context["settings_ref"] == settings_ref
            and context["horizon"] == horizon
            and all(
                (context[key]["ref"] if key == "profile_ref" else context[key]) == value
                for key, value in partition.items()
            )
        ):
            return bundle
    return None
