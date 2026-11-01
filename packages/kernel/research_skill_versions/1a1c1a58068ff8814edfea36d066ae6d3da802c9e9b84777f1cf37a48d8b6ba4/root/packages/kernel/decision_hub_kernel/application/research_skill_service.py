"""Finite normal tool entry points; every authority is re-read from originals."""

from __future__ import annotations

from contextlib import contextmanager

from ..persistence.original_preparation import (
    _current_shape_read_scope,
    begin_immediate,
    prepare_originals,
)
from ..ports.market_path import canonical_hash
from .research_recipe_read_scope import recipe_original_read_scope
from .research_skill_consumption import hypothesis_delivery, node_response
from .research_skill_occurrence import read_occurrence
from .research_skill_package import decoded, encoded
from .research_skill_workflow import SkillWorkflowService


@contextmanager
def skill_original_read_scope(session):
    """Reuse only existing guarded readers inside this physical RO snapshot.

    No execution/permission result is cached. Recipe originals are independently
    reauthenticated on exit; shape reuse is pure. Neither scope reaches a writer.
    """
    with _current_shape_read_scope(), recipe_original_read_scope(session):
        yield


def prepare_skill_originals(database, operation, **kwargs):
    """Keep the original SQL/source readset and writer fences unchanged."""
    def read(session):
        with skill_original_read_scope(session):
            return operation(session)

    return prepare_originals(database, read, **kwargs)


class ResearchSkillService:
    def __init__(self, consumption):
        self.consumption = consumption
        self.nodes, self.use = consumption.nodes, consumption.use
        self.packages = self.use.packages
        self.database = self.packages.registry.database
        self.workflow = SkillWorkflowService(consumption)

    def _identity(self, session, identity):
        self.packages.contracts.check("host_identity_v1", identity)
        selection, execution, actual = self.use._selection(
            session, identity["run_id"], identity["generation"], identity["execution_session_id"]
        )
        if (
            identity["root_session_id"] != actual["dsh_session_id"]
            or identity["execution_session_id"] != identity["root_session_id"]
            or identity["request_hash"] != actual["request_hash"]
        ):
            raise ValueError("skill_host_frozen_identity_mismatch")
        return selection, execution, actual

    def _tool_original(self, session, body):
        identity = body["identity"]
        selection, _, _ = self._identity(session, identity)
        pair = self.use.emission_reader(
            session,
            run_id=identity["run_id"],
            generation=identity["generation"],
            root_session_id=identity["root_session_id"],
            execution_session_id=identity["execution_session_id"],
            call_id=body["call_id"],
        )
        if pair is None:
            raise ValueError("skill_native_request_missing")
        emission, _ = pair
        if (
            emission.submit.tool_name != "decision_hub_skill"
            or decoded(emission.submit.raw_arguments_json) != body["arguments"]
            or emission.submit.request_hash != identity["request_hash"]
        ):
            raise ValueError("skill_native_request_invalid")
        args = body["arguments"]
        if (
            args["operation"] != "list"
            and args["skill_version_id"] != selection["skill_version_id"]
        ):
            raise ValueError("skill_native_version_mismatch")
        return selection

    @staticmethod
    def _common(identity):
        return {key: identity[key] for key in ("run_id", "generation", "execution_session_id")}

    def _stages(self, session, identity):
        view = self.workflow.list_in_session(session, **self._common(identity))
        return {
            (item["stage"], item["node_id"]): item["event_ref"] for item in view["stage_receipts"]
        }

    @staticmethod
    def _required(stages, stage, node=""):
        try:
            return stages[stage, node]
        except KeyError as error:
            raise ValueError("skill_required_prior_stage_missing:" + stage + ":" + node) from error

    def _write(self, prepared, freezer, request_check):
        request_check()
        with self.database.session() as session:
            begin_immediate(session)
            request_check()
            result = freezer(session, prepared)
            request_check()
            return result

    def tool(self, body, *, request_check=lambda: None):
        """Host callback. Model arguments cannot supply a verified flag or scope."""
        self.packages.contracts.check("tool_request_v1", body)
        identity, args = body["identity"], body["arguments"]
        request_check()
        with self.database.session() as session:
            # Provider originals require one physical snapshot. SQLite SELECT
            # alone does not begin it; keep this a deferred read transaction.
            session.connection().exec_driver_sql("BEGIN")
            with skill_original_read_scope(session):
                self._tool_original(session, body)
                if args["operation"] == "list":
                    return self.workflow.list_in_session(session, **self._common(identity))
                if args["operation"] == "load":
                    return self.use.load_in_session(
                        session, **self._common(identity), skill_version_id=args["skill_version_id"]
                    )
                stages = self._stages(session, identity)
                loaded = self._required(stages, "loaded")
        common = self._common(identity)
        key = "skill_step_" + canonical_hash([identity, body["call_id"]])
        node, value = args["node_id"], args["input"]
        if node == "spot_evidence":
            if set(value) != {"research_call_id"} or not isinstance(value["research_call_id"], str):
                raise ValueError("skill_gateway_binding_input_invalid")
            proof = body["research_occurrence"]
            if proof is None or proof["call_id"] != value["research_call_id"]:
                raise ValueError("skill_gateway_occurrence_required")
            prepared = self.nodes.prepare_gateway(
                **common,
                stage_request_id=key,
                loaded_event_ref=loaded,
                research_call_id=value["research_call_id"],
                occurrence_proof=proof,
                binder_call_id=body["call_id"],
            )
            event = self._write(prepared, self.nodes.freeze_gateway_in_session, request_check)
        elif node == "features":
            if value or body["research_occurrence"] is not None:
                raise ValueError("skill_component_input_must_be_empty")
            prepared = self.nodes.prepare_component(
                **common,
                stage_request_id=key,
                loaded_event_ref=loaded,
                gateway_event_ref=self._required(stages, "executed", "spot_evidence"),
                call_id=body["call_id"],
            )
            event = self._write(prepared, self.nodes.freeze_component_in_session, request_check)
        elif node == "hypothesis":
            if set(value) != {"output"} or body["research_occurrence"] is not None:
                raise ValueError("skill_hypothesis_delivery_input_invalid")
            self._required(stages, "executed", "spot_evidence")
            self._required(stages, "executed", "features")
            self.packages.contracts.check("hypothesis_output_v1", value["output"])
            # No event is written here: the following real ProviderInput is the
            # mandatory proof that this complete native tool result was present.
            return hypothesis_delivery(decoded(encoded(value["output"])))
        else:
            raise ValueError("skill_node_not_installed")
        return node_response(
            event,
            self.nodes._read_value(event["details"]["output_ref"], event["details"]["output_hash"]),
        )

    def record(self, body, *, request_check=lambda: None):
        self.packages.contracts.check("record_request_v1", body)
        identity = body["identity"]
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with skill_original_read_scope(session):
                self._identity(session, identity)
                stages = self._stages(session, identity)
                previous = stages.get((body["stage"], ""))
                if previous is not None:
                    # A restarted Host may see the same original tool in a later
                    # actual input. Authenticate this new occurrence, then return
                    # the FIRST immutable receipt, never replace its original proof.
                    occurrence = read_occurrence(
                        session,
                        body["proof"],
                        run_id=identity["run_id"],
                        generation=identity["generation"],
                        root_session_id=identity["root_session_id"],
                        execution_session_id=identity["execution_session_id"],
                        request_hash=identity["request_hash"],
                        tool_name="decision_hub_skill",
                        provider_reader=self.use.provider_reader,
                        emission_reader=self.use.emission_reader,
                    )
                    if body["stage"] == "loaded":
                        event = self.use.read_loaded_in_session(
                            session, run_id=identity["run_id"], event_ref=previous
                        )
                        expected_args = dict(
                            operation="load", skill_version_id=event["skill_version_id"]
                        )
                        expected_result = self.use.load_in_session(
                            session,
                            **self._common(identity),
                            skill_version_id=event["skill_version_id"],
                        )
                        if (
                            occurrence.arguments != expected_args
                            or occurrence.result != expected_result
                        ):
                            raise ValueError("skill_loaded_recovery_original_mismatch")
                        return event
                    event = self.consumption.read_consumed_in_session(
                        session, run_id=identity["run_id"], event_ref=previous
                    )
                    executed = self.consumption.read_executed_in_session(
                        session,
                        run_id=identity["run_id"],
                        event_ref=event["details"]["consumer_event"],
                    )
                    original_call = executed["details"]["proof"]["native_step_call"]["call_id"]
                    if body["proof"]["call_id"] != original_call:
                        raise ValueError("skill_consumed_recovery_call_mismatch")
                    output = self.nodes._read_value(
                        event["details"]["consumer_output_ref"],
                        event["details"]["consumer_output_hash"],
                    )
                    expected_args = dict(
                        operation="execute_step",
                        skill_version_id=event["skill_version_id"],
                        node_id="hypothesis",
                        input={"output": output},
                    )
                    if (
                        occurrence.arguments != expected_args
                        or occurrence.result != hypothesis_delivery(output)
                    ):
                        raise ValueError("skill_consumed_recovery_original_mismatch")
                    return {"executed": executed, "consumed": event}
        common = self._common(identity)
        request_check()
        if body["stage"] == "loaded":
            prepared = self.use.prepare_loaded(
                **common, stage_request_id=body["stage_request_id"], proof=body["proof"]
            )
            return self._write(prepared, self.use.freeze_loaded_in_session, request_check)
        prepared = self.consumption.prepare(
            **common,
            stage_request_id=body["stage_request_id"],
            loaded_event_ref=self._required(stages, "loaded"),
            gateway_event_ref=self._required(stages, "executed", "spot_evidence"),
            feature_event_ref=self._required(stages, "executed", "features"),
            occurrence_proof=body["proof"],
        )
        return self._write(prepared, self.consumption.freeze_in_session, request_check)
