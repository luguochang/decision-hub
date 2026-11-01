"""Read every executable GET contract from the running product's OpenAPI schema.

Known IDs come from the live ledger. Missing optional ledgers are reported as
skipped rather than being counted as a pass. The script never mutates data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import quote

import requests


def get_json(session: requests.Session, base: str, path: str) -> object:
    response = session.get(base + path, timeout=20)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8047")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    base = args.api_url.rstrip("/")
    session = requests.Session()
    schema = get_json(session, base, "/openapi.json")
    assert isinstance(schema, dict)
    runs = get_json(session, base, "/v1/research/runs?limit=100")
    assert isinstance(runs, list)
    run = next(
        (
            item
            for item in runs
            if isinstance(item, dict)
            and item.get("artifact_id")
            and (not args.run_id or item.get("run_id") == args.run_id)
        ),
        None,
    )
    if run is None:
        raise RuntimeError("No committed Run is available for the API matrix")
    run_id = str(run["run_id"])
    detail = get_json(session, base, f"/v1/research/runs/{run_id}")
    assert isinstance(detail, dict)
    jobs = get_json(session, base, "/v1/data-jobs")
    events = get_json(session, base, "/v1/macro-events?limit=1")
    documents = get_json(session, base, "/v1/research-library/documents?limit=1")
    operations = get_json(session, base, "/v1/operations")
    dsh_link = get_json(session, base, f"/v1/dsh/sessions/{run_id}")
    assert isinstance(dsh_link, dict)
    document = documents[0] if isinstance(documents, list) and documents else {}
    sources = operations.get("sources", []) if isinstance(operations, dict) else []
    source = next((item for item in sources if isinstance(item, dict) and item.get("source_id")), {})
    values = {
        "run_id": run_id,
        "artifact_id": str(run["artifact_id"]),
        "job_id": str(jobs[0]["job_id"]) if isinstance(jobs, list) and jobs else "",
        "macro_event_id": str(events[0]["macro_event_id"]) if isinstance(events, list) and events else "",
        "document_id": str(document.get("document_id", "")),
        "version_id": str(document.get("latest_version_id", "")),
        "dsh_session_id": str(dsh_link.get("dsh_session_id") or ""),
        "source_id": str(source.get("source_id", "")),
        "evaluation_id": "",
    }
    passed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for path, methods in schema["paths"].items():
        if "get" not in methods:
            continue
        if path in {"/v1/{path}", "/{path}"}:
            skipped.append(f"{path}: fallback route")
            continue
        if path.endswith("/events"):
            try:
                with session.get(base + path.replace("{run_id}", quote(run_id, safe="")), stream=True, timeout=20) as response:
                    if response.status_code != 200 or "text/event-stream" not in response.headers.get("content-type", ""):
                        failed.append(f"{path}: HTTP {response.status_code} or wrong stream type")
                    elif not next(response.iter_lines(), b"").startswith(b"id:"):
                        failed.append(f"{path}: no first trace event")
                    else:
                        passed.append(path)
            except requests.RequestException as exc:
                failed.append(f"{path}: {type(exc).__name__}")
            continue
        if path.endswith("/prompt"):
            response = session.get(base + path.replace("{run_id}", quote(run_id, safe="")), timeout=15)
            if response.status_code == 401:
                passed.append(path)
            else:
                failed.append(f"{path}: unauthenticated request returned HTTP {response.status_code}")
            continue
        variables = re.findall(r"\{([^}]+)\}", path)
        missing = [name for name in variables if not values.get(name)]
        if missing:
            skipped.append(f"{path}: no live {', '.join(missing)}")
            continue
        resolved = path
        for name in variables:
            resolved = resolved.replace("{" + name + "}", quote(values[name], safe=""))
        try:
            response = session.get(base + resolved, timeout=25)
            if response.status_code != 200:
                failed.append(f"{path}: HTTP {response.status_code}")
            elif path.endswith("/original") or path.startswith("/health/"):
                passed.append(path)
            elif "application/json" not in response.headers.get("content-type", ""):
                failed.append(f"{path}: non-JSON content type")
            else:
                response.json()
                passed.append(path)
        except (requests.RequestException, ValueError) as exc:
            failed.append(f"{path}: {type(exc).__name__}")
    print(
        json.dumps(
            {
                "status": "passed" if not failed else "failed",
                "run_id": run_id,
                "get_contracts": len(passed) + len(skipped) + len(failed),
                "passed": len(passed),
                "skipped": skipped,
                "failed": failed,
            },
            ensure_ascii=True,
        )
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, RuntimeError, ValueError, AssertionError) as exc:
        print(f"product API smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
