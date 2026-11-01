"""Finite three-node first-seed execution using existing originals and 0093 rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from packages.kernel.decision_hub_kernel.application.provider_input_receipts import strict_json
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ResearchSkillUseEventRecord,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchToolCallReservationRecord,
    as_utc,
)

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from . import research_skill_compiler as compiler
from .research_skill_gateway import _prepared_gateway, read_gateway_node_in_session
from .research_skill_market_input import read_market_input_in_session
from .research_skill_node_ledger import event_key, freeze_event, node_output_ref, read_envelope
from .research_skill_package import decoded, encoded, sha
from .research_skill_use import event_ref


@dataclass(frozen=True)
class NodeMaterial:
    intent_json: bytes
    selection_json: bytes
    details_json: bytes | None
    archive_refs: tuple[tuple[str, str], ...]
    market_refs: tuple[tuple[str, str], ...]
    known_at: datetime
    replay_json: bytes | None = None
    execution_json: bytes | None = None


class SkillNodeService:
    def __init__(
        self,
        use,
        market_store,
        component_consumer,
        *,
        gateway_prepared_reader=_prepared_gateway,
        deadline_reader=None,
    ):
        self.use, self.packages, self.market, self.component = (
            use,
            use.packages,
            market_store,
            component_consumer,
        )
        self.gateway_prepared_reader = gateway_prepared_reader
        self.deadline_reader = deadline_reader or self._deadline

    @staticmethod
    def _deadline(session, run_id, generation):
        return read_prepared_execution_in_session(
            session, run_id, generation
        ).execution.request.deadline_at

    def _base(self, session, intent):
        selection, execution, identity = self.use._selection(
            session, intent["run_id"], intent["generation"], intent["execution_session_id"]
        )
        raw = self.packages.archive.read(selection["compiled_plan_ref"])
        if sha(raw) != selection["compiled_plan_hash"]:
            raise ValueError("skill_node_plan_changed")
        return (
            selection,
            execution,
            identity,
            compiler.CompiledWorkflow(raw.decode(), selection["compiled_plan_hash"]),
        )

    def _archive(self, value):
        raw = encoded(value)
        digest = sha(raw)
        reference = "raw://sha256/" + digest
        try:
            self.packages.archive.read(reference)
        except FileNotFoundError:
            self.packages.archive.put(raw)
        return reference, digest

    def _read_value(self, reference, digest):
        raw = self.packages.archive.read(reference)
        if sha(raw) != digest:
            raise ValueError("skill_node_archived_value_changed")
        return decoded(raw)

    def _archive_refs(self, session, selection, details):
        refs = list(self.use.selections._archive_refs(session, selection))
        if details:
            refs.extend(
                (details[key + "_ref"], details[key + "_hash"]) for key in ("input", "output")
            )
        return tuple(sorted(set(refs)))

    def _details(
        self,
        selection,
        plan,
        node_id,
        loaded,
        parents,
        input_value,
        output_value,
        proof,
        completed,
        known,
    ):
        node = next(row for row in plan.read()["workflow"]["nodes"] if row["id"] == node_id)
        compiler.validate_node_output(plan, node_id, output_value)
        input_ref, input_hash = self._archive(input_value)
        output_ref, output_hash = self._archive(output_value)
        details = dict(
            stage="executed",
            loaded_event=loaded,
            op=node["op"],
            parent_outputs=parents,
            input_contract=node["input_schema"],
            input_ref=input_ref,
            input_hash=input_hash,
            output_contract=node["output_schema"],
            output_ref=output_ref,
            output_hash=output_hash,
            proof=proof,
            method_first_known_at=selection["method_first_known_at"],
            market_inputs_known_through=known.isoformat(),
            completed_at=completed.isoformat(),
        )
        self.packages.contracts.check("executed_details_v1", details)
        return details

    def _gateway(self, session, intent):
        if intent.get("source_mode") == "frozen_original":
            from .research_skill_original_node import derive_original_node
            return derive_original_node(self, session, intent)
        selection, execution, identity, plan = self._base(session, intent)
        reservation = session.get(
            ResearchToolCallReservationRecord,
            (intent["run_id"], intent["research_call_id"]),
            populate_existing=True,
        )
        if reservation is None or as_utc(reservation.completed_at) is None:
            raise ValueError("skill_gateway_completed_reservation_required")
        cutoff = as_utc(reservation.completed_at)
        if intent.get("binder_call_id") is not None:
            pair = self.use.emission_reader(
                session,
                run_id=intent["run_id"],
                generation=intent["generation"],
                root_session_id=identity["dsh_session_id"],
                execution_session_id=intent["execution_session_id"],
                call_id=intent["binder_call_id"],
            )
            expected = dict(
                operation="execute_step",
                skill_version_id=selection["skill_version_id"],
                node_id="spot_evidence",
                input={"research_call_id": intent["research_call_id"]},
            )
            if pair is None:
                raise ValueError("skill_gateway_binder_emission_required")
            emission, _ = pair
            if (
                emission.submit.tool_name != "decision_hub_skill"
                or strict_json(emission.submit.raw_arguments_json) != expected
                or aware_utc(emission.known_at) <= as_utc(reservation.completed_at)
            ):
                raise ValueError("skill_gateway_binder_emission_invalid")
        if cutoff > aware_utc(
            self.deadline_reader(session, intent["run_id"], intent["generation"])
        ):
            raise ValueError("skill_gateway_after_frozen_task_upper_bound")
        market = read_market_input_in_session(
            self.market, session, scope_id=selection["scope_id"], cutoff_at=cutoff
        )
        workflow_input = decoded(market.workflow_input_json)
        original = read_gateway_node_in_session(
            self.use,
            session,
            run_id=intent["run_id"],
            generation=intent["generation"],
            execution_session_id=intent["execution_session_id"],
            loaded_event_ref=intent["loaded_event_ref"],
            node_id="spot_evidence",
            workflow_input=workflow_input,
            requested_call_id=intent["research_call_id"],
            occurrence_proof=intent["occurrence_proof"],
            prepared_reader=self.gateway_prepared_reader,
        )
        details = self._details(
            selection,
            plan,
            "spot_evidence",
            intent["loaded_event_ref"],
            [],
            decoded(original.input_json),
            decoded(original.output_json),
            decoded(original.proof_json),
            original.completed_at,
            max(original.inputs_known_through, market.known_through),
        )
        enriched = {
            **intent,
            "workflow_input": workflow_input,
            "attempts": decoded(original.attempts_json),
        }
        return selection, details, enriched, market.originals

    def prepare_original(self, *, run_id, generation, execution_session_id,
                         stage_request_id, loaded_event_ref, call_id):
        arguments = decoded(encoded(dict(run_id=run_id, generation=generation, execution_session_id=execution_session_id, stage_request_id=stage_request_id, loaded_event_ref=loaded_event_ref, call_id=call_id)))
        return self.use.selections.originals_preparer(
            self.packages.registry.database,
            lambda session: self.prepare_original_in_session(session, **arguments),
        )

    def prepare_original_in_session(self, session, *, run_id, generation, execution_session_id,
                         stage_request_id, loaded_event_ref, call_id):
        intent = dict(run_id=run_id, generation=generation, execution_session_id=execution_session_id,
            stage_request_id=stage_request_id, loaded_event_ref=loaded_event_ref,
            source_mode="frozen_original", call_id=call_id)
        from .research_skill_node_ledger import read_envelope
        selection, _, _, _ = self._base(session, intent)
        row = session.get(ResearchSkillUseEventRecord,
            "skill_evt_" + event_key(selection["use_id"], "executed", "spot_evidence"), populate_existing=True)
        if row is not None:
            _, saved = read_envelope(session, self.packages.contracts, run_id=run_id,
                event_ref={"event_id": row.event_id, "payload_hash": row.payload_hash}, stage="executed")
            if any(saved.get(key) != value for key, value in intent.items()):
                raise ValueError("skill_node_stage_key_conflict")
            intent["read_at"] = saved["read_at"]
        else:
            intent["read_at"] = aware_utc(self.use.clock()).isoformat()
        selected, details, enriched, market = self._gateway(session, intent)
        replay = self._replay(session, selected, "spot_evidence", enriched, self.read_gateway_in_session)
        return NodeMaterial(encoded(enriched), encoded(selected), encoded(details),
            self._archive_refs(session, selected, details), market,
            datetime.fromisoformat(intent["read_at"]), encoded(replay) if replay else None)

    def prepare_gateway(
        self,
        *,
        run_id,
        generation,
        execution_session_id,
        stage_request_id,
        loaded_event_ref,
        research_call_id,
        occurrence_proof,
        binder_call_id=None,
    ):
        arguments = decoded(encoded(dict(run_id=run_id, generation=generation, execution_session_id=execution_session_id, stage_request_id=stage_request_id, loaded_event_ref=loaded_event_ref, research_call_id=research_call_id, occurrence_proof=occurrence_proof, binder_call_id=binder_call_id)))
        return self.use.selections.originals_preparer(
            self.packages.registry.database,
            lambda session: self.prepare_gateway_in_session(session, **arguments),
        )

    def prepare_gateway_in_session(
        self, session,
        *,
        run_id,
        generation,
        execution_session_id,
        stage_request_id,
        loaded_event_ref,
        research_call_id,
        occurrence_proof,
        binder_call_id=None,
    ):
        intent = decoded(
            encoded(
                dict(
                    run_id=run_id,
                    generation=generation,
                    execution_session_id=execution_session_id,
                    stage_request_id=stage_request_id,
                    loaded_event_ref=loaded_event_ref,
                    research_call_id=research_call_id,
                    occurrence_proof=occurrence_proof,
                )
            )
        )

        if binder_call_id is not None:
            intent["binder_call_id"] = binder_call_id

        selection, details, enriched, market = self._gateway(session, intent)
        replay = self._replay(
            session, selection, "spot_evidence", enriched, self.read_gateway_in_session
        )
        now = (
            datetime.fromisoformat(replay["first_known_at"])
            if replay
            else aware_utc(self.use.clock())
        )
        if now < datetime.fromisoformat(details["completed_at"]):
            raise ValueError("skill_gateway_binding_before_execution")
        return NodeMaterial(
            encoded(enriched),
            encoded(selection),
            encoded(details),
            self._archive_refs(session, selection, details),
            market,
            now,
            encoded(replay) if replay else None,
        )

    def _replay(self, session, selection, node_id, intent, reader):
        row = session.get(
            ResearchSkillUseEventRecord,
            "skill_evt_" + event_key(selection["use_id"], "executed", node_id),
            populate_existing=True,
        )
        if row is None:
            return None
        body, old = read_envelope(
            session,
            self.packages.contracts,
            run_id=selection["run_id"],
            event_ref={"event_id": row.event_id, "payload_hash": row.payload_hash},
            stage="executed",
        )
        if old != intent:
            raise ValueError("skill_node_stage_key_conflict")
        return reader(session, run_id=selection["run_id"], event_ref=event_ref(body))

    def _verify_material(self, session, prepared):
        prepared.verify(session)
        material = prepared.value
        self.packages.installed.verify()
        for reference, digest in material.archive_refs:
            if sha(self.packages.archive.read(reference)) != digest:
                raise ValueError("skill_node_archive_changed")
        for reference, digest in material.market_refs:
            if sha(self.market.archive.read(reference)) != digest:
                raise ValueError("skill_market_original_changed")
        return material, decoded(material.intent_json), decoded(material.selection_json)

    def freeze_gateway_in_session(self, session, prepared):
        material, intent, selection = self._verify_material(session, prepared)
        if intent.get("source_mode") == "frozen_original" and material.replay_json is None:
            if aware_utc(self.use.clock()) >= datetime.fromisoformat(intent["original_deadline_at"]):
                raise ValueError("skill_original_read_deadline_elapsed")
        body = freeze_event(
            session,
            self.packages.contracts,
            selection=selection,
            execution_session_id=intent["execution_session_id"],
            stage="executed",
            node_id="spot_evidence",
            stage_request_id=intent["stage_request_id"],
            parent_event=intent["loaded_event_ref"],
            details=decoded(material.details_json),
            intent=intent,
            known_at=material.known_at,
        )
        prepared.verify_after_writes(session)
        return body

    def _check(self, body, intent, selection, details, node_id):
        if (
            body["details"] != details
            or body["node_id"] != node_id
            or body["parent_event"] != intent["loaded_event_ref"]
            or body["stage_request_id"] != intent["stage_request_id"]
            or body["run_id"] != intent["run_id"]
            or body["generation"] != intent["generation"]
            or body["execution_session_id"] != intent["execution_session_id"]
            or body["root_session_id"] != intent["execution_session_id"]
            or any(
                body[key] != selection[key]
                for key in (
                    "use_id",
                    "skill_version_id",
                    "package_hash",
                    "workflow_hash",
                    "method_binding_hash",
                )
            )
            or datetime.fromisoformat(body["first_known_at"])
            < datetime.fromisoformat(details["completed_at"])
        ):
            raise ValueError("skill_node_original_changed")

    def read_gateway_in_session(self, session, *, run_id, event_ref):
        body, intent = read_envelope(
            session, self.packages.contracts, run_id=run_id, event_ref=event_ref, stage="executed"
        )
        for key in ("input", "output"):
            self._read_value(body["details"][key + "_ref"], body["details"][key + "_hash"])
        plain = {
            key: value for key, value in intent.items() if key not in {"workflow_input", "attempts"}
        }
        selection, details, enriched, _ = self._gateway(session, plain)
        if enriched != intent:
            raise ValueError("skill_gateway_input_or_attempts_changed")
        self._check(body, intent, selection, details, "spot_evidence")
        return body

    def _component(self, session, intent, *, audit_ref=None, known_at=None):
        selection, execution, identity, plan = self._base(session, intent)
        parent = self.read_gateway_in_session(
            session, run_id=intent["run_id"], event_ref=intent["gateway_event_ref"]
        )
        if parent["details"]["loaded_event"] != intent["loaded_event_ref"]:
            raise ValueError("skill_component_loaded_identity_mismatch")
        _, parent_intent = read_envelope(
            session,
            self.packages.contracts,
            run_id=intent["run_id"],
            event_ref=intent["gateway_event_ref"],
            stage="executed",
        )
        output = self._read_value(parent["details"]["output_ref"], parent["details"]["output_hash"])
        bound = compiler.bind_node_inputs(
            plan, "features", parent_intent["workflow_input"], {"spot_evidence": output}
        )
        value = decoded(bound.input_json)
        pair = self.use.emission_reader(
            session,
            run_id=intent["run_id"],
            generation=intent["generation"],
            root_session_id=identity["dsh_session_id"],
            execution_session_id=intent["execution_session_id"],
            call_id=intent["call_id"],
        )
        expected = dict(
            operation="execute_step",
            skill_version_id=selection["skill_version_id"],
            node_id="features",
            input={},
        )
        if pair is None:
            raise ValueError("skill_component_native_request_missing")
        emission, reference = pair
        if (
            emission.submit.tool_name != "decision_hub_skill"
            or strict_json(emission.submit.raw_arguments_json) != expected
            or aware_utc(emission.known_at) <= datetime.fromisoformat(parent["first_known_at"])
        ):
            raise ValueError("skill_component_native_request_invalid")
        manifest = plan.read()["dependency_manifest"]["nodes"]["features"]["component_manifest"]
        ref = {"version_id": manifest["version_id"], "payload_hash": canonical_hash(manifest)}
        # ComponentManifest excludes its computed payload_hash field on serialization.
        from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest

        ref["payload_hash"] = ComponentManifest.model_validate_json(encoded(manifest)).payload_hash
        at = aware_utc(known_at or self.use.clock())
        if at < aware_utc(emission.known_at) or at > aware_utc(
            self.deadline_reader(session, intent["run_id"], intent["generation"])
        ):
            raise ValueError("skill_component_execution_clock_invalid")
        args = dict(
            manifest_ref=ref,
            scope_id=selection["scope_id"],
            request_id=selection["use_id"] + ":features",
            known_at=at,
            cutoff_at=datetime.fromisoformat(value["as_of"]),
            payload=value,
        )
        if audit_ref is None:
            return selection, parent, bound, ref, args, reference, plan
        audit = self.component.read_in_session(session, audit_ref=audit_ref, **args)
        proof = dict(
            kind="component",
            component_manifest_ref=ref,
            component_audit_ref=audit_ref,
            component_manifest_hash=ref["payload_hash"],
        )
        details = self._details(
            selection,
            plan,
            "features",
            intent["loaded_event_ref"],
            [node_output_ref(parent)],
            value,
            audit.output,
            proof,
            at,
            datetime.fromisoformat(parent["details"]["market_inputs_known_through"]),
        )
        return selection, details

    def prepare_component(
        self,
        *,
        run_id,
        generation,
        execution_session_id,
        stage_request_id,
        loaded_event_ref,
        gateway_event_ref,
        call_id,
    ):
        arguments = decoded(encoded(dict(run_id=run_id, generation=generation, execution_session_id=execution_session_id, stage_request_id=stage_request_id, loaded_event_ref=loaded_event_ref, gateway_event_ref=gateway_event_ref, call_id=call_id)))
        return self.use.selections.originals_preparer(
            self.packages.registry.database,
            lambda session: self.prepare_component_in_session(session, **arguments),
        )

    def prepare_component_in_session(
        self, session,
        *,
        run_id,
        generation,
        execution_session_id,
        stage_request_id,
        loaded_event_ref,
        gateway_event_ref,
        call_id,
    ):
        intent = decoded(
            encoded(
                dict(
                    run_id=run_id,
                    generation=generation,
                    execution_session_id=execution_session_id,
                    stage_request_id=stage_request_id,
                    loaded_event_ref=loaded_event_ref,
                    gateway_event_ref=gateway_event_ref,
                    call_id=call_id,
                )
            )
        )

        selection, _, _ = self.use._selection(session, run_id, generation, execution_session_id)
        replay = self._replay(
            session, selection, "features", intent, self.read_component_in_session
        )
        now = (
            datetime.fromisoformat(replay["first_known_at"])
            if replay
            else aware_utc(self.use.clock())
        )
        values = self._component(session, intent, known_at=now)
        # Exact component authentication/execution occurs in the short writer.
        # Parent is independently re-read above; its raw path bytes are frozen.
        gateway, old = read_envelope(
            session,
            self.packages.contracts,
            run_id=run_id,
            event_ref=gateway_event_ref,
            stage="executed",
        )
        cutoff = datetime.fromisoformat(old["workflow_input"]["feature_input"]["as_of"])
        if old.get("source_mode") == "frozen_original":
            market_refs = tuple(tuple(pair) for pair in old["original_market_refs"])
        else:
            market_refs = read_market_input_in_session(
                self.market, session, scope_id=selection["scope_id"], cutoff_at=cutoff
            ).originals
        _, parent, bound, ref, args, _, plan = values
        execution = dict(
            parent=parent,
            bound_input=decoded(bound.input_json),
            manifest_ref=ref,
            component_args={
                **args,
                "known_at": args["known_at"].isoformat(),
                "cutoff_at": args["cutoff_at"].isoformat(),
            },
        )
        return NodeMaterial(
            encoded(intent),
            encoded(selection),
            None,
            self._archive_refs(session, selection, gateway["details"]),
            market_refs,
            now,
            encoded(replay) if replay else None,
            encoded(execution),
        )

    def freeze_component_in_session(self, session, prepared):
        material, intent, selection = self._verify_material(session, prepared)
        if material.replay_json is not None:
            body = decoded(material.replay_json)
            prepared.verify_after_writes(session)
            return body
        # No model/network operation: bounded real deterministic callback and its
        # exact existing component audit share this node's transaction.
        execution = decoded(material.execution_json)
        parent, ref = execution["parent"], execution["manifest_ref"]
        args = execution["component_args"]
        args["known_at"], args["cutoff_at"] = (
            datetime.fromisoformat(args["known_at"]),
            datetime.fromisoformat(args["cutoff_at"]),
        )
        raw = self.packages.archive.read(selection["compiled_plan_ref"])
        plan = compiler.CompiledWorkflow(raw.decode(), selection["compiled_plan_hash"])
        value, audit_ref = self.component.execute_in_session(session, **args)
        proof = dict(
            kind="component",
            component_manifest_ref=ref,
            component_audit_ref=audit_ref,
            component_manifest_hash=ref["payload_hash"],
        )
        details = self._details(
            selection,
            plan,
            "features",
            intent["loaded_event_ref"],
            [node_output_ref(parent)],
            execution["bound_input"],
            value,
            proof,
            material.known_at,
            datetime.fromisoformat(parent["details"]["market_inputs_known_through"]),
        )
        body = freeze_event(
            session,
            self.packages.contracts,
            selection=selection,
            execution_session_id=intent["execution_session_id"],
            stage="executed",
            node_id="features",
            stage_request_id=intent["stage_request_id"],
            parent_event=intent["loaded_event_ref"],
            details=details,
            intent=intent,
            known_at=material.known_at,
        )
        prepared.verify_after_writes(session)
        return body

    def read_component_in_session(self, session, *, run_id, event_ref):
        body, intent = read_envelope(
            session, self.packages.contracts, run_id=run_id, event_ref=event_ref, stage="executed"
        )
        for key in ("input", "output"):
            self._read_value(body["details"][key + "_ref"], body["details"][key + "_hash"])
        selection, details = self._component(
            session,
            intent,
            audit_ref=body["details"]["proof"]["component_audit_ref"],
            known_at=datetime.fromisoformat(body["first_known_at"]),
        )
        self._check(body, intent, selection, details, "features")
        return body
