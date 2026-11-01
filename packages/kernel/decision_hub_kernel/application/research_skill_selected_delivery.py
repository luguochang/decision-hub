"""Exact selected-package delivery; a recorded input is not execution evidence."""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputRef,
)

from ..persistence.action_records import ResearchSkillUseEventRecord
from ..persistence.callback_source_lease import callback_source_lease_scope
from ..persistence.db import DshSessionLinkRecord, RunRecord, as_utc, utcnow
from ..ports.research_observation import ObservedExecutionRef
from .research_skill_package import decoded, encoded, sha

MODE = "selected_package_provider_input.v1"
PROOF = "selected_skill_provider_input.v1"


def record_contract(body):
    return (
        "record_request_v2"
        if body.get("schema_version") == "research-skill-record-request.v2"
        else "record_request_v1"
    )


def selected_stage_key(identity, proof):
    return "skill_selected_" + sha(encoded([identity, proof["selection_ref"], proof["input_ref"]]))


def read_delivery_in_session(
    use, session, *, run_id, generation, root_session_id, archive_readset=None
):
    selection, execution, identity = use._selection(session, run_id, generation, root_session_id)
    row = (
        use.selections._row(session, run_id)
        if generation == 1
        else use.bindings._row(session, run_id, generation)
    )
    event = session.get(
        ResearchSkillUseEventRecord, selection["use_id"] + "_selected", populate_existing=True
    )
    # _selection has fully authenticated both these exact rows, package and plan.
    if row is None or event is None:
        raise ValueError("skill_selected_original_missing")
    if archive_readset is not None:
        archive_readset.extend(use.selections._archive_refs(session, selection))
    common = dict(
        run_id=run_id,
        generation=generation,
        request_hash=identity["request_hash"],
        root_session_id=root_session_id,
        selection_ref=dict(
            output_id=row.output_id,
            role=row.role,
            schema_version=row.schema_version,
            payload_hash=row.payload_hash,
            recorded_at=as_utc(row.created_at).isoformat(),
        ),
        selected_event_ref=dict(event_id=event.event_id, payload_hash=event.payload_hash),
    )
    if selection.get("delivery_mode") != MODE:
        value = dict(
            schema_version="research-selected-skill-delivery-mode.v1",
            delivery_mode="legacy-tool",
            **common,
        )
        use.packages.contracts.check("selected_delivery_legacy_v1", value)
        return value
    value = dict(
        schema_version="research-selected-skill-delivery.v1",
        delivery_mode=MODE,
        **common,
        task_request_hash=execution.task_request_hash,
        method_binding_hash=execution.method_hash,
        delivery=use.load_in_session(
            session,
            run_id=run_id,
            generation=generation,
            execution_session_id=root_session_id,
            skill_version_id=selection["skill_version_id"],
        ),
    )
    use.packages.contracts.check("selected_delivery_v1", value)
    return value


def prove_selected_input(use, session, intent):
    proof = intent["proof"]
    use.packages.contracts.check("selected_provider_input_v1", proof)
    expected = use.read_selected_delivery_in_session(
        session,
        run_id=intent["run_id"],
        generation=intent["generation"],
        root_session_id=intent["execution_session_id"],
    )
    if expected["delivery_mode"] != MODE:
        raise ValueError("skill_selected_delivery_not_enabled")
    reference = ObservedExecutionRef.model_validate_json(encoded(proof["input_ref"]))
    original, actual_ref = use.provider_reader(
        session,
        ResearchProviderInputRef(
            output_id=reference.output_id, payload_hash=reference.payload_hash
        ),
    )
    submit = original.submit
    if (
        actual_ref != reference
        or submit.run_id != expected["run_id"]
        or submit.generation != expected["generation"]
        or submit.root_session_id != expected["root_session_id"]
        or submit.execution_session_id != submit.root_session_id
        or submit.parent_session_id is not None
        or submit.request_hash != expected["request_hash"]
        or submit.dispatch_id != proof["dispatch_id"]
        or original.known_at < datetime.fromisoformat(expected["selection_ref"]["recorded_at"])
    ):
        raise ValueError("skill_selected_input_identity_invalid")
    if (
        proof["selection_ref"] != expected["selection_ref"]
        or proof["selected_event_ref"] != expected["selected_event_ref"]
    ):
        raise ValueError("skill_selected_reference_changed")
    identity = dict(
        run_id=submit.run_id,
        generation=submit.generation,
        root_session_id=submit.root_session_id,
        execution_session_id=submit.execution_session_id,
        request_hash=submit.request_hash,
    )
    if intent["stage_request_id"] != selected_stage_key(identity, proof):
        raise ValueError("skill_selected_stage_key_invalid")
    wire, digest = encoded(expected).decode(), sha(encoded(expected))
    load = expected["delivery"]
    if (
        proof["envelope_hash"] != digest
        or proof["method_body_hash"] != load["method_body_hash"]
        or proof["delivered_package_hash"] != load["package_hash"]
    ):
        raise ValueError("skill_selected_delivery_hash_invalid")
    match = re.fullmatch(r"/messages/(0|[1-9][0-9]*)", proof["message_json_pointer"])
    if (
        match is None
        or proof["body_json_pointer"] != proof["message_json_pointer"] + "/content/0/text"
    ):
        raise ValueError("skill_selected_input_locus_invalid")
    body = decoded(submit.input_json)
    messages = body.get("messages", [])
    expected_message = dict(
        id="selected_skill_" + digest,
        role="user",
        source=dict(kind="plugin", plugin="decision-hub-selected-skill"),
        content=[dict(type="text", text=wire)],
    )
    index = int(match.group(1))
    if index >= len(messages) or messages[index] != expected_message:
        raise ValueError("skill_selected_input_message_invalid")
    occurrences = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and block.get("text") == wire
            ):
                occurrences += 1
        if message.get("source") == expected_message["source"] and message != expected_message:
            raise ValueError("skill_selected_input_duplicate_or_changed")
    if occurrences != 1:
        raise ValueError("skill_selected_input_duplicate_or_changed")
    details = dict(
        schema_version="research-skill-loaded-details.v2",
        stage="loaded",
        selected_event=expected["selected_event_ref"],
        selected_input=proof,
        provider_input_ref=proof["input_ref"],
        dispatch_id=submit.dispatch_id,
        method_body_hash=load["method_body_hash"],
        delivered_package_hash=load["package_hash"],
        provider_input_method_loci=[proof["body_json_pointer"]],
        provider_input_result_loci=[proof["message_json_pointer"]],
    )
    use.packages.contracts.check("loaded_details_v2", details)
    return details, original


def read_owned_selected_delivery(
    service, *, run_id, generation, root_session_id, request_hash, checkpoint
):
    """Same owned thread, issued readset, then independent current permission cut."""
    database, use = service.database, service.use

    def permission(session, owner=None):
        run = session.get(RunRecord, run_id, populate_existing=True)
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        now = utcnow()
        if (
            run is None
            or link is None
            or run.status != "running"
            or run.artifact_id is not None
            or run.finished_at is not None
            or not run.lease_owner
            or owner is not None
            and run.lease_owner != owner
            or run.lease_expires_at is None
            or as_utc(run.lease_expires_at) <= now
            or link.generation != generation
            or link.dsh_session_id != root_session_id
            or link.request_hash != request_hash
            or link.state not in {"admitted", "running", "idle"}
            or link.terminal_at is not None
            or link.deadline_at is None
            or as_utc(link.deadline_at) <= now
        ):
            raise ValueError("skill_selected_current_permission_invalid")
        return run.lease_owner, (
            run.event_id,
            run.snapshot_id,
            link.plugin_build_hash,
            as_utc(link.deadline_at),
        )

    with callback_source_lease_scope(checkpoint) as source_lease:

        def read(session):
            owner, identity = permission(session)
            archive_readset = []
            value = read_delivery_in_session(
                use,
                session,
                run_id=run_id,
                generation=generation,
                root_session_id=root_session_id,
                archive_readset=archive_readset,
            )
            if value["request_hash"] != request_hash:
                raise ValueError("skill_selected_request_changed")
            source_lease.extend_authenticated_selection(session, run_id)
            return value, owner, identity, tuple(archive_readset)

        prepared = use.selections.originals_preparer(
            database, read, checkpoint=checkpoint, source_lease=source_lease
        )
        value, owner, identity, archive_readset = prepared.value
        with database.engine.connect() as connection, Session(bind=connection) as session:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            try:
                connection.exec_driver_sql("BEGIN")
                prepared.verify_read_snapshot(session, checkpoint=checkpoint)
                for reference, digest in archive_readset:
                    checkpoint()
                    if sha(use.packages.archive.read(reference)) != digest:
                        raise ValueError("skill_selected_archive_changed")
                session.rollback()
                connection.exec_driver_sql("BEGIN")
                if permission(session, owner) != (owner, identity):
                    raise ValueError("skill_selected_current_identity_changed")
                checkpoint()
                return value
            finally:
                session.rollback()
                connection.exec_driver_sql("PRAGMA query_only=OFF")
