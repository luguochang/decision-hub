"""Finite behavior gate over actual immutable attempts, not model-declared pass.

Storage/runtime/source adapters are composition-owned. Public entry accepts only
the preregistered protocol identity. Normal trial rejects controlled executions.
"""

from packages.kernel.decision_hub_kernel.ports.research_learning import (
    BehaviorAttempt,
    BehaviorCase,
    BehaviorOutput,
    BehaviorProtocol,
    BehaviorReceipt,
    BehaviorOriginalRef as Ref,
)
from dataclasses import dataclass
from packages.kernel.decision_hub_kernel.application.research_learning_async import learning_sync
from datetime import datetime
from typing import Literal
from jsonschema import Draft202012Validator
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.application.research_skill_package import (
    decoded,
    encoded,
    sha,
)


def ref(value, identity="id"):
    body = value.model_dump(mode="json")
    return Ref(ref=body[identity], hash=canonical_hash(body))


@dataclass(frozen=True)
class RuntimeOutput:
    payload: dict
    runtime_id: str
    completed_at: datetime
    original: dict | None = None


class BehaviorControlRunner:
    def __init__(self, protocols, attempts, archive, materials, runtime, *, clock):
        self.protocols, self.attempts, self.archive = (protocols, attempts, archive)
        self.materials, self.runtime, self.clock = (materials, runtime, clock)

    def _read(self, reference):
        raw = self.archive.read(reference.ref)
        if sha(raw) != reference.hash:
            raise ValueError("behavior_original_changed")
        return decoded(raw)

    def _archive(self, value):
        raw = encoded(value)
        digest, address = self.archive.put(raw)
        return Ref(ref=address, hash=digest)

    async def evaluate(self, protocol_id: str):
        # Advance finite synchronous original/read/write work in a thread. The
        # only yielded boundary is an already-recorded model attempt; its async
        # call stays on the owning loop. No SQL session spans this boundary.
        steps = self._evaluation_steps(protocol_id)
        result = None
        try:
            while True:
                complete, value = await learning_sync(self._advance_evaluation, steps, result)
                if complete:
                    return value
                result = await self.runtime.execute(value)
        finally:
            await learning_sync(steps.close)

    @staticmethod
    def _advance_evaluation(steps, result):
        # Future cannot carry StopIteration, so return an explicit completion.
        try:
            return False, steps.send(result)
        except StopIteration as finished:
            return True, finished.value

    def _evaluation_steps(self, protocol_id: str):
        protocol = self.protocols.read(protocol_id)
        if not isinstance(protocol, BehaviorProtocol):
            raise ValueError("behavior_protocol_reader_invalid")
        self.materials.require_package_stage(
            protocol.job_id, protocol.package_stage, protocol.parent, protocol.candidate
        )
        now = self.clock()
        if protocol.registered_at > now or self.runtime.recipe_hash != protocol.recipe_hash:
            raise ValueError("behavior_runtime_or_registration_mismatch")
        if self.runtime.mode not in {"live_model", "controlled_model"}:
            raise ValueError("behavior_runtime_mode_invalid")
        mode = self.runtime.mode
        old = self.attempts.receipt(protocol_id)
        if old is not None:
            if old.protocol != ref(protocol):
                raise ValueError("behavior_receipt_protocol_mismatch")
        attempt_refs, output_refs = ([], [])
        passed = {"parent": True, "candidate": True}
        for case in protocol.cases:
            case_input, expected = (self._read(case.inputs), self._read(case.expected))
            Draft202012Validator.check_schema(expected)
            validator = Draft202012Validator(expected)
            for slot in ("parent", "candidate"):
                method = getattr(protocol, slot)
                resources = self.materials.read_method(method)
                input_body = {
                    "protocol": protocol.id,
                    "case": case.id,
                    "method": method.model_dump(mode="json"),
                    "resources": resources,
                    "case_input": case_input,
                }
                input_ref = self._archive(input_body)
                key = canonical_hash([protocol.payload_hash, case.id, slot])
                attempt = self.attempts.read_attempt(key)
                if attempt is None:
                    if old is not None:
                        raise ValueError("behavior_receipt_attempt_missing")
                    attempt = BehaviorAttempt(
                        schema_version="skill-behavior-attempt.v1",
                        key=key,
                        protocol=ref(protocol),
                        case_id=case.id,
                        method=method,
                        method_slot=slot,
                        input_original=input_ref,
                        expected_original=case.expected,
                        recipe_hash=protocol.recipe_hash,
                        mode=mode,
                        created_at=self.clock(),
                    )
                    attempt, dispatch = self.attempts.append_attempt(attempt)
                else:
                    dispatch = False
                if (
                    attempt.protocol != ref(protocol)
                    or attempt.method != method
                    or attempt.case_id != case.id
                    or (attempt.method_slot != slot)
                    or (attempt.input_original != input_ref)
                    or (attempt.expected_original != case.expected)
                    or (attempt.recipe_hash != protocol.recipe_hash)
                    or (attempt.mode != mode)
                ):
                    raise ValueError("behavior_attempt_binding_mismatch")
                attempt_ref = ref(attempt, "key")
                output = self.attempts.read_output(key)
                if output is None:
                    if not dispatch:
                        raise ValueError("behavior_provider_completion_unconfirmed")
                    result = yield input_body
                    if (
                        self.runtime.recipe_hash != protocol.recipe_hash
                        or self.runtime.mode != mode
                    ):
                        raise ValueError("behavior_runtime_changed")
                    if self.runtime.mode == "live_model" and result.runtime_id.lower().startswith(
                        ("fake", "replay", "controlled")
                    ):
                        raise ValueError("behavior_live_result_required")
                    output = BehaviorOutput(
                        schema_version="skill-behavior-output.v1",
                        attempt=attempt_ref,
                        actual_output=self._archive(result.payload),
                        runtime_original=self._archive(
                            result.original
                            or {
                                "mode": mode,
                                "runtime_id": result.runtime_id,
                                "payload": result.payload,
                            }
                        ),
                        completed_at=result.completed_at,
                        runtime_id=result.runtime_id,
                        mode=mode,
                    )
                    output = self.attempts.append_output(key, output)
                if (
                    output.attempt != attempt_ref
                    or output.mode != attempt.mode
                    or output.completed_at < attempt.created_at
                    or (output.completed_at > self.clock())
                ):
                    raise ValueError("behavior_output_binding_mismatch")
                actual = self._read(output.actual_output)
                runtime_original = self._read(output.runtime_original)
                if (
                    runtime_original.get("payload") != actual
                    or runtime_original.get("mode") != mode
                ):
                    raise ValueError("behavior_runtime_original_mismatch")
                passed[slot] = passed[slot] and validator.is_valid(actual)
                attempt_refs.append(attempt_ref)
                output_refs.append(
                    Ref(ref=key, hash=canonical_hash(output.model_dump(mode="json")))
                )
        status = (
            "control_only"
            if self.runtime.mode == "controlled_model"
            else "behavior_passed"
            if passed["candidate"]
            else "behavior_failed"
        )
        body = dict(
            schema_version="skill-behavior-receipt.v1",
            id="behavior_" + protocol.payload_hash,
            protocol=ref(protocol).model_dump(mode="json"),
            package_stage=protocol.package_stage.model_dump(mode="json"),
            attempts=[r.model_dump(mode="json") for r in attempt_refs],
            outputs=[r.model_dump(mode="json") for r in output_refs],
            candidate_passed=passed["candidate"],
            parent_passed=passed["parent"],
            execution_kind=self.runtime.mode,
            status=status,
            completed_at=old.completed_at if old else self.clock(),
            payload_hash="0" * 64,
        )
        parsed = BehaviorReceipt.model_validate(body)
        body = parsed.model_dump(mode="json", exclude={"payload_hash"})
        receipt = BehaviorReceipt.model_validate(body | {"payload_hash": canonical_hash(body)})
        if old is not None and old != receipt:
            raise ValueError("behavior_receipt_original_mismatch")
        return self.attempts.append_receipt(protocol_id, receipt)


def require_normal_trial_behavior(receipt: BehaviorReceipt):
    body = receipt.model_dump(mode="json", exclude={"payload_hash"})
    if canonical_hash(body) != receipt.payload_hash:
        raise ValueError("behavior_receipt_hash_mismatch")
    if (
        receipt.status != "behavior_passed"
        or receipt.execution_kind != "live_model"
        or (not receipt.candidate_passed)
    ):
        raise ValueError("behavior_normal_trial_not_qualified")
