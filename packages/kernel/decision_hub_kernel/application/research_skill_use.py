"""Staged S3 loaded original service; no invented Gateway execution/consumption."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    read_provider_emission_in_session,
    read_provider_input_in_session,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ResearchSkillUseEventRecord,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, as_utc, utcnow
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from . import research_skill_compiler as compiler
from .research_skill_occurrence import read_occurrence
from .research_skill_package import decoded, encoded, sha
from .research_skill_selection import SkillSelectionService

FRESH_LOAD_INSTRUCTIONS = (
    "First call the named ordinary tool with these exact business arguments, "
    "then bind its actual call ID through skill execute_step. "
    "Do not add fields or raise max_results. Additional analysis must use a separate "
    "ordinary research call. Delivery alone does not prove loaded, executed, "
    "consumed, or financial value."
)

ROLE = "skill_loaded_proof"
FIELDS = (
    "use_id",
    "skill_version_id",
    "version_hash",
    "package_hash",
    "workflow_hash",
    "compiled_plan_hash",
)
COLUMNS = (
    "event_id",
    "use_id",
    "run_id",
    "generation",
    "root_session_id",
    "execution_session_id",
    "skill_version_id",
    "package_hash",
    "workflow_hash",
    "method_binding_hash",
    "stage",
    "node_id",
    "stage_request_id",
)


def block_hash(value):
    return sha(tool_arguments_json(value).encode())


def event_ref(event):
    return {"event_id": event["event_id"], "payload_hash": canonical_hash(event)}


@dataclass(frozen=True)
class LoadedMaterial:
    intent_json: bytes
    details_json: bytes
    selection_json: bytes
    archive_readset: tuple[tuple[str, str], ...]
    original_known_at: datetime
    existing_event_json: bytes | None = None


class SkillUseService:
    def __init__(
        self,
        selections: SkillSelectionService,
        *,
        provider_reader=read_provider_input_in_session,
        emission_reader=read_provider_emission_in_session,
        clock=utcnow,
    ):
        self.selections, self.packages = selections, selections.packages
        self.provider_reader, self.emission_reader, self.clock = (
            provider_reader,
            emission_reader,
            clock,
        )

    def _selection(self, session, run_id, generation, execution_session_id):
        if (
            type(generation) is not int
            or generation < 1
            or not isinstance(run_id, str)
            or not 1 <= len(run_id) <= 128
            or not isinstance(execution_session_id, str)
            or not 1 <= len(execution_session_id) <= 256
        ):
            raise ValueError("skill_use_identity_invalid")
        selection = self.selections.read_in_session(session, run_id)
        execution = self.selections.execution_reader(session, run_id, generation)
        identity = decoded(execution.scope_identity_json)
        if selection["generation"] != generation or execution.generation != generation:
            raise ValueError("skill_use_generation_mismatch")
        if identity["dsh_session_id"] != execution_session_id:
            raise ValueError("skill_root_session_required")
        return selection, execution, identity

    def list_in_session(self, session: Session, *, run_id, generation, execution_session_id):
        selection, _, _ = self._selection(session, run_id, generation, execution_session_id)
        result = {
            "schema_version": "research-skill-list.v1",
            "status": "selected",
            "items": [{key: selection[key] for key in FIELDS}],
            "stage_receipts": [],
        }
        self.packages.contracts.check("list_response_v1", result)
        return result

    def _load_directive(self, session, selection, execution):
        # Selection/package readers authenticate this exact compiled original.
        # Initial evidence acquisition is literal-only: never fabricate missing
        # market input or evaluate a future node at load time.
        raw = self.packages.archive.read(selection["compiled_plan_ref"])
        if sha(raw) != selection["compiled_plan_hash"]:
            raise ValueError("skill_gateway_compiled_plan_changed")
        plan = compiler.CompiledWorkflow(raw.decode(), selection["compiled_plan_hash"]).read()
        node = next(
            (item for item in plan["workflow"]["nodes"] if item["id"] == "spot_evidence"), None
        )
        if (
            node is None
            or node["op"] != "evidence.acquire"
            or node["capability_id"] != "spot_market_facts"
            or node["depends_on"]
            or any(binding["from"] != "literal" for binding in node["inputs"].values())
        ):
            raise ValueError("skill_initial_gateway_intent_not_static")
        intent = {name: binding["value"] for name, binding in node["inputs"].items()}
        self.packages.contracts.check("gateway_intent_v1", intent)
        return {
            "next_tool_call": {
                "tool_name": "decision_hub_research",
                "node_id": "spot_evidence",
                "arguments": {
                    "capability_id": node["capability_id"],
                    **intent,
                    "max_cost_usd": None,
                },
            },
            "instructions": FRESH_LOAD_INSTRUCTIONS,
        }

    def load_in_session(
        self, session: Session, *, run_id, generation, execution_session_id, skill_version_id
    ):
        selection, execution, _ = self._selection(session, run_id, generation, execution_session_id)
        if selection["skill_version_id"] != skill_version_id:
            raise ValueError("skill_load_version_not_selected")
        package = self.packages.read_in_session(session, skill_version_id)
        body = next(item.body for item in package.resources if item.path == "SKILL.md")
        if len(body) > 262144:
            raise ValueError("skill_method_delivery_oversize")
        result = {
            "schema_version": "research-skill-load.v2",
            **self._load_directive(session, selection, execution),
            "status": "delivered",
            **{key: selection[key] for key in FIELDS},
            "method_body": body.decode("utf8"),
            "method_body_hash": sha(body),
        }
        self.packages.contracts.check("load_response_v2", result)
        return result

    def _prove(self, session, intent):
        proof = intent["proof"]
        if proof.get("kind") == "selected_skill_provider_input.v1":
            from .research_skill_selected_delivery import prove_selected_input

            details, original = prove_selected_input(self, session, intent)
            selection, _, _ = self._selection(
                session, intent["run_id"], intent["generation"], intent["execution_session_id"]
            )
            return LoadedMaterial(
                encoded(intent), encoded(details), encoded(selection),
                self.selections._archive_refs(session, selection), aware_utc(original.known_at),
            )
        self.packages.contracts.check("tool_occurrence_proof_v1", proof)
        if proof["kind"] != "provider_input_transcript.v1":
            raise ValueError("skill_native_transport_reader_not_installed")
        selection, execution, identity = self._selection(
            session,
            intent["run_id"],
            intent["generation"],
            intent["execution_session_id"],
        )
        expected = self.load_in_session(
            session,
            run_id=intent["run_id"],
            generation=intent["generation"],
            execution_session_id=intent["execution_session_id"],
            skill_version_id=selection["skill_version_id"],
        )
        occurrence = read_occurrence(
            session,
            proof,
            run_id=intent["run_id"],
            generation=intent["generation"],
            root_session_id=identity["dsh_session_id"],
            execution_session_id=intent["execution_session_id"],
            request_hash=identity["request_hash"],
            tool_name="decision_hub_skill",
            provider_reader=self.provider_reader,
            emission_reader=self.emission_reader,
        )
        original, submit = occurrence.provider_input, occurrence.provider_input.submit
        selected_at = datetime.fromisoformat(selection["selected_at"])
        if (
            aware_utc(original.known_at) < selected_at
            or aware_utc(occurrence.emission.known_at) < selected_at
        ):
            raise ValueError("skill_loaded_provider_emission_mismatch")
        if occurrence.arguments != {
            "operation": "load",
            "skill_version_id": selection["skill_version_id"],
        }:
            raise ValueError("skill_loaded_call_not_load")
        if occurrence.result != expected:
            raise ValueError("skill_loaded_result_not_exact_package")
        selected_row = session.get(
            ResearchSkillUseEventRecord, selection["use_id"] + "_selected", populate_existing=True
        )
        # S2 reader above independently validated this exact selected event.
        details = {
            "stage": "loaded",
            "selected_event": {
                "event_id": selected_row.event_id,
                "payload_hash": selected_row.payload_hash,
            },
            "native_load": proof,
            "provider_input_ref": proof["input_ref"],
            "dispatch_id": submit.dispatch_id,
            "method_body_hash": expected["method_body_hash"],
            "delivered_package_hash": expected["package_hash"],
            "provider_input_method_loci": [
                proof["result_json_pointer"] + "/content/0/content/0/text"
            ],
            "provider_input_result_loci": [proof["result_json_pointer"]],
        }
        self.packages.contracts.check("loaded_details_v1", details)
        archive = self.selections._archive_refs(session, selection)
        return LoadedMaterial(
            encoded(intent),
            encoded(details),
            encoded(selection),
            archive,
            aware_utc(original.known_at),
        )

    def read_selected_delivery_in_session(self, session, *, run_id, generation, root_session_id):
        from .research_skill_selected_delivery import read_delivery_in_session

        return read_delivery_in_session(self, session, run_id=run_id, generation=generation,
                                        root_session_id=root_session_id)

    def prepare_loaded(self, *, run_id, generation, execution_session_id, stage_request_id, proof):
        if not isinstance(stage_request_id, str) or not 1 <= len(stage_request_id) <= 128:
            raise ValueError("skill_stage_request_invalid")
        arguments = decoded(encoded(dict(run_id=run_id, generation=generation, execution_session_id=execution_session_id, stage_request_id=stage_request_id, proof=proof)))
        return self.selections.originals_preparer(
            self.packages.registry.database,
            lambda session: self.prepare_loaded_in_session(session, **arguments),
        )

    def prepare_loaded_in_session(self, session, *, run_id, generation, execution_session_id, stage_request_id, proof):
        if not isinstance(stage_request_id, str) or not 1 <= len(stage_request_id) <= 128:
            raise ValueError("skill_stage_request_invalid")
        # Detach caller data before the captured authentication transaction.
        intent = decoded(
            encoded(
                dict(
                    run_id=run_id,
                    generation=generation,
                    execution_session_id=execution_session_id,
                    stage_request_id=stage_request_id,
                    proof=proof,
                )
            )
        )
        return self._prepare_loaded(session, intent)

    def _prepare_loaded(self, session, intent):
        from dataclasses import replace

        material = self._prove(session, intent)
        selection = decoded(material.selection_json)
        key = canonical_hash([selection["use_id"], "loaded", intent["stage_request_id"]])[:40]
        existing = session.get(
            ResearchSkillUseEventRecord, "skill_evt_" + key, populate_existing=True
        )
        original = session.get(RunRoleOutputRecord, "skill_proof_" + key, populate_existing=True)
        if existing is None and original is None:
            return material
        if existing is None or original is None:
            raise ValueError("skill_loaded_stage_original_incomplete")
        body = self.read_loaded_in_session(
            session,
            run_id=intent["run_id"],
            event_ref={"event_id": existing.event_id, "payload_hash": existing.payload_hash},
        )
        if decoded(original.payload_json)["intent"] != intent or body["details"] != decoded(
            material.details_json
        ):
            raise ValueError("skill_loaded_stage_key_conflict")
        return replace(material, existing_event_json=encoded(body))

    def freeze_loaded_in_session(self, session: Session, prepared):
        prepared.verify(session)
        material = prepared.value
        intent, details, selection = (
            decoded(material.intent_json),
            decoded(material.details_json),
            decoded(material.selection_json),
        )
        self.packages.installed.verify()
        for ref, digest in material.archive_readset:
            if sha(self.packages.archive.read(ref)) != digest:
                raise ValueError("skill_loaded_archive_changed")
        key = canonical_hash([selection["use_id"], "loaded", intent["stage_request_id"]])[:40]
        event_id, proof_id = "skill_evt_" + key, "skill_proof_" + key
        existing = session.get(ResearchSkillUseEventRecord, event_id, populate_existing=True)
        if existing is not None:
            if (
                material.existing_event_json is None
                or existing.payload_json.encode() != material.existing_event_json
                or existing.payload_hash != sha(material.existing_event_json)
            ):
                raise ValueError("skill_loaded_stage_key_conflict")
            return decoded(material.existing_event_json)
        if material.existing_event_json is not None:
            raise ValueError("skill_loaded_stage_original_deleted")
        now = aware_utc(self.clock())
        if now < material.original_known_at:
            raise ValueError("skill_loaded_time_invalid")
        selected_input = details.get("schema_version") == "research-skill-loaded-details.v2"
        original = {
            "schema_version": (
                "research-skill-loaded-proof.v2" if selected_input
                else "research-skill-loaded-proof.v1"
            ),
            "intent": intent,
            "details": details,
            "known_at": now.isoformat(),
        }
        reference = {
            "output_id": proof_id,
            "role": ROLE + ":" + key,
            "schema_version": original["schema_version"],
            "payload_hash": canonical_hash(original),
            "recorded_at": now.isoformat(),
        }
        body = {
            "schema_version": (
                "research-skill-use-event.v2" if selected_input else "research-skill-use-event.v1"
            ),
            "event_id": event_id,
            **{
                key: selection[key]
                for key in (
                    "use_id",
                    "run_id",
                    "generation",
                    "skill_version_id",
                    "package_hash",
                    "workflow_hash",
                    "method_binding_hash",
                )
            },
            "root_session_id": intent["execution_session_id"],
            "execution_session_id": intent["execution_session_id"],
            "stage": "loaded",
            "node_id": "",
            "stage_request_id": intent["stage_request_id"],
            "parent_event": details["selected_event"],
            "original": reference,
            "first_known_at": now.isoformat(),
            "details": details,
        }
        self.packages.contracts.check("use_event_v2" if selected_input else "use_event_v1", body)
        session.add(
            RunRoleOutputRecord(
                output_id=proof_id,
                run_id=selection["run_id"],
                role=reference["role"],
                schema_version=original["schema_version"],
                payload_json=encoded(original).decode(),
                payload_hash=canonical_hash(original),
                created_at=now,
            )
        )
        session.add(
            ResearchSkillUseEventRecord(
                **{key: body[key] for key in COLUMNS},
                parent_event_ref=encoded(body["parent_event"]).decode(),
                original_ref=encoded(reference).decode(),
                original_hash=reference["payload_hash"],
                first_known_at=now,
                payload_json=encoded(body).decode(),
                payload_hash=canonical_hash(body),
            )
        )
        session.flush()
        prepared.verify_after_writes(session)
        return body

    def read_loaded_in_session(self, session: Session, *, run_id, event_ref):
        row = session.get(
            ResearchSkillUseEventRecord, event_ref["event_id"], populate_existing=True
        )
        if row is None:
            raise ValueError("skill_loaded_original_missing")
        body = decoded(row.payload_json)
        selected_input = body.get("schema_version") == "research-skill-use-event.v2"
        self.packages.contracts.check("use_event_v2" if selected_input else "use_event_v1", body)
        if (
            row.run_id != run_id
            or body["stage"] != "loaded"
            or row.payload_hash != event_ref["payload_hash"]
            or row.payload_hash != canonical_hash(body)
            or row.payload_json.encode() != encoded(body)
            or any(getattr(row, key) != body[key] for key in COLUMNS)
            or row.original_ref != encoded(body["original"]).decode()
            or row.parent_event_ref != encoded(body["parent_event"]).decode()
            or row.original_hash != body["original"]["payload_hash"]
            or as_utc(row.first_known_at) != datetime.fromisoformat(body["first_known_at"])
        ):
            raise ValueError("skill_loaded_event_invalid")
        ref = body["original"]
        original = session.get(RunRoleOutputRecord, ref["output_id"], populate_existing=True)
        if original is None:
            raise ValueError("skill_loaded_proof_missing")
        payload = decoded(original.payload_json)
        if (
            original.run_id != run_id
            or not original.role.startswith(ROLE + ":")
            or original.role != ref["role"]
            or original.schema_version != ref["schema_version"]
            or original.payload_hash != ref["payload_hash"]
            or original.payload_hash != canonical_hash(payload)
            or original.payload_json.encode() != encoded(payload)
            or as_utc(original.created_at) != datetime.fromisoformat(ref["recorded_at"])
            or payload["known_at"] != body["first_known_at"]
            or payload["details"] != body["details"]
        ):
            raise ValueError("skill_loaded_proof_invalid")
        material = self._prove(session, payload["intent"])
        intent, selection = payload["intent"], decoded(material.selection_json)
        expected_key = canonical_hash([selection["use_id"], "loaded", intent["stage_request_id"]])[
            :40
        ]
        if (
            decoded(material.details_json) != body["details"]
            or intent["run_id"] != run_id
            or intent["generation"] != body["generation"]
            or intent["stage_request_id"] != body["stage_request_id"]
            or body["event_id"] != "skill_evt_" + expected_key
            or ref["output_id"] != "skill_proof_" + expected_key
            or ref["role"] != ROLE + ":" + expected_key
            or payload["schema_version"] != (
                "research-skill-loaded-proof.v2" if selected_input
                else "research-skill-loaded-proof.v1"
            )
            or original.schema_version != payload["schema_version"]
            or body["root_session_id"] != intent["execution_session_id"]
            or body["execution_session_id"] != intent["execution_session_id"]
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
            or material.original_known_at > as_utc(row.first_known_at)
        ):
            raise ValueError("skill_loaded_original_changed")
        return body
