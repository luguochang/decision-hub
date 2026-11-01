"""Bounded, read-only concurrent checks against the local product API."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8047")
    parser.add_argument("--run-id")
    parser.add_argument("--rounds", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if target.hostname not in {"localhost", "127.0.0.1", "::1"} or target.scheme != "http":
        parser.error("base-url must be a local HTTP API")
    if target.username or target.password or target.query or target.fragment:
        parser.error("base-url must not contain credentials or query parameters")
    if not 0 < args.timeout <= 60:
        parser.error("timeout must be between 0 and 60 seconds")
    paths = [
        "/health/live", "/v1/product/reports?limit=12&offset=0&unread_only=false",
        "/v1/research/runs?limit=8", "/v1/operations",
    ]
    if args.run_id:
        run = quote(args.run_id, safe="")
        paths.extend([f"/v1/research/runs/{run}", f"/v1/research/runs/{run}/observability"])

    def probe(task):
        round_id, path = task
        started = time.monotonic()
        result = {"round": round_id, "path": path, "at": datetime.now(UTC).isoformat()}
        try:
            with httpx.Client(trust_env=False, timeout=args.timeout) as client:
                response = client.get(args.base_url.rstrip("/") + path,
                                      headers={"X-Owner-Id": "owner"})
                result.update(status=response.status_code, bytes=len(response.content))
                if response.status_code == 200:
                    response.json()
        except (httpx.HTTPError, ValueError) as exc:
            result["error"] = type(exc).__name__
        result["seconds"] = round(time.monotonic() - started, 3)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for round_id in range(1, args.rounds + 1):
            results.extend(pool.map(probe, [(round_id, path) for path in paths]))
    failed = [item for item in results if item.get("status") != 200 or "error" in item]
    summary = {"requests": len(results), "failed": len(failed),
               "max_seconds": max(item["seconds"] for item in results)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "results": results}, indent=2),
                           encoding="utf-8")
    print(json.dumps(summary))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
