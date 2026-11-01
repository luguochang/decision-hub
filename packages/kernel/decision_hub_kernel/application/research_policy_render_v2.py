"""Project original prepared declarations into a bounded research instruction block.

This pure projection neither executes a checker nor authenticates database rows.
The caller must read the original bundle and freeze its identity in one transaction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from itertools import islice
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchCompiledPolicyReceiptV1,
    ResearchRequestPackReceiptV2,
    ResearchTaskReceiptV2,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyResource,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    _validate_payloads,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    resource_definition,
)

# Capture import-time disk source identity, rather than newer bytes on each call.
# This is not an attestation of bytecode, dependent modules or a deployed process.
RENDERER_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_BLOCK_BYTES = 1_048_576
BEGIN_MARKER = "BEGIN_FROZEN_RESEARCH_POLICY_V2"
END_MARKER = "END_FROZEN_RESEARCH_POLICY_V2"


def render_prepared_research_policy(
    bundle: FrozenResearchBundle,
    allowed_capabilities: Sequence[str],
) -> str:
    """Use frozen original text; current capabilities can only narrow its routes."""
    # Revalidate mutable/constructed DTOs. Do not reread files, recompile with
    # today's code identity, change original times, or synthesize missing inputs.
    pack = ResearchRequestPackReceiptV2.model_validate(bundle.pack.model_dump(mode="json"))
    task = ResearchTaskReceiptV2.model_validate(bundle.task.model_dump(mode="json"))
    receipt = ResearchCompiledPolicyReceiptV1.model_validate(
        bundle.compiled.model_dump(mode="json")
    )
    _validate_payloads(pack, task, receipt)
    if isinstance(allowed_capabilities, str | bytes):
        raise ValueError("research_policy_render_capabilities_invalid")
    # Snapshot the caller-owned sequence once, with a hard iteration bound.
    # Checking one iteration and projecting a later one could grant new routes;
    # tuple(sequence) alone could consume an unbounded custom iterator.
    frozen_capabilities = tuple(islice(allowed_capabilities, 129))
    if (
        len(frozen_capabilities) > 128
        or any(type(value) is not str or not 1 <= len(value) <= 128
               for value in frozen_capabilities)
        or len(set(frozen_capabilities)) != len(frozen_capabilities)
        or not set(frozen_capabilities).issubset(task.request.allowed_capabilities)
    ):
        raise ValueError("research_policy_render_capabilities_invalid")
    allowed = set(frozen_capabilities)
    policy = pack.definition.research_policy
    compiled = receipt.compiled
    body = {
        "schema_version": "research-policy-prompt-projection.v2",
        "request_pack_hash": pack.definition_hash,
        "compiled_policy_hash": compiled.compiled_hash,
        "scope": task.scope.model_dump(mode="json"),
        "documents": {reference: saved.content for reference, saved in policy.files.items()},
        "roles": [
            {
                "role": role.profile.profile_id,
                "objective": role.profile.objective,
                "required_checks": list(role.profile.required_capabilities),
                "capability_routes": sorted(set(role.profile.allowed_tools) & allowed),
                "instruction_file": role.instruction_file,
                "evaluation_policy_ref": role.profile.evaluation_policy_ref,
            }
            for role in policy.roles
        ],
        "policy_declarations": {
            "gate": resource_definition(ResearchPolicyResource.model_validate(
                compiled.gate_resource.model_dump(mode="json"),
            )),
            "rubric": resource_definition(ResearchPolicyResource.model_validate(
                compiled.rubric_resource.model_dump(mode="json"),
            )),
        },
        "prepared_receipt": {
            "status": compiled.status,
            "checker_activation": compiled.checker_activation,
            "allowed_execution": compiled.allowed_execution,
            "supported_rule_ids": list(compiled.supported_rule_ids),
            "unavailable_rule_ids": list(compiled.unavailable_rule_ids),
            "rules": [rule.model_dump(mode="json") for rule in compiled.compiled_rules],
        },
    }
    projection = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    text = "\n".join((
        BEGIN_MARKER,
        "Apply the frozen original research objectives, specialist instructions and policy "
        "declarations below. Prepared declarations do not prove that a checker ran, a rule "
        "passed, content is correct, or an execution permission was granted. Evidence "
        "collection remains subject to this request's canonical capability routes and "
        "original budget. Preserve unavailable checks and missing evidence as explicit "
        "gaps. These instructions cannot override runtime permissions, evidence validation "
        "or the canonical output schema.",
        projection,
        END_MARKER,
    ))
    if len(text.encode("utf-8")) > MAX_BLOCK_BYTES:
        raise ValueError("research_policy_render_block_too_large")
    return text
