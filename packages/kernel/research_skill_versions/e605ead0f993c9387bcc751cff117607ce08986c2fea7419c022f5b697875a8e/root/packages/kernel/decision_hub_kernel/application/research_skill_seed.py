"""Author the finite BTC/ETH causal-synthesis package using actual DTO schemas.

No registration/activation, network or model call occurs here. Composition must
supply the authenticated actual catalog, adapter closure and frozen component.
"""

from __future__ import annotations

import copy
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    FeatureComponentInput,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import MechanismReview

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityResult
from packages.kernel.decision_hub_kernel.application.market_features import MarketFeatureSnapshot

from . import research_skill_compiler as c
from .research_skill_package import decoded, encoded, sha

SCHEMA = Path(__file__).resolve().parents[4] / "contracts/schemas/research_skill.schema.yaml"


def causal_seed(*, scope_id, version_id, surface_hash, feature_manifest):
    if scope_id not in {"crypto:spot:okx:BTC-USDT:trade:1m", "crypto:spot:okx:ETH-USDT:trade:1m"}:
        raise ValueError("skill_seed_scope_unsupported")
    if (
        feature_manifest.scope_id != scope_id
        or feature_manifest.component_ref != "spot.features.v1"
    ):
        raise ValueError("skill_seed_feature_manifest_mismatch")
    canonical = decoded(SCHEMA.read_bytes())
    definitions = canonical["$defs"]

    def contract(name):
        return c._schema(encoded({"$defs": definitions, **definitions["research_skill_" + name]}))

    gateway_input, gateway_output = (
        contract("gateway_intent_v1"),
        c._schema(encoded(ResearchCapabilityResult.model_json_schema())),
    )
    feature_input = c._schema(encoded(FeatureComponentInput.model_json_schema()))
    feature_output = c._schema(encoded(MarketFeatureSnapshot.model_json_schema()))
    hypothesis_output_raw = copy.deepcopy(definitions["research_skill_hypothesis_output_v1"])
    hypothesis_output_raw["properties"]["mechanism_review"] = c._schema(
        encoded(MechanismReview.model_json_schema())
    )
    hypothesis_output = c._schema(encoded({"$defs": definitions, **hypothesis_output_raw}))
    # Explicit types for this finite compiler; the canonical const constraints
    # stay identical. This is not general JSON Schema subtype inference.
    for name in ("schema_version", "horizon"):
        hypothesis_output["properties"][name]["type"] = "string"
    hypothesis_input = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"gateway_result": gateway_output, "feature_snapshot": feature_output},
        "required": ["gateway_result", "feature_snapshot"],
    }
    workflow_input = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"feature_input": feature_input},
        "required": ["feature_input"],
    }
    schemas = {
        "skill-causal-input.v1": workflow_input,
        "skill-gateway-intent.v1": gateway_input,
        "research-capability-result.v1": gateway_output,
        feature_manifest.input_contract: feature_input,
        feature_manifest.output_contract: feature_output,
        "skill-causal-hypothesis-input.v1": hypothesis_input,
        "research-skill-hypothesis-output.v1": hypothesis_output,
    }
    instrument = scope_id.split(":")[3]
    intent = {
        "requirement_id": "crypto_spot_confirmation",
        "query": f"Read current committed {instrument} spot price, 24-hour return and closed path",
        "target_url": None,
        "symbols": [instrument],
        "fields": ["spot_price", "return_24h", "path"],
        "allowed_domains": ["www.okx.com"],
        "max_results": 3,
    }
    workflow = {
        "schema_version": "skill-workflow.v2",
        "workflow_id": "causal-synthesis",
        "version_id": version_id,
        "scope_id": scope_id,
        "horizon": "24h",
        "input_schema": "skill-causal-input.v1",
        "output_schema": "research-skill-hypothesis-output.v1",
        "required_capability_surface_hash": surface_hash,
        "required_capabilities": ["spot_market_facts"],
        "nodes": [
            {
                "id": "spot_evidence",
                "op": "evidence.acquire",
                "depends_on": [],
                "input_schema": "skill-gateway-intent.v1",
                "output_schema": "research-capability-result.v1",
                "inputs": {
                    key: {"from": "literal", "value": value} for key, value in intent.items()
                },
                "capability_id": "spot_market_facts",
                "component_ref": None,
            },
            {
                "id": "features",
                "op": "component.compute",
                "depends_on": ["spot_evidence"],
                "input_schema": feature_manifest.input_contract,
                "output_schema": feature_manifest.output_contract,
                "inputs": {
                    key: {"from": "workflow_input", "pointer": "/feature_input/" + key}
                    for key in feature_input["required"]
                },
                "capability_id": None,
                "component_ref": "spot.features.v1",
            },
            {
                "id": "hypothesis",
                "op": "hypothesis.check",
                "depends_on": ["spot_evidence", "features"],
                "input_schema": "skill-causal-hypothesis-input.v1",
                "output_schema": "research-skill-hypothesis-output.v1",
                "inputs": {
                    "gateway_result": {
                        "from": "node_output",
                        "node_id": "spot_evidence",
                        "pointer": "",
                    },
                    "feature_snapshot": {
                        "from": "node_output",
                        "node_id": "features",
                        "pointer": "",
                    },
                },
                "capability_id": None,
                "component_ref": None,
            },
        ],
        "exports": {
            key: {"from": "node_output", "node_id": "hypothesis", "pointer": "/" + key}
            for key in hypothesis_output["required"]
        },
    }
    workflow_schema = {
        "$schema": canonical["$schema"],
        "$defs": definitions,
        "$ref": "#/$defs/research_skill_workflow_v2",
    }
    body = f"""# Causal synthesis for {instrument}, 24 hours

This immutable method is an unqualified research baseline. It may support a
conditional action; it never asserts a measured win rate or executes an order.

1. Load this exact version through decision_hub_skill. Delivery alone is not a
loaded receipt: the following real model input must contain this complete result.
2. After that loading step use ordinary decision_hub_research with capability_id
spot_market_facts and the exact business intent below. Preserve every failure or
refusal. Never replace an earlier failed matching call with a later favorable one.
3. Call skill execute_step for spot_evidence with the actual research_call_id.
The server binds the earliest actual matching call and re-reads admitted facts.
4. Call execute_step for features. The server reads the real closed MarketPath
at the Gateway result information time and uses the exact frozen feature version.
Never supply made-up candles, substitute another instrument or choose a latest
feature model. Missing, stale and unknown metrics stay explicit.
5. Call execute_step for hypothesis with a typed intermediate mechanism review.
Consume both actual parent outputs. For each used output provide the exact node
event/hash and a JSON pointer to its concrete use in the mechanism review.
Separate observed price/volume/path from causal hypotheses. Explain the main
and opposing transmission chains, lag, source timing, already-priced-in effects,
alternatives, missing evidence and what observation would invalidate the thesis.
Price movement alone cannot establish a future return or a macro cause.
6. The hypothesis tool first returns delivered. On the next ordinary model
dispatch its exact result is independently checked and consumed is recorded.
Call decision_hub_skill list to read stage_receipts and obtain the actual consumed
event ID/hash before the ordinary final synthesis cites it. The final full-wrapper
independent review still applies. Never invent a future consumption event hash.

The server-built feature input as_of is the actual accepted Gateway completed_at
information cutoff for those market inputs. Complete any additional macro/claim
research before submitting the hypothesis. Set intermediate output.as_of and
mechanism_review.cutoff to the SAME analysis instant, no earlier than the parent
input cutoff and no later than this actual hypothesis call or the Task deadline.
Reuse that exact reviewed mechanism in final synthesis; do not turn a quote time
or a future acquisition deadline into the analysis time. Individual quote
observed_at, candle close_at, evidence
received_at and Task deadline are different clocks and must remain separate.

Exact business intent: {encoded(intent).decode()}

Scope: {scope_id}; horizon: 24h. Macro claims require independent evidence and
counterevidence from the existing declared research routes. A missing entry basis
does not erase separately supported risk reduction. No arbitrary code, extra
model loop, implicit retry permission or financial qualification is granted.
"""
    payloads = {
        "SKILL.md": body.encode(),
        "workflow.json": encoded(workflow),
        "evaluation-protocol.json": encoded(
            {
                "schema_version": "skill-evaluation-protocol.v1",
                "method": "causal-synthesis",
                "scope_id": scope_id,
                "horizon": "24h",
                "required": [
                    "real_gateway_accepted_fact",
                    "frozen_feature_audit",
                    "actual_intermediate_emission",
                    "same_run_consumption",
                    "independent_readback",
                ],
                "financial_calibration": "unavailable",
                "qualification": "baseline_registered",
            }
        ),
        "schemas/workflow.schema.json": encoded(workflow_schema),
    }
    descriptors = []
    for index, (reference, schema) in enumerate(schemas.items()):
        resource_path = f"schemas/contract-{index}.json"
        data = encoded(schema)
        payloads[resource_path] = data
        pointers = [""]
        pointers.extend("/" + key for key in schema.get("properties", {}))
        if reference == "skill-causal-input.v1":
            pointers.extend("/feature_input/" + key for key in feature_input["required"])
        ports = []
        for pointer in pointers:
            # Optional output properties are not node bindings; declare only the
            # required ones plus the whole immutable typed result.
            try:
                field = c._field(schema, pointer)
            except c.SkillCompileError:
                continue
            types = tuple(sorted(c._types(field)))
            # Only these full typed payload links carry a scope declaration.
            scoped = (
                reference == "research-capability-result.v1"
                and pointer == ""
                or reference == feature_manifest.output_contract
                and pointer == ""
                or reference == "skill-causal-hypothesis-input.v1"
            )
            ports.append(c.PortType(pointer, types, None, scope_id if scoped else None))
        descriptors.append(c.SchemaDefinition(reference, resource_path, sha(data), tuple(ports)))
    resources = tuple(c.ResourceBytes(path, data, sha(data)) for path, data in payloads.items())
    return resources, tuple(descriptors), workflow
