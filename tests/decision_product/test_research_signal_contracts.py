"""Research signal boundary regressions; synthetic fixtures, no live model or DB."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from datetime import UTC

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    AnalystClaim,
    ResearchDocument,
    ResearchDocumentVersion,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from packages.kernel.decision_hub_kernel.ports.research_signal import (
    ClaimSignal,
    ClaimSourceContext,
    MechanismReview,
    PublicationTime,
    ResearchActionCandidate,
    ResearchActionSynthesisSidecar,
    ResearchDecisionSynthesis,
    claim_signal_from_library,
    legacy_candidate_projection,
)
from tests.decision_product.test_action_contracts import ASOF, HASH, ROOT, SCOPE, UNTIL, condition


def action():
    return {
        "schema_version": "research-action-candidate.v1",
        "scope_id": SCOPE,
        "horizon": "24h",
        "as_of": ASOF,
        "valid_until": UNTIL,
        "next_review_at": "2026-10-13T01:00:00Z",
        "position_context": "flat",
        "action": "enter_long",
        "probability": {
            "target_id": "target-1",
            "kind": "unavailable",
            "value": None,
            "provenance_ref": None,
        },
        "entry": [condition()],
        "exit": [],
        "invalidation": [],
        "size_policy_ref": "size-1",
        "cost_policy_ref": "cost-1",
        "target_spec_ref": "target-1",
        "evidence_refs": ["evidence-1"],
        "claim_refs": ["claim_a"],
        "mechanism_review_ref": "mechanism-1",
        "reason": "Synthetic example of a conditional long, with unknown probability",
    }


def mechanism():
    return {
        "schema_version": "research-mechanism-review.v1",
        "review_id": "mechanism-1",
        "run_id": "run-1",
        "cutoff": ASOF,
        "scope_id": SCOPE,
        "horizon": "24h",
        "nodes": [
            {
                "id": "event",
                "kind": "expectation",
                "description": "Funding repriced",
                "refs": ["evidence-1"],
            },
            {"id": "flow", "kind": "observable", "description": "Position adjustment", "refs": []},
        ],
        "edges": [
            {
                "id": "edge-1",
                "from_node": "event",
                "to_node": "flow",
                "hypothesis": "A testable transmission hypothesis, not established causality",
                "lag": {"minimum_seconds": 60, "maximum_seconds": 3600},
                "support_refs": ["evidence-1"],
                "counter_refs": ["evidence-2"],
                "testable_feature_refs": [
                    {"feature": "close", "scope_id": SCOPE, "unit": "USDT/BTC"}
                ],
                "status": "hypothesis",
            }
        ],
        "alternative_refs": ["alternative-1"],
        "priced_in_status": "unknown",
        "priced_in_refs": [],
        "unresolved": ["Position-flow evidence unavailable"],
        "affected_plan_refs": [],
    }


def legacy():
    return {
        "schema_version": "research-synthesis-candidate.v1",
        "request_id": "request-1",
        "causal_case": None,
        "horizons": [
            {
                "horizon": "24h",
                "action": "long",
                "subjective_probability": 0.62,
                "probability_status": "uncalibrated",
                "evidence_refs": ["evidence-1"],
                "trigger": "Original text",
                "invalidation": "Original counterargument",
                "expires_at": "2026-10-14T08:00:00+08:00",
                "next_review_at": "2026-10-13T09:00:00+08:00",
                "missing_facts": ["flow unavailable"],
                "confidence_cap_reason": None,
            }
        ],
    }


def synthesis():
    return {
        "schema_version": "research-decision-synthesis.v1",
        "request_id": "request-1",
        "research_candidate_v1": legacy(),
        "action_intents": [action()],
        "mechanism_reviews": [mechanism()],
        "claim_signal_refs": ["claim_a"],
        "skill_consumption_refs": [],
    }


def locator():
    return {
        "output_id": "output-1",
        "role": "native-1",
        "schema_version": "native.v1",
        "payload_hash": HASH,
        "recorded_at": ASOF,
    }


def sidecar():
    return {
        "schema_version": "research-action-synthesis-sidecar.v1",
        "run_id": "run-1",
        "request_id": "request-1",
        "generation": 2,
        "request_hash": HASH,
        "context_hash": HASH,
        "scope_id": SCOPE,
        "horizon": "24h",
        "cutoff": ASOF,
        "producer_cutoff": ASOF,
        "selected_native_observation_ref": locator(),
        "selection_witness": {
            "kind": "tool_capture",
            "final_response_hash": None,
            "call_id": "call-1",
            "call_event_index": 0,
            "result_event_index": 1,
            "payload_occurrence": 0,
            "payload_path": ["result", None, 0],
            "call_event_hash": HASH,
            "result_event_hash": HASH,
            "call_seq": 1,
            "result_seq": 2,
            "origin_generation": None,
            "presence_generation": 2,
            "presence_ref": locator(),
        },
        "raw_candidate_hash": HASH,
        "projected_research_candidate_hash": HASH,
        "synthesis": synthesis(),
        "payload_hash": HASH,
    }


def source():
    return {
        "source_id": "source-news",
        "canonical_url": "https://example.org/article",
        "language": "en",
        "publication": {
            "precision": "unknown",
            "published_at": None,
            "window_start": None,
            "window_end": None,
            "display_text": "10/12/2026 12:03:12 PM",
            "source_timezone": None,
        },
        "first_seen_at": ASOF,
        "syndication_cluster": None,
        "rights_ref": "public-metadata-only",
        "extraction_version": "extract-v1",
        "content_kind": "editorial",
    }


def claim_signal():
    return {
        "schema_version": "research-claim-signal.v1",
        "claim_id": "claim_a",
        "version_id": "docver_a",
        "document_id": "doc_a",
        "supersedes_version_id": None,
        "source": source(),
        "author": None,
        "content_ref": "raw://fixture",
        "content_hash": HASH,
        "claim_type": "opinion",
        "stance": "mixed",
        "proposition": "Synthetic author proposition",
        "supporting_quote": "Synthetic quote",
        "quoted_evidence_refs": ["block-1"],
        "market_scope": "crypto_macro",
        "instrument_scopes": ["BTC"],
        "horizon": None,
        "claimed_conditions": [],
        "invalidation_conditions": [],
        "review_status": "candidate",
        "attribution_confidence": "unknown",
        "requires_independent_evidence": True,
    }


def library():
    document = ResearchDocument.model_validate(
        {
            "schema_version": "research-document.v1",
            "document_id": "doc_a",
            "title": "Fixture",
            "source_type": "public_web",
            "source_profile_id": None,
            "author": None,
            "market_scopes": ["crypto_macro"],
            "instrument_scopes": ["BTC"],
            "privacy_mode": "remote_allowed",
            "visibility": "shared",
            "created_at": ASOF,
            "latest_version_id": "docver_future",
            "version_count": 2,
        }
    )
    version = ResearchDocumentVersion.model_validate(
        {
            "schema_version": "research-document-version.v1",
            "version_id": "docver_a",
            "document_id": "doc_a",
            "original_file_ref": "raw://fixture",
            "original_file_hash": HASH,
            "status": "ready",
            "correction_status": "not_requested",
            "published_at": None,
            "observed_at": ASOF,
            "received_at": ASOF,
            "supersedes_version_id": None,
            "parse_receipt_ref": None,
            "corrected_content_ref": None,
            "block_count": 1,
            "claim_count": 1,
            "created_at": ASOF,
            "quality": {
                "layout_coverage": 1,
                "text_readability": 1,
                "table_integrity": 1,
                "image_coverage": 1,
                "numeric_suspicion": 0,
                "encoding_errors": 0,
                "locator_completeness": 1,
                "quality_status": "ready_for_correction",
                "issues": [],
            },
        }
    )
    claim = AnalystClaim.model_validate(
        {
            "schema_version": "analyst-claim.v1",
            "claim_id": "claim_a",
            "version_id": "docver_a",
            "claim_type": "opinion",
            "stance": "mixed",
            "claim_text": "Synthetic author proposition",
            "supporting_quote": "Synthetic quote",
            "locator_refs": ["block-1"],
            "market_scope": "crypto_macro",
            "instrument_scope": ["BTC"],
            "horizon": None,
            "conditions": [],
            "invalidation_conditions": [],
            "status": "candidate",
            "attribution_confidence": "unknown",
            "requires_independent_evidence": False,
            "created_at": ASOF,
            "reviewed_at": None,
        }
    )
    return claim, version, document


def test_valid_signals_roundtrip_preserve_unknown_and_real_locator_type():
    for model, raw in [
        (ClaimSignal, claim_signal()),
        (MechanismReview, mechanism()),
        (ResearchActionCandidate, action()),
        (ResearchDecisionSynthesis, synthesis()),
        (ResearchActionSynthesisSidecar, sidecar()),
    ]:
        row = model.model_validate(raw)
        assert model.model_validate_json(row.model_dump_json()) == row
        assert model.model_validate(row.model_dump(mode="python")) == row
    row = ResearchActionSynthesisSidecar.model_validate(sidecar())
    assert type(row.selected_native_observation_ref) is ObservedExecutionRef
    assert type(row.selection_witness.presence_ref) is ObservedExecutionRef
    assert row.cutoff.tzinfo is UTC
    assert row.synthesis.action_intents[0].probability.value is None


def test_original_candidate_serialization_and_hash_are_unchanged():
    original = ResearchSynthesisCandidate.model_validate(legacy())
    wrapped = ResearchDecisionSynthesis.model_validate(synthesis())
    projected = legacy_candidate_projection(wrapped)
    assert type(projected) is ResearchSynthesisCandidate
    assert projected.model_dump() == original.model_dump()
    assert projected.model_dump_json() == original.model_dump_json()
    assert (
        hashlib.sha256(projected.model_dump_json().encode()).digest()
        == hashlib.sha256(original.model_dump_json().encode()).digest()
    )
    assert "+08:00" in projected.model_dump_json()


def test_adapter_reuses_original_version_and_keeps_unknowns():
    claim, version, document = library()
    adapted = claim_signal_from_library(claim, version, document, ClaimSourceContext(**source()))
    assert adapted.model_dump(mode="json") == ClaimSignal(**claim_signal()).model_dump(mode="json")
    assert adapted.version_id != document.latest_version_id
    assert adapted.requires_independent_evidence is True
    assert adapted.horizon is None and adapted.author is None
    assert adapted.source.publication.published_at is None
    with pytest.raises(ValueError, match="version_mismatch"):
        claim_signal_from_library(
            claim.model_copy(update={"version_id": "docver_future"}),
            version,
            document,
            ClaimSourceContext(**source()),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_id", "invented"),
        ("published_at", ASOF),
        ("advice_qualification", "qualified"),
        ("position_context", "long"),
        ("as_of", "2026-10-13T00:00:00"),
        ("target_spec_ref", "x" * 257),
        ("horizon", "7d"),
        ("valid_until", ASOF),
    ],
)
def test_action_rejects_unsupported_shape_and_authority(field, value):
    with pytest.raises(ValidationError):
        ResearchActionCandidate.model_validate({**action(), field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "0.7"])
def test_probability_strict_inside_candidate(value):
    raw = action()
    raw["probability"].update(kind="subjective", value=value)
    with pytest.raises(ValidationError):
        ResearchActionCandidate.model_validate(raw)


def test_publication_precision_no_fabrication_or_unordered_window():
    raw = source()["publication"]
    with pytest.raises(ValidationError):
        PublicationTime.model_validate({**raw, "published_at": ASOF})
    PublicationTime.model_validate(
        {**raw, "precision": "interval", "window_start": ASOF, "window_end": UNTIL}
    )
    with pytest.raises(ValidationError):
        PublicationTime.model_validate(
            {**raw, "precision": "interval", "window_start": UNTIL, "window_end": ASOF}
        )
    claim, version, document = library()
    context = source()
    context["publication"].update(precision="exact", published_at=ASOF)
    with pytest.raises(ValueError, match="publication_mismatch"):
        claim_signal_from_library(claim, version, document, ClaimSourceContext(**context))


@pytest.mark.parametrize("case", ["node_duplicate", "endpoint", "self_edge", "lag", "priced_in"])
def test_mechanism_structure_rejects_incoherent_chain(case):
    raw = mechanism()
    if case == "node_duplicate":
        raw["nodes"].append(copy.deepcopy(raw["nodes"][0]))
    elif case == "endpoint":
        raw["edges"][0]["to_node"] = "absent"
    elif case == "self_edge":
        raw["edges"][0]["to_node"] = "event"
    elif case == "lag":
        raw["edges"][0]["lag"]["minimum_seconds"] = 4000
    else:
        raw["priced_in_status"] = "priced_in"
    with pytest.raises(ValidationError):
        MechanismReview.model_validate(raw)


@pytest.mark.parametrize("case", ["request", "claim", "review", "duplicate", "cutoff"])
def test_synthesis_binds_actions_to_same_frozen_context(case):
    raw = synthesis()
    if case == "request":
        raw["request_id"] = "another"
    elif case == "claim":
        raw["claim_signal_refs"] = []
    elif case == "review":
        raw["action_intents"][0]["mechanism_review_ref"] = "absent"
    elif case == "duplicate":
        raw["action_intents"].append(copy.deepcopy(raw["action_intents"][0]))
    else:
        raw["mechanism_reviews"][0]["cutoff"] = "2026-10-12T00:00:00Z"
    with pytest.raises(ValidationError):
        ResearchDecisionSynthesis.model_validate(raw)


@pytest.mark.parametrize(
    "case", ["generation", "bool_generation", "uri_locator", "future", "id", "target"]
)
def test_sidecar_declared_binding_is_closed(case):
    raw = sidecar()
    if case == "generation":
        raw["selection_witness"]["presence_generation"] = 1
    elif case == "bool_generation":
        raw["generation"] = True
    elif case == "uri_locator":
        raw["selected_native_observation_ref"] = "db://made-up-trusted"
    elif case == "future":
        raw["producer_cutoff"] = UNTIL
    elif case == "id":
        raw["run_id"] = "x" * 129
    else:
        raw["horizon"] = "30m"
    with pytest.raises(ValidationError):
        ResearchActionSynthesisSidecar.model_validate(raw)


def test_sidecar_can_expose_stale_producer_without_rewriting_asof():
    raw = sidecar()
    raw["cutoff"] = "2026-10-13T00:05:00Z"
    row = ResearchActionSynthesisSidecar.model_validate(raw)
    assert row.producer_cutoff < row.cutoff
    assert row.synthesis.action_intents[0].as_of == row.producer_cutoff


def test_zod_checks_canonical_shapes(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node required for generated contract verification"
    data = tmp_path / "signals.json"
    data.write_text(
        json.dumps({"action": action(), "sidecar": sidecar(), "claim": claim_signal()}),
        encoding="utf8",
    )
    generated = (ROOT / "packages/contracts_ts/src/generated/r2.ts").as_posix()
    runner = tmp_path / "signals.ts"
    output = tmp_path / "signals.cjs"
    runner.write_text(
        "import {readFileSync} from 'node:fs';\n"
        "import {researchActionCandidateV1Schema as a,researchActionSynthesisSidecarV1Schema as s,"
        f"researchClaimSignalV1Schema as c}} from {json.dumps(generated)};\n"
        f"const x=JSON.parse(readFileSync({json.dumps(str(data))},'utf8'));\n"
        "let count=0;function check(schema,data,expected){count++;"
        "if(schema.safeParse(data).success!==expected)throw Error('case '+count);}\n"
        "check(a,x.action,true);check(s,x.sidecar,true);check(c,x.claim,true);"
        "check(a,{...x.action,position_context:'long'},false);"
        "check(a,{...x.action,plan_id:'made-up'},false);"
        "check(a,{...x.action,published_at:'2026-10-13T00:00:00Z'},false);"
        "check(a,{...x.action,advice_qualification:'qualified'},false);"
        "check(a,{...x.action,as_of:'2026-10-13T00:00:00'},false);"
        "for(const value of [NaN,Infinity,-Infinity,true,'0.7'])check(a,{...x.action,probability:"
        "{...x.action.probability,kind:'subjective',value}},false);"
        "check(s,{...x.sidecar,generation:true},false);"
        "check(s,{...x.sidecar,run_id:'x'.repeat(129)},false);"
        "check(s,{...x.sidecar,selected_native_observation_ref:'db://fake'},false);"
        "check(c,{...x.claim,source:{...x.claim.source,publication:"
        "{...x.claim.source.publication,published_at:'2026-10-13T00:00:00Z'}}},false);"
        "console.log(JSON.stringify({count,passed:true}));",
        encoding="utf8",
    )
    bootstrap = (
        "const path=require('node:path');const [desk,entry,output]=process.argv.slice(1);"
        "const vite=require.resolve('vite/package.json',{paths:[desk]});"
        "const esbuild=require(require.resolve('esbuild',{paths:[path.dirname(vite)]}));"
        "esbuild.buildSync({entryPoints:[entry],outfile:output,bundle:true,platform:'node',"
        "format:'cjs',target:'node18',logLevel:'error'});require(output);"
    )
    result = subprocess.run(
        [node, "-e", bootstrap, str(ROOT / "apps/decision-desk"), str(runner), str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"count": 17, "passed": True}
