"""Finite normal tool entry points; every authority is re-read from originals."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
import weakref

from ..persistence.original_preparation import (
    _current_shape_read_scope,
    PreparedOriginals,
    begin_immediate,
    prepare_originals,
)
from ..ports.market_path import canonical_hash
from .research_recipe_read_scope import recipe_original_read_scope
from .event_read_scope import native_event_read_scope
from .research_execution_binding import read_prepared_execution_in_session
from .research_skill_consumption import hypothesis_delivery, node_response
from .research_skill_occurrence import read_occurrence
from .research_skill_package import decoded, encoded
from .research_skill_trial_selection import trial_original_read_scope
from .research_skill_workflow import SkillWorkflowService
from .research_skill_selected_delivery import record_contract

_ISSUED_SKILL_ORIGINALS = {}


@contextmanager
def skill_original_read_scope(session):
    """Reuse only existing guarded readers inside this physical RO snapshot.

    No execution/permission decision is cached. Prepared trial originals retain
    first-use and exit readset/raw/source checks, with business relations checked
    each use. Recipe exit and pure shape guards remain. A writer receives explicit
    originals and independently verifies them; it cannot reuse this scope.
    """
    from .cpi_pre_event_context import _archive
    from .research_graph_binding import _local_original_read

    with (
        _current_shape_read_scope(),
        _local_original_read(session) as event_boundary,
        native_event_read_scope(session, archive=_archive(), original_read=event_boundary),
        recipe_original_read_scope(session),
        trial_original_read_scope(session) as trial_scope,
    ):
        yield trial_scope


@dataclass(frozen=True)
class PreparedSkillOriginals(PreparedOriginals):
    """Carry nested trial originals whose native SQL bypasses outer capture.

    These are full authenticated readsets, not an allowed/verified flag. Keep
    PreparedOriginals' value/replace ABI and independently recheck them in every
    writer, including after its writes. Baseline preparations carry no trials.
    """

    trial_originals: tuple = ()
    issued_originals: PreparedOriginals | None = None

    def verify_read_snapshot(self, session, **kwargs):
        # The base issuer intentionally rejects subclasses/copies. Preserve its
        # exact issued object and require the wrapper's entire captured payload
        # to still be that object before delegating the independent RO recheck.
        original = self.issued_originals
        issued = _ISSUED_SKILL_ORIGINALS.get(id(self))
        if (original is None or issued is None or issued[0]() is not self
                or issued[1] is not original or issued[2] is not self.trial_originals or any(
            getattr(self, field.name) is not getattr(original, field.name)
            for field in fields(PreparedOriginals)
        )):
            raise ValueError("original_preparation_unissued")
        original.verify_read_snapshot(session, **kwargs)
        for trial in self.trial_originals:
            checkpoint = kwargs.get("checkpoint")
            if checkpoint is not None:
                checkpoint()
            trial.verify(session)
            if checkpoint is not None:
                checkpoint()

    def verify(self, session, **kwargs):
        super().verify(session, **kwargs)
        for original in self.trial_originals:
            checkpoint = kwargs.get("checkpoint")
            if checkpoint is not None:
                checkpoint()
            original.verify(session)
            if checkpoint is not None:
                checkpoint()

    def verify_after_writes(self, session, **kwargs):
        super().verify_after_writes(session, **kwargs)
        for original in self.trial_originals:
            checkpoint = kwargs.get("checkpoint")
            if checkpoint is not None:
                checkpoint()
            original.verify_after_writes(session)
            if checkpoint is not None:
                checkpoint()


def prepare_skill_originals(database, operation, **kwargs):
    """Preserve outer fences and explicitly carry nested trial writer readsets."""
    trial_originals = ()

    def read(session):
        nonlocal trial_originals
        with skill_original_read_scope(session) as trial_scope:
            result = operation(session)
            candidates = tuple(trial_scope.prepared.values())
        # No object is exposed or carried if any scope's successful-exit
        # authentication fails. Speculative archive blobs confer no authority.
        trial_originals = candidates
        return result

    prepared = prepare_originals(database, read, **kwargs)
    result = PreparedSkillOriginals(
        **{field.name: getattr(prepared, field.name) for field in fields(PreparedOriginals)},
        trial_originals=trial_originals,
        issued_originals=prepared,
    )
    key = id(result)
    _ISSUED_SKILL_ORIGINALS[key] = (
        weakref.ref(result, lambda _ref: _ISSUED_SKILL_ORIGINALS.pop(key, None)),
        prepared, trial_originals,
    )
    return result


@dataclass(frozen=True)
class SkillCallbackMaterial:
    """Internal branch result; authority remains in the enclosing preparation."""

    kind: str
    value: object


@dataclass(frozen=True)
class PreparedSkillCallbackValue:
    """Ephemeral per-attempt metadata and material, never persisted authority."""

    binding_json: bytes
    callback: SkillCallbackMaterial


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

    def _write(self, prepared, freezer, request_check, callback_writer=None, projection=None):
        if callback_writer is not None:
            return callback_writer(prepared, freezer, projection or (lambda value: value))
        request_check()
        with self.database.session() as session:
            begin_immediate(session)
            request_check()
            result = freezer(session, prepared)
            request_check()
            return result

    def tool(self, body, *, request_check=lambda: None, callback_writer=None):
        """Host callback; authenticate A+B in one outer snapshot before writing."""
        self.packages.contracts.check("tool_request_v1", body)
        body = decoded(encoded(body))
        args = body["arguments"]
        request_check()
        writes = args["operation"] == "execute_step" and args["node_id"] in {
            "spot_evidence", "features",
        }
        if writes:
            prepared = self.use.selections.originals_preparer(
                self.database, lambda session: self._tool_in_session(session, body)
            )
            callback = prepared.value
            freezers = {
                "gateway": self.nodes.freeze_gateway_in_session,
                "component": self.nodes.freeze_component_in_session,
            }
            event = self._write(
                replace(prepared, value=callback.value), freezers[callback.kind], request_check,
                callback_writer, self._node_response,
            )
            if callback_writer is not None:
                return event
            return node_response(
                event,
                self.nodes._read_value(event["details"]["output_ref"], event["details"]["output_hash"]),
            )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with skill_original_read_scope(session):
                callback = self._tool_in_session(session, body)
        request_check()
        return decoded(callback.value)

    def _tool_in_session(self, session, body):
        identity, args = body["identity"], body["arguments"]
        self._tool_original(session, body)
        common = self._common(identity)
        if args["operation"] == "list":
            return SkillCallbackMaterial("response", encoded(
                self.workflow.list_in_session(session, **common)
            ))
        if args["operation"] == "load":
            return SkillCallbackMaterial("response", encoded(
                self.use.load_in_session(session, **common, skill_version_id=args["skill_version_id"])
            ))
        stages = self._stages(session, identity)
        loaded = self._required(stages, "loaded")
        key = "skill_step_" + canonical_hash([identity, body["call_id"]])
        node, value = args["node_id"], args["input"]
        if node == "spot_evidence" and value == {"source": "frozen_original"}:
            if body["research_occurrence"] is not None:
                raise ValueError("skill_original_read_cannot_borrow_occurrence")
            material = self.nodes.prepare_original_in_session(
                session, **common, stage_request_id=key,
                loaded_event_ref=loaded, call_id=body["call_id"],
            )
            return SkillCallbackMaterial("gateway", material)
        if node == "spot_evidence":
            if set(value) != {"research_call_id"} or not isinstance(value["research_call_id"], str):
                raise ValueError("skill_gateway_binding_input_invalid")
            proof = body["research_occurrence"]
            if proof is None or proof["call_id"] != value["research_call_id"]:
                raise ValueError("skill_gateway_occurrence_required")
            material = self.nodes.prepare_gateway_in_session(
                session, **common, stage_request_id=key, loaded_event_ref=loaded,
                research_call_id=value["research_call_id"], occurrence_proof=proof,
                binder_call_id=body["call_id"],
            )
            return SkillCallbackMaterial("gateway", material)
        if node == "features":
            if value or body["research_occurrence"] is not None:
                raise ValueError("skill_component_input_must_be_empty")
            material = self.nodes.prepare_component_in_session(
                session, **common, stage_request_id=key, loaded_event_ref=loaded,
                gateway_event_ref=self._required(stages, "executed", "spot_evidence"),
                call_id=body["call_id"],
            )
            return SkillCallbackMaterial("component", material)
        if node == "hypothesis":
            if set(value) != {"output"} or body["research_occurrence"] is not None:
                raise ValueError("skill_hypothesis_delivery_input_invalid")
            self._required(stages, "executed", "spot_evidence")
            self._required(stages, "executed", "features")
            self.packages.contracts.check("hypothesis_output_v1", value["output"])
            return SkillCallbackMaterial("response", encoded(hypothesis_delivery(value["output"])))
        raise ValueError("skill_node_not_installed")

    def record(self, body, *, request_check=lambda: None, callback_writer=None):
        self.packages.contracts.check(record_contract(body), body)
        body = decoded(encoded(body))
        request_check()
        prepared = self.use.selections.originals_preparer(
            self.database, lambda session: self._record_in_session(session, body)
        )
        callback = prepared.value
        request_check()
        if callback.kind == "response":
            return decoded(callback.value)
        freezer = (
            self.use.freeze_loaded_in_session if callback.kind == "loaded"
            else self.consumption.freeze_in_session
        )
        return self._write(replace(prepared, value=callback.value), freezer, request_check, callback_writer)

    def _record_in_session(self, session, body):
        identity = body["identity"]
        self._identity(session, identity)
        stages = self._stages(session, identity)
        previous = stages.get((body["stage"], ""))
        if previous is not None:
            if body["proof"].get("kind") == "selected_skill_provider_input.v1":
                if body["stage"] != "loaded":
                    raise ValueError("skill_selected_proof_loaded_only")
                material = self.use._prove(session, dict(
                    **self._common(identity), stage_request_id=body["stage_request_id"],
                    proof=body["proof"],
                ))
                event = self.use.read_loaded_in_session(
                    session, run_id=identity["run_id"], event_ref=previous
                )
                current = decoded(material.selection_json)
                if any(event[key] != current[key] for key in (
                    "use_id", "skill_version_id", "package_hash", "workflow_hash",
                    "method_binding_hash"
                )):
                    raise ValueError("skill_selected_recovery_changed")
                return SkillCallbackMaterial("response", encoded(event))
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
                return SkillCallbackMaterial("response", encoded(event))
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
            return SkillCallbackMaterial("response", encoded({"executed": executed, "consumed": event}))
        common = self._common(identity)
        if body["stage"] == "loaded":
            material = self.use.prepare_loaded_in_session(
                session, **common, stage_request_id=body["stage_request_id"], proof=body["proof"]
            )
            return SkillCallbackMaterial("loaded", material)
        material = self.consumption.prepare_in_session(
            session, **common, stage_request_id=body["stage_request_id"],
            loaded_event_ref=self._required(stages, "loaded"),
            gateway_event_ref=self._required(stages, "executed", "spot_evidence"),
            feature_event_ref=self._required(stages, "executed", "features"),
            occurrence_proof=body["proof"],
        )
        return SkillCallbackMaterial("consumed", material)

    def _node_response(self, event):
        return node_response(
            event,
            self.nodes._read_value(event["details"]["output_ref"], event["details"]["output_hash"]),
        )

    def prepare_callback(
        self, kind, body, *, request_check=lambda: None, preparation_check=None,
        source_lease=None, diagnostics=None
    ):
        """Authenticate metadata, native/stages and material in one outer RO scope.

        All captured original SQL/source/raw and nested trial preparations remain
        in the actual configured PreparedOriginals subclass. No verified flag or
        preparation is persisted between callback attempts. A durable worker may
        supply its confirmed-lease abort check for hot RO checkpoints; entry and
        after-preparation permission fences and the independent writer remain full.
        """
        if kind not in {"tool", "records"}:
            raise ValueError("skill_callback_kind_invalid")
        self.packages.contracts.check(
            "tool_request_v1" if kind == "tool" else record_contract(body), body
        )
        body = decoded(encoded(body))
        request_check()

        def read(session):
            operation = self._tool_in_session if kind == "tool" else self._record_in_session
            callback = operation(session, body)
            selection, execution, _ = self._identity(session, body["identity"])
            frozen = read_prepared_execution_in_session(
                session, body["identity"]["run_id"], body["identity"]["generation"]
            )
            binding = dict(
                deadline_at=frozen.execution.request.deadline_at.isoformat(),
                method_hash=execution.method_hash,
                selection_ref=selection["selection_id"],
                compiled_plan_ref=selection["compiled_plan_ref"],
                compiled_plan_hash=selection["compiled_plan_hash"],
                task_request_hash=execution.task_request_hash,
            )
            if source_lease is not None:
                # Same RO, after full native/stage/material authentication. No
                # selected package or source bytes grant permission by themselves.
                source_lease.extend_authenticated_selection(session, body["identity"]["run_id"])
            return PreparedSkillCallbackValue(encoded(binding), callback)

        return self.use.selections.originals_preparer(
            self.database, read,
            checkpoint=request_check if preparation_check is None else preparation_check,
            source_lease=source_lease, diagnostics=diagnostics,
        )

    def execute_prepared_callback(self, prepared, *, request_check=lambda: None, callback_writer):
        """The sole domain writer and first job response share the Q1 transaction."""
        if not isinstance(prepared.value, PreparedSkillCallbackValue):
            raise ValueError("skill_callback_preparation_required")
        callback = prepared.value.callback
        if callback.kind == "response":
            def freeze_read(session, material):
                material.verify(session)
                return decoded(encoded(material.value))

            freezer, value, projection = freeze_read, decoded(callback.value), lambda result: result
        else:
            freezer = {
                "gateway": self.nodes.freeze_gateway_in_session,
                "component": self.nodes.freeze_component_in_session,
                "loaded": self.use.freeze_loaded_in_session,
                "consumed": self.consumption.freeze_in_session,
            }[callback.kind]
            value = callback.value
            projection = self._node_response if callback.kind in {"gateway", "component"} else lambda result: result
        return self._write(
            replace(prepared, value=value), freezer, request_check, callback_writer, projection
        )
