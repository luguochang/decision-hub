from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from mcp_types import CallToolResult

from apps.research_mcp.main import create_server

CAPABILITIES = (
    "official.macro,market.cross_asset,market.crypto_derivatives,market.crypto_crowding"
)


def _arguments(case_id: str) -> dict[str, object]:
    now = datetime.now(UTC)
    common: dict[str, object] = {
        "request_id": f"product-c4-live:{case_id}",
        "research_session_id": "product-c4-live-canary",
        "round": 1,
        "mode": "live",
        "observed_at": now.isoformat(),
        "cutoff_at": (now + timedelta(seconds=30)).isoformat(),
        "max_results": 10,
        "max_cost_usd": 0.0,
    }
    cases: dict[str, dict[str, object]] = {
        "official_feed": {
            "capability_id": "official.macro",
            "requirement_id": "event.identity",
            "query": "latest Federal Reserve official speech entry",
            "target_url": "https://www.federalreserve.gov/feeds/speeches.xml",
            "symbols": [],
            "fields": [],
            "allowed_domains": ["federalreserve.gov"],
        },
        "cross_asset": {
            "capability_id": "market.cross_asset",
            "requirement_id": "macro.transmission",
            "query": "latest PIT-eligible US 2Y, 10Y and broad dollar observations",
            "target_url": None,
            "symbols": ["DGS2", "DGS10", "DTWEXBGS"],
            "fields": [],
            "allowed_domains": ["fred.stlouisfed.org"],
        },
        "crypto_spot": {
            "capability_id": "market.crypto_derivatives",
            "requirement_id": "crypto.spot",
            "query": "current BTCUSDT spot price and volume",
            "target_url": None,
            "symbols": ["BTCUSDT"],
            "fields": ["spot_price", "spot_volume"],
            "allowed_domains": ["api.coinex.com"],
        },
        "crypto_derivatives": {
            "capability_id": "market.crypto_derivatives",
            "requirement_id": "crypto.derivatives",
            "query": "current BTCUSDT funding, OI, mark, index and basis",
            "target_url": None,
            "symbols": ["BTCUSDT"],
            "fields": [
                "funding_rate",
                "open_interest",
                "mark_price",
                "index_price",
                "basis",
            ],
            "allowed_domains": ["api.coinex.com"],
        },
        "crypto_crowding": {
            "capability_id": "market.crypto_crowding",
            "requirement_id": "crypto.derivatives",
            "query": "current BTCUSDT order-book imbalance proxy",
            "target_url": None,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": ["crowding_signal", "book_imbalance"],
            # Exercise the Pack route, including CoinEx fallback when OKX is
            # rate-limited or unavailable; this is not an OKX-only probe.
            "allowed_domains": ["okx.com", "api.coinex.com"],
        },
    }
    return {**common, **cases[case_id]}


async def _execute(server: Any, case_id: str) -> dict[str, object]:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(35):
            raw = await server.call_tool(
                "research_capability_execute",
                _arguments(case_id),
            )
        result = raw if isinstance(raw, CallToolResult) else CallToolResult.model_validate(raw)
        payload = result.structured_content
        if result.is_error or not isinstance(payload, dict):
            raise RuntimeError("capability_tool_failed")
        evidence = payload.get("evidence_candidates")
        if not isinstance(evidence, list) or not evidence:
            raise RuntimeError("capability_returned_no_evidence")
        facts = payload.get("facts")
        if not isinstance(facts, list) or not facts:
            raise RuntimeError("capability_returned_no_facts")
        now = datetime.now(UTC)
        ages: list[float] = []
        domains: set[str] = set()
        authorities: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise RuntimeError("capability_evidence_shape_invalid")
            authorities.add(str(item.get("authority")))
            hostname = urlsplit(str(item.get("source_url"))).hostname
            if hostname:
                domains.add(hostname)
            anchor = item.get("published_at") or item.get("observed_at")
            if isinstance(anchor, str):
                ages.append(max(0.0, (now - datetime.fromisoformat(anchor)).total_seconds()))
        metric_families = sorted(
            {str(item.get("metric_family")) for item in facts if isinstance(item, dict)}
        )
        delay_classes = sorted(
            {str(item.get("delay_class")) for item in facts if isinstance(item, dict)}
        )
        event_offsets = sorted(
            {
                str(item["event_offset"])
                for item in facts
                if isinstance(item, dict) and item.get("event_offset") is not None
            }
        )
        attempts = payload.get("provider_attempts")
        return {
            "case_id": case_id,
            "status": "passed",
            "provider": payload.get("provider"),
            "evidence_count": len(evidence),
            "fact_count": len(facts),
            "metric_families": metric_families,
            "delay_classes": delay_classes,
            "event_offsets": event_offsets,
            "provider_attempt_count": len(attempts) if isinstance(attempts, list) else 0,
            "authorities": sorted(authorities),
            "source_domains": sorted(domains),
            "max_age_seconds": round(max(ages), 3) if ages else None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "cost_usd": payload.get("cost_usd"),
            "error_code": None,
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "status": "failed",
            "provider": None,
            "evidence_count": 0,
            "fact_count": 0,
            "metric_families": [],
            "delay_classes": [],
            "event_offsets": [],
            "provider_attempt_count": 0,
            "authorities": [],
            "source_domains": [],
            "max_age_seconds": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "cost_usd": None,
            "error_code": str(getattr(exc, "error_code", type(exc).__name__)),
        }


async def _run() -> dict[str, object]:
    if os.getenv("DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY") != "1":
        raise RuntimeError(
            "set DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY=1 to authorize read-only network use"
        )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    previous_capabilities = os.environ.get("DECISION_HUB_RESEARCH_CAPABILITIES")
    previous_durable_progress = os.environ.get(
        "DECISION_HUB_RESEARCH_DURABLE_PROGRESS"
    )
    os.environ["DECISION_HUB_RESEARCH_CAPABILITIES"] = CAPABILITIES
    # This standalone canary verifies provider adapters and network facts. A
    # product Run uses the default durable gateway and must supply a real DSH
    # Session link; using that boundary here would stop before any adapter call.
    os.environ["DECISION_HUB_RESEARCH_DURABLE_PROGRESS"] = "0"
    try:
        server = create_server()
        cases = await asyncio.gather(
            *(
                _execute(server, case_id)
                for case_id in (
                    "official_feed",
                    "cross_asset",
                    "crypto_spot",
                    "crypto_derivatives",
                    "crypto_crowding",
                )
            )
        )
    finally:
        if previous_capabilities is None:
            os.environ.pop("DECISION_HUB_RESEARCH_CAPABILITIES", None)
        else:
            os.environ["DECISION_HUB_RESEARCH_CAPABILITIES"] = previous_capabilities
        if previous_durable_progress is None:
            os.environ.pop("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", None)
        else:
            os.environ[
                "DECISION_HUB_RESEARCH_DURABLE_PROGRESS"
            ] = previous_durable_progress
    return {
        "schema_version": "research-fact-capability-canary.v1",
        "status": "passed" if all(item["status"] == "passed" for item in cases) else "failed",
        "cases": cases,
        "semantic_scope": "current_snapshot_adapter_health_only",
        "live_provider_blockers": [
            "macro.cross_asset_intraday",
            "macro.expectation_pricing",
        ],
        "note": (
            "A passed public canary proves typed adapter reachability only; empty event_offsets "
            "do not satisfy event-window requirements."
        ),
    }


def main() -> int:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
