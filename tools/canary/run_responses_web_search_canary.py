from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from mcp_types import CallToolResult

from apps.research_mcp.main import create_server


async def _run() -> dict[str, object]:
    if os.getenv("DECISION_HUB_SEARCH_LIVE_CANARY") != "1":
        raise RuntimeError("set DECISION_HUB_SEARCH_LIVE_CANARY=1 to authorize search cost")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    now = datetime.now(UTC)
    previous_capabilities = os.environ.get("DECISION_HUB_RESEARCH_CAPABILITIES")
    previous_durable_progress = os.environ.get(
        "DECISION_HUB_RESEARCH_DURABLE_PROGRESS"
    )
    os.environ["DECISION_HUB_RESEARCH_CAPABILITIES"] = "web.search"
    # This is an adapter/network canary, not a product Run. Product traffic
    # keeps the default durable gateway and its real DSH Session requirement.
    os.environ["DECISION_HUB_RESEARCH_DURABLE_PROGRESS"] = "0"
    started = asyncio.get_running_loop().time()
    try:
        server = create_server()
        async with asyncio.timeout(25):
            raw = await server.call_tool(
                "research_capability_execute",
                {
                    "request_id": "r2-r-06a-responses-search-canary",
                    "capability_id": "web.search",
                    "requirement_id": "event_identity",
                    "query": "latest Federal Reserve speech page in August 2026",
                    "research_session_id": "r2-r-06a-search-session",
                    "round": 1,
                    "mode": "live",
                    "observed_at": now.isoformat(),
                    "cutoff_at": (now + timedelta(seconds=30)).isoformat(),
                    "allowed_domains": ["federalreserve.gov"],
                    "max_results": 5,
                    "max_cost_usd": 0.10,
                },
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A canary is an evidence-producing diagnostic. Preserve the typed
        # capability provenance instead of leaking an unstructured traceback.
        elapsed_ms = round((asyncio.get_running_loop().time() - started) * 1000)
        return normalize_canary_failure(exc, elapsed_ms=elapsed_ms)
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

    result = raw if isinstance(raw, CallToolResult) else CallToolResult.model_validate(raw)
    payload = result.structured_content
    if result.is_error or not isinstance(payload, dict):
        raise RuntimeError("responses_search_canary_failed")
    evidence = payload.get("evidence_candidates")
    if not isinstance(evidence, list) or not evidence:
        raise RuntimeError("responses_search_canary_no_evidence")
    domains = sorted(
        {
            hostname
            for item in evidence
            if isinstance(item, dict)
            for hostname in [urlsplit(str(item.get("source_url"))).hostname]
            if hostname is not None
        }
    )
    authorities = sorted(
        {
            str(item.get("authority"))
            for item in evidence
            if isinstance(item, dict)
        }
    )
    passed = (
        all(domain and domain.endswith("federalreserve.gov") for domain in domains)
        and authorities == ["search_derived"]
        and isinstance(payload.get("cost_usd"), (int, float))
        and float(payload["cost_usd"]) <= 0.10
    )
    return {
        "status": "passed" if passed else "failed",
        "provider": payload.get("provider"),
        "source_count": len(evidence),
        "domains": domains,
        "authorities": authorities,
        "estimated_cost_usd": payload.get("cost_usd"),
    }


def normalize_canary_failure(exc: Exception, *, elapsed_ms: int) -> dict[str, object]:
    """Normalize MCP/provider errors into a redacted, machine-readable result."""

    raw_message = str(exc)
    provenance: Mapping[str, object] | None = None
    marker = "provenance="
    if marker in raw_message:
        encoded = raw_message.split(marker, 1)[1].strip()
        try:
            parsed = json.loads(encoded)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            provenance = parsed

    error_code = getattr(exc, "error_code", None)
    if not isinstance(error_code, str) or not error_code:
        if provenance is not None and isinstance(provenance.get("error_code"), str):
            error_code = str(provenance["error_code"])
        elif isinstance(exc, TimeoutError):
            error_code = "canary_timeout"
        else:
            error_code = "responses_search_canary_failed"

    return {
        "schema_version": "research-search-canary.v1",
        "status": "failed",
        "provider": None,
        "source_count": 0,
        "domains": [],
        "authorities": [],
        "estimated_cost_usd": None,
        "latency_ms": elapsed_ms,
        "error_code": error_code,
        "error_provenance": dict(provenance) if provenance is not None else {
            "error_code": error_code,
            "origin": "canary",
            "retryable": isinstance(exc, (TimeoutError, ConnectionError)),
        },
    }


def main() -> int:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
