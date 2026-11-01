"""Authenticate an actual intermediate native hypothesis and independently re-read usage."""

from __future__ import annotations

import re
from datetime import datetime

from packages.kernel.decision_hub_kernel.ports.research_signal import MechanismReview

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from . import research_skill_compiler as compiler
from .research_skill_market_input import read_market_input_in_session
from .research_skill_node_ledger import freeze_event, node_output_ref, read_envelope
from .research_skill_nodes import NodeMaterial
from .research_skill_occurrence import block_hash, read_occurrence
from .research_skill_package import decoded, encoded
from .research_skill_use import event_ref as event_reference


def node_response(event, output):
    return dict(
        schema_version="research-skill-node-result.v1",
        status="executed",
        node_output=node_output_ref(event),
        output=output,
    )


def hypothesis_delivery(output):
    # Delivered is not an executed/consumed receipt. The next actual ProviderInput
    # is needed to authenticate this tool call/result with no self-hash cycle.
    return dict(
        schema_version="research-skill-hypothesis-delivery.v1", status="delivered", output=output
    )


def _locus(value, pointer):
    if not re.fullmatch(r"/mechanism_review/(nodes|edges)/(0|[1-9][0-9]*)", pointer):
        raise ValueError("skill_consumption_locus_must_be_concrete_mechanism_node_or_edge")
    try:
        field, index = pointer.split("/")[2:]
        result = value["mechanism_review"][field][int(index)]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("skill_consumption_locus_missing") from exc
    return result


class SkillConsumptionService:
    def __init__(self, nodes):
        self.nodes, self.use, self.packages = nodes, nodes.use, nodes.packages

    def _previous_result(self, session, occurrence, proof, identity, call_id, tool_name, expected):
        messages = occurrence.transcript["messages"]
        calls = []
        results = []
        for mi, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            source = message.get("source", {})
            if (
                isinstance(source, dict)
                and source.get("kind") == "tool"
                and source.get("callId") == call_id
            ):
                results.append((mi, message))
            for bi, block in enumerate(
                message.get("content", []) if isinstance(message.get("content"), list) else []
            ):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool-call"
                    and block.get("id") == call_id
                ):
                    calls.append((mi, bi, block))
        if len(calls) != 1 or len(results) != 1 or results[0][0] >= occurrence.call_message_index:
            raise ValueError("skill_consumption_parent_not_in_actual_model_input")
        ci, bi, call = calls[0]
        ri, result = results[0]
        parent_proof = {
            **proof,
            "call_id": call_id,
            "tool_name": tool_name,
            "call_json_pointer": f"/messages/{ci}/content/{bi}",
            "result_json_pointer": f"/messages/{ri}",
            "call_hash": block_hash(call),
            "result_hash": block_hash(result),
        }
        actual = read_occurrence(
            session,
            parent_proof,
            run_id=identity["run_id"],
            generation=identity["generation"],
            root_session_id=identity["execution_session_id"],
            execution_session_id=identity["execution_session_id"],
            request_hash=identity["request_hash"],
            tool_name=tool_name,
            provider_reader=self.use.provider_reader,
            emission_reader=self.use.emission_reader,
        )
        if tool_name == "decision_hub_research":
            from packages.contracts_py.decision_hub_contracts import ResearchCapabilityResult

            matches = ResearchCapabilityResult.model_validate_json(
                encoded(actual.result)
            ) == ResearchCapabilityResult.model_validate_json(encoded(expected))
        else:
            matches = encoded(actual.result) == encoded(expected)
        if not matches:
            raise ValueError("skill_consumption_parent_delivery_changed")

    def _derive(self, session, intent):
        selection, execution, identity, plan = self.nodes._base(session, intent)
        gateway = self.nodes.read_gateway_in_session(
            session, run_id=intent["run_id"], event_ref=intent["gateway_event_ref"]
        )
        feature = self.nodes.read_component_in_session(
            session, run_id=intent["run_id"], event_ref=intent["feature_event_ref"]
        )
        if feature["details"]["parent_outputs"] != [node_output_ref(gateway)] or any(
            event["details"]["loaded_event"] != intent["loaded_event_ref"]
            for event in (gateway, feature)
        ):
            raise ValueError("skill_consumption_parent_identity_mismatch")
        _, gintent = read_envelope(
            session,
            self.packages.contracts,
            run_id=intent["run_id"],
            event_ref=intent["gateway_event_ref"],
            stage="executed",
        )
        _, fintent = read_envelope(
            session,
            self.packages.contracts,
            run_id=intent["run_id"],
            event_ref=intent["feature_event_ref"],
            stage="executed",
        )
        outputs = {
            event["node_id"]: self.nodes._read_value(
                event["details"]["output_ref"], event["details"]["output_hash"]
            )
            for event in (gateway, feature)
        }
        bound = compiler.bind_node_inputs(plan, "hypothesis", gintent["workflow_input"], outputs)
        proof = intent["occurrence_proof"]
        self.packages.contracts.check("tool_occurrence_proof_v1", proof)
        occurrence = read_occurrence(
            session,
            proof,
            run_id=intent["run_id"],
            generation=intent["generation"],
            root_session_id=identity["dsh_session_id"],
            execution_session_id=intent["execution_session_id"],
            request_hash=identity["request_hash"],
            tool_name="decision_hub_skill",
            provider_reader=self.use.provider_reader,
            emission_reader=self.use.emission_reader,
        )
        args = occurrence.arguments
        if (
            set(args) != {"operation", "skill_version_id", "node_id", "input"}
            or args["operation"] != "execute_step"
            or args["skill_version_id"] != selection["skill_version_id"]
            or args["node_id"] != "hypothesis"
            or not isinstance(args["input"], dict)
            or set(args["input"]) != {"output"}
        ):
            raise ValueError("skill_hypothesis_native_arguments_invalid")
        output = args["input"]["output"]
        self.packages.contracts.check("hypothesis_output_v1", output)
        compiler.validate_node_output(plan, "hypothesis", output)
        review = MechanismReview.model_validate_json(encoded(output["mechanism_review"]))
        parent_cutoff = aware_utc(
            datetime.fromisoformat(gintent["workflow_input"]["feature_input"]["as_of"])
        )
        cutoff = aware_utc(datetime.fromisoformat(output["as_of"]))
        if (
            output["scope_id"] != selection["scope_id"]
            or output["horizon"] != "24h"
            or review.scope_id != selection["scope_id"]
            or review.horizon != "24h"
            or review.run_id != intent["run_id"]
        ):
            raise ValueError("skill_hypothesis_run_scope_horizon_mismatch")
        if review.cutoff != cutoff:
            raise ValueError("skill_hypothesis_as_of_and_review_cutoff_must_match")
        call_at = aware_utc(occurrence.emission.known_at)
        completed = aware_utc(occurrence.provider_input.known_at)
        if (
            any(
                call_at <= datetime.fromisoformat(event["first_known_at"])
                for event in (gateway, feature)
            )
            or cutoff < parent_cutoff
            or cutoff > call_at
            or completed
            > aware_utc(self.nodes.deadline_reader(session, intent["run_id"], intent["generation"]))
        ):
            raise ValueError("skill_hypothesis_before_dependencies_or_after_task_upper_bound")
        if occurrence.result != hypothesis_delivery(output):
            raise ValueError("skill_hypothesis_delivery_mismatch")
        trusted_identity = {**intent, "request_hash": identity["request_hash"]}
        self._previous_result(
            session,
            occurrence,
            proof,
            trusted_identity,
            gintent["research_call_id"],
            "decision_hub_research",
            outputs["spot_evidence"],
        )
        self._previous_result(
            session,
            occurrence,
            proof,
            trusted_identity,
            fintent["call_id"],
            "decision_hub_skill",
            node_response(feature, outputs["features"]),
        )
        parent_refs = [node_output_ref(event) for event in (gateway, feature)]
        uses = output["used_outputs"]
        if len(uses) != 2 or sorted(encoded(use["node_output"]) for use in uses) != sorted(
            encoded(ref) for ref in parent_refs
        ):
            raise ValueError("skill_consumption_must_cover_exact_two_parent_outputs_once")
        for use in uses:
            target = _locus(output, use["consumer_json_pointer"])
            citations = []
            for name in ("refs", "support_refs", "counter_refs"):
                citations.extend(target.get(name, []))
            if use["node_output"]["output_ref"] not in citations:
                raise ValueError("skill_consumption_locus_missing_exact_output_citation")
        reasoning = dict(
            kind="dsh_reasoning",
            native_step_call=proof,
            provider_emission_ref=occurrence.emission_ref.model_dump(mode="json"),
            typed_result_hash=canonical_hash(output),
        )
        known = max(
            datetime.fromisoformat(event["details"]["market_inputs_known_through"])
            for event in (gateway, feature)
        )
        details = self.nodes._details(
            selection,
            plan,
            "hypothesis",
            intent["loaded_event_ref"],
            parent_refs,
            decoded(bound.input_json),
            output,
            reasoning,
            completed,
            known,
        )
        return selection, details, output

    def prepare(
        self,
        *,
        run_id,
        generation,
        execution_session_id,
        stage_request_id,
        loaded_event_ref,
        gateway_event_ref,
        feature_event_ref,
        occurrence_proof,
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
                    feature_event_ref=feature_event_ref,
                    occurrence_proof=occurrence_proof,
                )
            )
        )

        def prepare(session):
            selection, details, _ = self._derive(session, intent)
            replay = self.nodes._replay(
                session, selection, "hypothesis", intent, self.read_executed_in_session
            )
            now = (
                datetime.fromisoformat(replay["first_known_at"])
                if replay
                else aware_utc(self.use.clock())
            )
            if now < datetime.fromisoformat(details["completed_at"]):
                raise ValueError("skill_hypothesis_binding_before_actual_input")
            refs = list(self.nodes._archive_refs(session, selection, details))
            gateway = None
            for parent_ref in (intent["gateway_event_ref"], intent["feature_event_ref"]):
                parent, parent_intent = read_envelope(
                    session,
                    self.packages.contracts,
                    run_id=run_id,
                    event_ref=parent_ref,
                    stage="executed",
                )
                refs.extend(
                    (parent["details"][key + "_ref"], parent["details"][key + "_hash"])
                    for key in ("input", "output")
                )
                if parent["node_id"] == "spot_evidence":
                    gateway = parent_intent
            cutoff = datetime.fromisoformat(gateway["workflow_input"]["feature_input"]["as_of"])
            market = read_market_input_in_session(
                self.nodes.market, session, scope_id=selection["scope_id"], cutoff_at=cutoff
            )
            return NodeMaterial(
                encoded(intent),
                encoded(selection),
                encoded(details),
                tuple(sorted(set(refs))),
                market.originals,
                now,
                encoded(replay) if replay else None,
            )

        return self.use.selections.originals_preparer(self.packages.registry.database, prepare)

    def _consumed_details(self, executed, output):
        details = executed["details"]
        return dict(
            stage="consumed",
            loaded_event=details["loaded_event"],
            consumer_node_id="hypothesis",
            consumer_op="hypothesis.check",
            consumer_event=event_reference(executed),
            consumer_output_ref=details["output_ref"],
            consumer_output_hash=details["output_hash"],
            provider_emission_ref=details["proof"]["provider_emission_ref"],
            uses=output["used_outputs"],
            method_first_known_at=details["method_first_known_at"],
            market_inputs_known_through=details["market_inputs_known_through"],
        )

    def freeze_in_session(self, session, prepared):
        material, intent, selection = self.nodes._verify_material(session, prepared)
        details = decoded(material.details_json)
        executed = freeze_event(
            session,
            self.packages.contracts,
            selection=selection,
            execution_session_id=intent["execution_session_id"],
            stage="executed",
            node_id="hypothesis",
            stage_request_id=intent["stage_request_id"],
            parent_event=intent["loaded_event_ref"],
            details=details,
            intent=intent,
            known_at=material.known_at,
        )
        output = self.nodes._read_value(details["output_ref"], details["output_hash"])
        consumed = freeze_event(
            session,
            self.packages.contracts,
            selection=selection,
            execution_session_id=intent["execution_session_id"],
            stage="consumed",
            node_id="",
            stage_request_id=intent["stage_request_id"],
            parent_event=event_reference(executed),
            details=self._consumed_details(executed, output),
            intent={"consumer_event": event_reference(executed)},
            known_at=material.known_at,
        )
        prepared.verify_after_writes(session)
        return dict(executed=executed, consumed=consumed)

    def read_executed_in_session(self, session, *, run_id, event_ref):
        body, intent = read_envelope(
            session, self.packages.contracts, run_id=run_id, event_ref=event_ref, stage="executed"
        )
        for key in ("input", "output"):
            self.nodes._read_value(body["details"][key + "_ref"], body["details"][key + "_hash"])
        selection, details, _ = self._derive(session, intent)
        self.nodes._check(body, intent, selection, details, "hypothesis")
        return body

    def read_consumed_in_session(self, session, *, run_id, event_ref):
        body, intent = read_envelope(
            session, self.packages.contracts, run_id=run_id, event_ref=event_ref, stage="consumed"
        )
        if set(intent) != {"consumer_event"}:
            raise ValueError("skill_consumption_original_intent_invalid")
        executed = self.read_executed_in_session(
            session, run_id=run_id, event_ref=intent["consumer_event"]
        )
        output = self.nodes._read_value(
            executed["details"]["output_ref"], executed["details"]["output_hash"]
        )
        if (
            body["details"] != self._consumed_details(executed, output)
            or body["parent_event"] != event_reference(executed)
            or body["first_known_at"] != executed["first_known_at"]
            or body["stage_request_id"] != executed["stage_request_id"]
            or any(
                body[key] != executed[key]
                for key in (
                    "use_id",
                    "generation",
                    "root_session_id",
                    "execution_session_id",
                    "skill_version_id",
                    "package_hash",
                    "workflow_hash",
                    "method_binding_hash",
                )
            )
        ):
            raise ValueError("skill_consumption_original_changed")
        return body
