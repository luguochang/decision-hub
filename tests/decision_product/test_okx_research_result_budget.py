"""The actual BTC16 three-field query retains bounded original OKX evidence."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from packages.contracts_py.decision_hub_contracts import (
    EvidenceRequirement,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    observe_capability_adapter_return,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import assess_evidence_sufficiency
from packages.provider_adapters.market.okx_research import OKXDerivativesResearchAdapter
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)
from packages.provider_adapters.routing import ProviderCapabilityRouter

ROOT = Path(__file__).resolve().parents[2]
ACTUAL_CALL = (
    "call_86732f3af6cf434d8f2503dff34d1c02|fc_0b3422d4b208054f016ad7476186f4819b93eb20ab9c99d3bc"
)


@pytest.mark.asyncio
async def test_actual_three_fields_respect_count_and_keep_original_links_and_gaps(
    monkeypatch, tmp_path
):
    at = datetime.now(UTC)
    query = ResearchCapabilityQuery.model_validate(
        dict(
            schema_version="research-capability-query.v1",
            request_id=ACTUAL_CALL,
            capability_id="market.crypto_derivatives",
            requirement_id="derivatives_crowding",
            query="BTC16 actual OKX funding, open interest and basis query",
            target_url=None,
            symbols=["BTC-USDT-SWAP"],
            fields=["funding_rate", "open_interest", "basis"],
            allowed_domains=["okx.com"],
            max_results=2,
            max_cost_usd=0.1,
            research_session_id="btc16-count-isolated-fixture",
            round=1,
            mode="live",
            observed_at=at,
            cutoff_at=at + timedelta(minutes=5),
        )
    )
    fixture_rows = {
        "/api/v5/public/funding-rate": {"fundingRate": "0.0001"},
        "/api/v5/public/open-interest": {"oiCcy": "1000"},
        "/api/v5/public/mark-price": {"markPx": "101000"},
        "/api/v5/market/index-tickers": {"idxPx": "100000"},
    }
    requested_urls = []

    async def response(transport, request):
        assert request.method == "GET" and request.url.host == "www.okx.com"
        requested_urls.append(str(request.url))
        row = {
            "instId": request.url.params["instId"],
            "ts": str(int((at - timedelta(seconds=1)).timestamp() * 1000)),
            **fixture_rows[request.url.path],
        }
        return httpx.Response(200, request=request, json={"code": "0", "data": [row]})

    # Real adapter/default CapturingAsyncClient, Router and Gateway. Only the
    # HTTP transport is controlled; no original, validator or authority stub.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    pack = yaml.safe_load((ROOT / "packs/crypto_macro/tools/bindings.yaml").read_text("utf-8"))
    manifest = next(
        ResearchCapabilityManifest.model_validate(value)
        for value in pack["capabilities"]
        if value["capability_id"] == query.capability_id
    )
    route = next(item for item in manifest.provider_routes if item.provider_id == "okx-public")
    router = ProviderCapabilityRouter(
        capability_id=query.capability_id,
        routes=(route,),
        adapters={"okx-public": OKXDerivativesResearchAdapter()},
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest], [router], enabled_capabilities=[query.capability_id]
    )
    archive = RawBodyArchive(tmp_path / "raw")
    path = Path(os.getenv("OKX_COUNT_EVIDENCE", tmp_path / "evidence.json"))
    evidence = {
        "classification": (
            "actual-native-query-shape_with_authored_HTTP_fixture_"
            "not_native_or_financial_qualification"
        ),
        "original_native_call_id": ACTUAL_CALL,
        "original_native_error_seq": 926,
        "query": query.model_dump(mode="json"),
        "runs": [],
        "checks": [],
    }

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), "utf-8")

    async def invoke(maximum):
        batch = CaptureBatch("okx-count-fixture", "unchanged-real-capture", archive, lambda x: None)
        request = query.model_copy(update={"max_results": maximum})
        start = len(requested_urls)
        run = {"max_results": maximum}
        try:
            with capture_scope(batch), observe_capability_adapter_return() as collector:
                try:
                    delivered = await gateway.execute(request)
                finally:
                    if collector.observation is not None:
                        original = collector.observation.result()
                        run["physical_adapter_return"] = original.model_dump(mode="json")
                        run["physical_return_hash"] = collector.observation.result_hash
            run["delivered_result"] = delivered.model_dump(mode="json")
            return delivered, batch
        except ResearchCapabilityError as error:
            run["error"] = error.provenance().model_dump(mode="json")
            raise
        finally:
            run["requested_urls"] = requested_urls[start:]
            run["captures"] = batch.captures
            run["original_raw_bodies"] = {
                item["body_ref"]: archive.read(item["body_ref"]).decode()
                for item in batch.captures
                if item["body_ref"]
            }
            evidence["runs"].append(run)
            save()

    partial, partial_batch = await invoke(2)
    assert len(partial.evidence_candidates) == len(partial.facts) == 2
    assert [fact.field for fact in partial.facts] == ["funding_rate", "open_interest"]
    assert len(partial_batch.captures) == 2
    assert all("mark-price" not in url and "index-tickers" not in url for url in requested_urls)
    omitted = [{"symbol": "BTC-USDT-SWAP", "field": "basis"}]
    for fact in partial.facts:
        assert fact.attributes["query_result_status"] == "partial_max_results"
        assert fact.attributes["omitted_job_count"] == "1"
        assert json.loads(fact.attributes["omitted_jobs_json"]) == omitted
    for candidate in partial.evidence_candidates:
        assert json.loads(candidate.excerpt)["query_coverage"]["omitted_jobs"] == omitted
    evidence["checks"].append("partial_is_explicit_and_omitted_jobs_not_fetched")

    requirement = EvidenceRequirement(
        requirement_id=query.requirement_id,
        description="All three derivatives fields are mandatory for this coverage check.",
        importance="hard",
        source_priority=["exchange"],
        authority_floor="exchange",
        preferred_capabilities=[query.capability_id],
        freshness_seconds=600,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.5,
        required_fields=["funding_rate", "open_interest", "basis"],
        accepted_metric_families=["crypto.derivatives"],
    )

    def coverage(result):
        # Coverage's accepted-input projection is separate from authentication;
        # every original has already passed the actual unchanged Gateway.
        return assess_evidence_sufficiency(
            [requirement],
            [
                c.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
                for c in result.evidence_candidates
            ],
            facts=[f.model_copy(update={"quality": "accepted"}) for f in result.facts],
            cutoff_at=query.cutoff_at,
        )

    insufficient = coverage(partial)
    assert insufficient.status == "insufficient"
    assert insufficient.gaps[0].reason_code == "semantic_mismatch"
    assert not insufficient.covered_requirement_ids
    evidence["checks"].append("mandatory_missing_basis_remains_real_coverage_gap")
    full, full_batch = await invoke(3)
    assert len(full.evidence_candidates) == len(full.facts) == 3
    assert [fact.field for fact in full.facts] == ["funding_rate", "open_interest", "basis"]
    assert len(full_batch.captures) == 4  # basis keeps both mark and index originals.
    assert coverage(full).status == "sufficient"
    evidence["checks"].append("max3_retains_all_three_fields_and_both_basis_inputs")
    for result, batch in ((partial, partial_batch), (full, full_batch)):
        assert result.provider == "okx-public" and result.cost_usd == 0.0
        assert len(result.provider_attempts) == 1
        attempt = result.provider_attempts[0]
        assert attempt.provider_id == "okx-public" and attempt.status == "succeeded"
        assert attempt.cost_usd == 0.0
        candidates = {c.evidence_id: c for c in result.evidence_candidates}
        captures = {c["url"]: c for c in batch.captures}
        for fact in result.facts:
            original_candidate = candidates[fact.evidence_id]
            capture = captures[str(original_candidate.source_url)]
            raw = archive.read(capture["body_ref"])
            assert hashlib.sha256(raw).hexdigest() == capture["body_hash"]
            assert capture["status_code"] == 200
            row = json.loads(raw)["data"][0]
            assert row["instId"] in {"BTC-USDT-SWAP", "BTC-USDT"}
        gateway._validate_result(
            query.model_copy(update={"max_results": len(candidates)}),
            result,
            frozenset({"okx.com"}),
        )
    evidence["checks"].append(
        "all_fact_links_original_URLs_capture_bytes_cost_and_attempts_verified"
    )
    for mutation, reason in (
        ({"evidence_id": "foreign"}, "research_fact_evidence_not_found"),
        ({"value": "999"}, "research_fact_hash_mismatch"),
        ({"attributes": {}}, "research_fact_hash_mismatch"),
    ):
        altered = partial.model_copy(
            update={"facts": [partial.facts[0].model_copy(update=mutation)]}
        )
        with pytest.raises(ResearchCapabilityError) as rejected:
            gateway._validate_result(query, altered, frozenset({"okx.com"}))
        assert rejected.value.error_code == reason
    original_capture = partial_batch.captures[0]
    raw_path = archive.root / original_capture["body_hash"][:2] / original_capture["body_hash"]
    saved = raw_path.read_bytes()
    try:
        raw_path.write_bytes(saved + b" ")
        with pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
            archive.read(original_capture["body_ref"])
    finally:
        raw_path.write_bytes(saved)
    assert archive.read(original_capture["body_ref"]) == saved
    evidence["checks"].append("lineage_fact_metadata_hash_and_real_raw_tamper_rejected")
    save()
