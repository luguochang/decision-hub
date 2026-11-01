"""Real OKX Adapter/Router/Gateway/capture; only HTTP transport is controlled."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.fact_store import research_fact_payload_hash
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
)
from packages.provider_adapters.market.okx_research import OKXDerivativesResearchAdapter
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)
from packages.provider_adapters.routing import ProviderCapabilityRouter

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_open_interest_uses_admitted_base_and_rejects_foreign_row(monkeypatch, tmp_path):
    at = datetime.now(UTC)
    published = at - timedelta(seconds=1)
    value = "12345.6700"
    response_instrument = "BTC-USDT-SWAP"
    requests = []
    authored_bodies = []

    async def response(_transport, request):
        assert request.method == "GET" and request.url.host == "www.okx.com"
        assert request.url.path == "/api/v5/public/open-interest"
        assert request.url.params["instType"] == "SWAP"
        requests.append(str(request.url))
        body = json.dumps(
            {
                "code": "0",
                "data": [
                    {
                        "instId": response_instrument,
                        "oiCcy": value,
                        "ts": str(int(published.timestamp() * 1000)),
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()
        authored_bodies.append(body)
        return httpx.Response(
            200, request=request, content=body, headers={"content-type": "application/json"}
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    pack = yaml.safe_load((ROOT / "packs/crypto_macro/tools/bindings.yaml").read_text("utf-8"))
    manifest = next(
        ResearchCapabilityManifest.model_validate(item)
        for item in pack["capabilities"]
        if item["capability_id"] == "market.crypto_derivatives"
    )
    route = next(item for item in manifest.provider_routes if item.provider_id == "okx-public")
    router = ProviderCapabilityRouter(
        capability_id=manifest.capability_id,
        routes=(route,),
        adapters={"okx-public": OKXDerivativesResearchAdapter()},
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest], [router], enabled_capabilities=[manifest.capability_id]
    )
    archive = RawBodyArchive(tmp_path / "raw")
    evidence = {
        "classification": "controlled_HTTP_engineering_gate_no_native_qualification",
        "runs": [],
    }
    delivered = []
    failures = []
    batches = []
    try:
        for label, requested, supplied in (
            ("btc", "BTC-USDT-SWAP", "BTC-USDT-SWAP"),
            ("eth", "ETH-USDT-SWAP", "ETH-USDT-SWAP"),
            ("foreign", "ETH-USDT-SWAP", "BTC-USDT-SWAP"),
        ):
            response_instrument = supplied
            query = ResearchCapabilityQuery.model_validate(
                dict(
                    schema_version="research-capability-query.v1",
                    request_id=f"oi-unit-{label}",
                    capability_id=manifest.capability_id,
                    requirement_id="derivatives_crowding",
                    query="Read open interest in its original base-currency unit",
                    target_url=None,
                    symbols=[requested],
                    fields=["open_interest"],
                    allowed_domains=["okx.com"],
                    max_results=1,
                    max_cost_usd=0.1,
                    research_session_id="oi-unit-isolated",
                    round=1,
                    mode="live",
                    observed_at=at,
                    cutoff_at=at + timedelta(minutes=5),
                )
            )
            batch = CaptureBatch(label, "actual-adapter-capture", archive, lambda _: None)
            result = error = None
            with capture_scope(batch):
                try:
                    result = await gateway.execute(query)
                except ResearchCapabilityError as caught:
                    error = caught
            delivered.append(result)
            failures.append(error)
            batches.append(batch)
            evidence["runs"].append(
                {
                    "case": label,
                    "query": query.model_dump(mode="json"),
                    "result": result.model_dump(mode="json") if result is not None else None,
                    "error": error.provenance().model_dump(mode="json") if error else None,
                    "captures": batch.captures,
                    "authored_response_sha256": hashlib.sha256(authored_bodies[-1]).hexdigest(),
                }
            )
            assert len(batch.captures) == 1
            capture = batch.captures[0]
            raw = archive.read(capture["body_ref"])
            assert raw == authored_bodies[-1]
            assert capture["body_hash"] == hashlib.sha256(raw).hexdigest()
            assert capture["status_code"] == 200 and capture["error_code"] is None
            assert json.loads(raw)["data"][0]["oiCcy"] == value
            if result is not None:
                assert len(result.evidence_candidates) == len(result.facts) == 1
                fact = result.facts[0]
                candidate = result.evidence_candidates[0]
                assert fact.evidence_id == candidate.evidence_id
                assert str(candidate.source_url) == capture["url"] == requests[-1]
                assert json.loads(candidate.excerpt)["value"] == json.loads(raw)["data"][0]
                assert fact.instrument == requested and fact.value == value
                assert fact.published_at.timestamp() == int(published.timestamp() * 1000) / 1000
                assert fact.received_at >= fact.published_at
                assert result.cost_usd == 0.0
                assert len(result.provider_attempts) == 1
                assert result.provider_attempts[0].status == "succeeded"
                assert result.provider_attempts[0].cost_usd == 0.0
                semantic_fields = (
                    "requirement_id",
                    "metric_family",
                    "field",
                    "instrument",
                    "venue",
                    "value",
                    "unit",
                    "window_start_at",
                    "window_end_at",
                    "event_offset",
                    "published_at",
                    "source_id",
                    "independence_group",
                    "delay_class",
                    "payload_schema_ref",
                    "attributes",
                )
                assert fact.payload_hash == research_fact_payload_hash(
                    **{name: getattr(fact, name) for name in semantic_fields}
                )
        assert len(requests) == 3
        assert failures[:2] == [None, None]
        assert delivered[0].facts[0].unit == "btc"
        assert delivered[1].facts[0].unit == "eth"
        assert delivered[2] is None and failures[2] is not None
        assert failures[2].error_code == "research_market_output_invalid"
        assert len(failures[2].provider_attempts) == 1
        assert failures[2].provider_attempts[0].status == "failed"
        assert failures[2].provider_attempts[0].error_code == "research_market_output_invalid"
        assert batches[2].captures[0]["body_hash"] == batches[0].captures[0]["body_hash"]
        evidence["checks"] = [
            "btc_btc_eth_eth",
            "foreign_instId_rejected",
            "original_bytes_value_hash_preserved",
            "actual_router_attempt_and_capture_lineage",
            "PIT_cost_count_unchanged",
        ]
    finally:
        path = Path(os.getenv("OKX_OI_EVIDENCE", tmp_path / "oi-evidence.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
