"""Independently authenticate real skill consumption in the selected final input."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime

from ..decision.action_derivation import ActionBasisEvidence
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_action_synthesis import normalize_action_synthesis
from ..ports.research_signal import ResearchDecisionSynthesis
from .provider_input_receipts import SYNTHESIS_TOOL
from .research_skill_consumption import SkillConsumptionService
from .research_skill_occurrence import block_hash, read_occurrence
from .research_skill_package import decoded, encoded, sha


class _ArchiveReads:
    def __init__(self, archive, kind, reads):
        self.archive, self.kind, self.reads = archive, kind, reads

    def read(self, ref):
        body = self.archive.read(ref)
        key, digest = (self.kind, ref), sha(body)
        if key in self.reads and self.reads[key] != digest:
            raise ValueError("skill_publication_raw_changed")
        self.reads[key] = digest
        return body

    def put(self, _body):
        # An independent reader cannot recreate a missing immutable original.
        raise ValueError("skill_publication_raw_missing")


@dataclass(frozen=True)
class SkillPublicationRead:
    evidence: tuple[ActionBasisEvidence, ...]
    originals: tuple[tuple[str, str, str], ...]


class ResearchSkillPublicationReader:
    def __init__(self, consumption):
        self.consumption = consumption

    def _tracked(self, reads):
        original = self.consumption
        packages = copy.copy(original.packages)
        packages.archive = _ArchiveReads(original.packages.archive, "package", reads)
        selections = copy.copy(original.use.selections)
        selections.packages = packages
        use = copy.copy(original.use)
        use.packages, use.selections = packages, selections
        nodes = copy.copy(original.nodes)
        nodes.use, nodes.packages = use, packages
        nodes.market = copy.copy(original.nodes.market)
        nodes.market.archive = _ArchiveReads(original.nodes.market.archive, "market", reads)
        return SkillConsumptionService(nodes)

    def verify_originals(self, originals):
        for kind, reference, digest in originals:
            archive = (
                self.consumption.packages.archive
                if kind == "package"
                else self.consumption.nodes.market.archive
            )
            if sha(archive.read(reference)) != digest:
                raise ValueError("skill_publication_raw_changed")

    def read_in_session(self, session, *, trusted):
        native = trusted.native_action
        if native is None or native.review_gate_status != "matched":
            raise ValueError("skill_publication_reviewed_native_required")
        synthesis = ResearchDecisionSynthesis.model_validate_json(native.synthesis_json)
        if not synthesis.skill_consumption_refs:
            return SkillPublicationRead((), ())
        reads = {}
        consumption = self._tracked(reads)
        use = consumption.use
        execution = use.selections.execution_reader(
            session, trusted.run_id, native.producer_generation
        )
        identity = decoded(execution.scope_identity_json)
        selection, _, _ = use._selection(
            session, trusted.run_id, native.producer_generation, identity["dsh_session_id"]
        )
        witness = decoded(native.selection_witness_json)
        if witness.get("kind") != "tool_capture" or not witness.get("call_id"):
            raise ValueError("skill_final_native_call_required")
        pair = use.emission_reader(
            session,
            run_id=trusted.run_id,
            generation=native.producer_generation,
            root_session_id=identity["dsh_session_id"],
            execution_session_id=identity["dsh_session_id"],
            call_id=witness["call_id"],
        )
        if pair is None:
            raise ValueError("skill_final_provider_emission_required")
        emission, _ = pair
        args = decoded(emission.submit.raw_arguments_json)
        if (
            emission.submit.tool_name != SYNTHESIS_TOOL
            or set(args) != {"candidate"}
            or normalize_action_synthesis(args["candidate"]).model_dump(mode="json")
            != synthesis.model_dump(mode="json")
            or emission.submit.request_hash != native.execution_request_hash
            or execution.task_request_hash != native.task_request_hash
        ):
            raise ValueError("skill_final_synthesis_original_mismatch")
        provider, provider_ref = use.provider_reader(session, emission.submit.input_ref)
        transcript = decoded(provider.submit.input_json)
        if (
            provider.submit.run_id != trusted.run_id
            or provider.submit.generation != native.producer_generation
            or provider.submit.execution_session_id != identity["dsh_session_id"]
            or provider.submit.request_hash != native.execution_request_hash
            or aware_utc(provider.known_at) > aware_utc(emission.known_at)
        ):
            raise ValueError("skill_final_provider_input_mismatch")
        evidence = []
        seen = set()
        for ref in synthesis.skill_consumption_refs:
            if ref.consumption_receipt_ref in seen:
                raise ValueError("skill_final_duplicate_consumption")
            seen.add(ref.consumption_receipt_ref)
            event = consumption.read_consumed_in_session(
                session,
                run_id=trusted.run_id,
                event_ref={
                    "event_id": ref.consumption_receipt_ref,
                    "payload_hash": ref.consumption_receipt_hash,
                },
            )
            if (
                event["generation"] != native.producer_generation
                or event["skill_version_id"] != ref.version_id
                or selection["version_hash"] != ref.version_hash
                or event["use_id"] != selection["use_id"]
                or event["root_session_id"] != identity["dsh_session_id"]
                or selection["scope_id"] != trusted.scope_id
            ):
                raise ValueError("skill_final_consumption_binding_mismatch")
            known = aware_utc(datetime.fromisoformat(event["first_known_at"]))
            input_known = aware_utc(
                datetime.fromisoformat(event["details"]["market_inputs_known_through"])
            )
            if known > aware_utc(provider.known_at) or input_known > native.producer_cutoff_at:
                raise ValueError("skill_final_consumption_time_invalid")
            self._list_delivery(session, use, provider, provider_ref, transcript, identity, event)
            output = consumption.nodes._read_value(
                event["details"]["consumer_output_ref"], event["details"]["consumer_output_hash"]
            )
            review = output["mechanism_review"]
            matches = [x for x in synthesis.mechanism_reviews if x.review_id == review["review_id"]]
            if len(matches) != 1 or matches[0].model_dump(mode="json") != normalize_review(review):
                raise ValueError("skill_final_intermediate_mechanism_not_consumed")
            evidence.append(
                ActionBasisEvidence(
                    ref=ref.consumption_receipt_ref,
                    payload_hash=ref.consumption_receipt_hash,
                    kind="skill",
                    known_at=known,
                    independence_key="method:" + ref.version_id,
                    eligible=True,
                    input_known_through=input_known,
                    consumer_input_known_at=aware_utc(provider.known_at),
                )
            )
        return SkillPublicationRead(
            tuple(evidence),
            tuple((kind, ref, digest) for (kind, ref), digest in sorted(reads.items())),
        )

    @staticmethod
    def _list_delivery(session, use, provider, provider_ref, transcript, identity, event):
        matches = []
        messages = transcript.get("messages", [])
        for ci, message in enumerate(messages):
            for bi, call in enumerate(message.get("content", [])):
                if (
                    not isinstance(call, dict)
                    or call.get("type") != "tool-call"
                    or call.get("name") != "decision_hub_skill"
                ):
                    continue
                if decoded(call.get("arguments", "{}")) != {"operation": "list"}:
                    continue
                for ri, result in enumerate(messages):
                    if result.get("source", {}).get("callId") != call.get("id"):
                        continue
                    proof = dict(
                        kind="provider_input_transcript.v1",
                        input_ref=provider_ref.model_dump(mode="json"),
                        dispatch_id=provider.submit.dispatch_id,
                        call_id=call["id"],
                        tool_name="decision_hub_skill",
                        call_json_pointer=f"/messages/{ci}/content/{bi}",
                        result_json_pointer=f"/messages/{ri}",
                        call_hash=block_hash(call),
                        result_hash=block_hash(result),
                    )
                    occurrence = read_occurrence(
                        session,
                        proof,
                        run_id=event["run_id"],
                        generation=event["generation"],
                        root_session_id=identity["dsh_session_id"],
                        execution_session_id=identity["dsh_session_id"],
                        request_hash=identity["request_hash"],
                        tool_name="decision_hub_skill",
                        provider_reader=use.provider_reader,
                        emission_reader=use.emission_reader,
                    )
                    value = occurrence.result
                    if (
                        not isinstance(value, dict)
                        or value.get("schema_version") != "research-skill-list.v1"
                    ):
                        continue
                    use.packages.contracts.check("list_response_v1", value)
                    if any(
                        item["stage"] == "consumed"
                        and item["event_ref"]
                        == {"event_id": event["event_id"], "payload_hash": canonical_hash(event)}
                        for item in value["stage_receipts"]
                    ):
                        if aware_utc(occurrence.emission.known_at) < aware_utc(
                            datetime.fromisoformat(event["first_known_at"])
                        ):
                            raise ValueError("skill_final_receipt_delivery_before_consumption")
                        matches.append(occurrence)
        if not matches:
            raise ValueError("skill_final_actual_consumed_receipt_delivery_required")


def normalize_review(value):
    from ..ports.research_signal import MechanismReview

    return MechanismReview.model_validate_json(encoded(value)).model_dump(mode="json")
