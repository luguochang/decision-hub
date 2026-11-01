"""Fixed-clock original OKX data through real normalization/PIT; no live network."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.fact_store import research_fact_payload_hash
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    _normalize_live_result,
    research_evidence_content_hash,
)
from packages.provider_adapters.market import okx_research
from packages.provider_adapters.research import raw_capture

ADAPTER_AT = datetime(2026, 10, 21, 0, 4, 44, 529094, tzinfo=UTC)
GATEWAY_AT = datetime(2026, 10, 21, 0, 4, 44, 530101, tzinfo=UTC)
PRICE_AT = datetime(2026, 10, 21, 0, 4, 43, 476000, tzinfo=UTC)


class FixedAdapterClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return ADAPTER_AT if tz is not None else ADAPTER_AT.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_fixed_provider_clock_preserves_originals_without_inventing_publication(
    monkeypatch, tmp_path
):
    rows = [
        {"instId": "BTC-USDT", "last": "67123.40", "vol24h": "1234.50", "ts": "1792541083476"},
        {"instId": "BTC-USDT", "last": "67123.40", "vol24h": "1234.50", "ts": "1792541087762"},
    ]
    bodies = []

    async def response(_transport, request):
        assert request.method == "GET" and request.url.host == "www.okx.com"
        assert request.url.path == "/api/v5/market/ticker"
        assert request.url.params["instId"] == "BTC-USDT"
        body = json.dumps({"code": "0", "data": [rows[len(bodies)]]}, separators=(",", ":")).encode()
        bodies.append(body)
        return httpx.Response(200, request=request, content=body, headers={"content-type": "application/json"})

    monkeypatch.setattr(okx_research, "datetime", FixedAdapterClock)
    monkeypatch.setattr(raw_capture, "datetime", FixedAdapterClock)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    query = ResearchCapabilityQuery.model_validate(
        dict(schema_version="research-capability-query.v1", request_id="fixed-okx-clock",
             capability_id="market.crypto_derivatives", requirement_id="crypto_spot_confirmation",
             query="Read the exact price and volume observations independently",
             target_url=None, symbols=["BTC-USDT-SWAP"], fields=["spot_price", "spot_volume"],
             allowed_domains=["okx.com"], max_results=2, max_cost_usd=0.1,
             research_session_id="clock-provenance-engineering-only", round=1, mode="live",
             observed_at=ADAPTER_AT - timedelta(seconds=1), cutoff_at=GATEWAY_AT + timedelta(seconds=1))
    )
    archive = raw_capture.RawBodyArchive(tmp_path / "raw")
    batch = raw_capture.CaptureBatch("fixed-clock", "actual-adapter-capture", archive, lambda _: None)
    with raw_capture.capture_scope(batch):
        original = await okx_research.OKXDerivativesResearchAdapter().execute(query)
    normalized = _normalize_live_result(original, GATEWAY_AT)
    ResearchCapabilityGatewayService._validate_result(query, normalized, frozenset({"okx.com"}))
    evidence = {"classification": "authored_HTTP_fixed_clock_engineering_not_native_approval",
                "query": query.model_dump(mode="json"), "physical_result": original.model_dump(mode="json"),
                "normalized_result": normalized.model_dump(mode="json"), "captures": batch.captures,
                "raw_bodies": [body.decode() for body in bodies]}
    evidence_path = Path(os.getenv("OKX_CLOCK_EVIDENCE", tmp_path / "evidence.json"))
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", "utf-8")

    price, volume = original.evidence_candidates
    price_fact, volume_fact = original.facts
    assert price.published_at == price_fact.published_at == PRICE_AT
    assert volume.published_at is None
    assert volume_fact.published_at is None
    assert price.evidence_id != volume.evidence_id
    assert price_fact.value == "67123.40" and volume_fact.value == "1234.50"
    assert price_fact.quality == volume_fact.quality == "candidate"
    assert price.freshness_status == volume.freshness_status == "unknown"
    clock = {"provider_timestamp_raw": "1792541087762",
             "adapter_received_at": ADAPTER_AT.isoformat(), "source_clock_status": "ahead_unverified"}
    assert json.loads(volume.excerpt)["source_clock"] == clock
    assert {key: volume_fact.attributes[key] for key in clock} == clock
    assert json.loads(price.excerpt)["value"] == rows[0]
    assert json.loads(volume.excerpt)["value"] == rows[1]
    assert len(batch.captures) == 2
    for capture, body in zip(batch.captures, bodies, strict=True):
        assert archive.read(capture["body_ref"]) == body
        assert capture["body_hash"] == hashlib.sha256(body).hexdigest()
    assert [item.received_at for item in original.evidence_candidates] == [ADAPTER_AT, ADAPTER_AT]
    assert normalized.completed_at == GATEWAY_AT
    assert all(item.observed_at == item.received_at == GATEWAY_AT for item in normalized.evidence_candidates)
    assert all(item.observed_at == item.received_at == GATEWAY_AT for item in normalized.facts)
    assert normalized.facts[1].attributes == volume_fact.attributes

    legacy_excerpt = json.dumps({"field": "spot_volume", "instrument": "BTC-USDT-SWAP", "value": rows[1]},
                                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    legacy_hash = research_evidence_content_hash(
        requirement_id=query.requirement_id, kind=volume.kind, authority=volume.authority,
        source_id=volume.source_id, source_url=str(volume.source_url), published_at=ADAPTER_AT,
        excerpt=legacy_excerpt, structured_payload_ref=None)
    assert volume.content_hash != legacy_hash
    fields = ("requirement_id", "metric_family", "field", "instrument", "venue", "value", "unit",
              "window_start_at", "window_end_at", "event_offset", "published_at", "source_id",
              "independence_group", "delay_class", "payload_schema_ref", "attributes")
    values = {name: getattr(volume_fact, name) for name in fields}
    assert volume_fact.payload_hash == research_fact_payload_hash(**values)
    assert volume_fact.payload_hash != research_fact_payload_hash(
        **{**values, "published_at": ADAPTER_AT, "attributes": {"provider_field": "spot_volume"}})

    future = GATEWAY_AT + timedelta(microseconds=1)
    for changed in (
        original.model_copy(update={"completed_at": future}),
        original.model_copy(update={"evidence_candidates": [price.model_copy(
            update={"observed_at": future, "received_at": future}), volume]}),
    ):
        with pytest.raises(ResearchCapabilityError) as rejected:
            _normalize_live_result(changed, GATEWAY_AT)
        assert rejected.value.error_code == "research_pit_violation"
    beyond_cutoff = _normalize_live_result(original, query.cutoff_at + timedelta(microseconds=1))
    with pytest.raises(ResearchCapabilityError) as rejected:
        ResearchCapabilityGatewayService._validate_result(query, beyond_cutoff, frozenset({"okx.com"}))
    assert rejected.value.error_code == "research_pit_violation"
