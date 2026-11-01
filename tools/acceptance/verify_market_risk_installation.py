"""One finite read-only installed API/Host census; never admit research or retry.

Use after standard startup. All responses are retained; failed or unknown data
remain explicit. UI, DSH terminal outcomes and future statistical results are
separate acceptance gates. This script does not load credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]
SCOPES = tuple(f"crypto:spot:okx:{instrument}:trade:1m" for instrument in ("BTC-USDT", "ETH-USDT"))


def write(path, body):
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def check(args, output):
    now = datetime.now(UTC)
    end = now.replace(second=0, microsecond=0)
    start = end - timedelta(days=1)
    routes = [
        ("api-ready", "http://127.0.0.1:8047/health/ready"),
        ("dsh-ready", "http://127.0.0.1:3097/decision-hub/v1/readiness"),
        ("scopes", "http://127.0.0.1:8047/v1/risk/scopes"),
    ]
    for index, scope in enumerate(SCOPES):
        base = "http://127.0.0.1:8047/v1/risk/scopes/" + quote(scope, safe="")
        for suffix in ("summary", "research", "path-trial", "predictions", "cases", "enhancements"):
            routes.append((f"asset{index}-{suffix}", base + "/" + suffix))
        routes.append((f"asset{index}-forecast-alarms", base + "/forecast-alarms?owner_id=owner"))
        routes.append(
            (
                f"asset{index}-timeline",
                base
                + "/timeline?"
                + str(
                    httpx.QueryParams(
                        {
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        }
                    )
                ),
            )
        )
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(
        trust_env=False, timeout=60, headers={"X-Owner-Id": "owner"}
    ) as client:

        async def read(name, url):
            async with semaphore:
                begun = datetime.now(UTC)
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    body = response.json()
                    write(output / (name + ".json"), body)
                    return {
                        "name": name,
                        "status_code": response.status_code,
                        "seconds": (datetime.now(UTC) - begun).total_seconds(),
                        "original_sha256": hashlib.sha256(response.content).hexdigest(),
                        "content_type": response.headers.get("Content-Type"),
                        "passed": True,
                    }
                except (httpx.HTTPError, ValueError) as error:
                    return {
                        "name": name,
                        "passed": False,
                        "error_type": type(error).__name__,
                        "detail": str(error)[:256],
                    }

        results = await asyncio.gather(*(read(name, url) for name, url in routes))
    passed = all(row["passed"] for row in results)
    checks = []
    if passed:

        def body(name):
            return json.loads((output / (name + ".json")).read_text(encoding="utf-8"))

        for index, scope in enumerate(SCOPES):
            summary = body(f"asset{index}-summary")
            timeline = body(f"asset{index}-timeline")
            current = body(f"asset{index}-path-trial")
            checks.append(
                {
                    "scope_id": scope,
                    "scope_matches": summary["scope_id"] == timeline["scope_id"] == scope,
                    "data_status": summary["data_status"],
                    "timeline_points": len(timeline["points"]),
                    "missing_ranges": len(timeline["missing_ranges"]),
                    "trial_registered": current["trial"] is not None,
                    "protocol_version": current["trial"]["protocol_version"]
                    if current["trial"]
                    else None,
                    "enhancement_routes": len(body(f"asset{index}-enhancements")["routes"]),
                    "saved_cases": len(body(f"asset{index}-cases")["items"]),
                    "experimental_alarms": len(body(f"asset{index}-forecast-alarms")["items"]),
                }
            )
        passed = all(
            row["scope_matches"]
            and row["trial_registered"]
            and row["protocol_version"] == "path-shadow-24h.v2"
            and row["enhancement_routes"] == 3
            for row in checks
        )
        if args.require_fresh:
            passed = passed and all(row["data_status"] == "fresh" for row in checks)
        host_ready = body("dsh-ready").get("ready") is True
        passed = passed and host_ready
    write(
        output / "receipt.json",
        {
            "schema_version": "market-risk-installed-read-census.v1",
            "at": now.isoformat(),
            "passed": passed,
            "requests": results,
            "scopes": checks,
            "formal_root": str(ROOT),
            "http_proxy_inheritance": False,
            "request_attempts_per_endpoint": 1,
            "read_only": True,
            "model_submissions": 0,
            "writes": 0,
            "real_dsh_terminal_accepted": False,
            "future_statistical_gain_accepted": False,
            "remaining_gates": [
                "real_dsh_btc_eth_terminal_originals",
                "pc_interactions_and_screenshots",
                "future_sample_qualification",
            ],
        },
    )
    print(
        json.dumps(
            {"passed": passed, "requests": len(results), "receipt": str(output / "receipt.json")}
        )
    )
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-fresh", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        ROOT.name != "decision-hub"
        or output.exists()
        or not output.is_relative_to(ROOT / "artifacts")
    ):
        raise ValueError("installed_formal_root_and_fresh_artifacts_output_required")
    output.mkdir(parents=True)
    raise SystemExit(asyncio.run(check(args, output)))


if __name__ == "__main__":
    main()
