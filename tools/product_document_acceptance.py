"""Exercise the local-only research library with a labelled test document.

The fixture is private and every extracted claim is rejected before a Run is
queued, so it cannot masquerade as market evidence. Repeated runs reuse the
same immutable upload and research idempotency key.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

TITLE = "Decision Hub automated acceptance fixture"
TEXT = "BTC 看多，目标上涨 10%。这是自动化验收用的虚构观点，不是真实市场证据。"
METADATA = {
    "schema_version": "document-upload.v1",
    "title": TITLE,
    "source_type": "private_file",
    "source_profile_id": None,
    "author": "Decision Hub acceptance",
    "published_at": None,
    "market_scopes": ["crypto_macro"],
    "instrument_scopes": ["BTC-USDT"],
    "privacy_mode": "local_only",
    "visibility": "private",
    "supersedes_version_id": None,
    "notes": "Automated local acceptance only; not factual market data.",
}


def checked(response: requests.Response, expected: int = 200) -> dict[str, object]:
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.path_url}: HTTP {response.status_code} {response.text[:200]}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("Expected a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8047")
    parser.add_argument("--start-research", action="store_true")
    args = parser.parse_args()
    base = args.api_url.rstrip("/")
    session = requests.Session()
    version = checked(
        session.post(
            base + "/v1/research-library/documents",
            data={"metadata": json.dumps(METADATA, ensure_ascii=False)},
            files={"file": ("acceptance-note.txt", TEXT.encode("utf-8"), "text/plain")},
            timeout=45,
        ),
        202,
    )
    version_id = str(version["version_id"])
    document_id = str(version["document_id"])
    deadline = time.monotonic() + 60
    while version["status"] == "received" and time.monotonic() < deadline:
        time.sleep(1)
        version = checked(session.get(base + f"/v1/research-library/versions/{version_id}", timeout=15))
    if version["status"] not in {"parsed", "correcting", "ready"}:
        raise RuntimeError(f"Document parsing did not complete: {version['status']}")
    original = session.get(base + f"/v1/research-library/versions/{version_id}/original", timeout=15)
    if original.status_code != 200 or original.content != TEXT.encode("utf-8"):
        raise RuntimeError("The immutable original cannot be read back")
    blocks = session.get(base + f"/v1/research-library/versions/{version_id}/blocks", timeout=15)
    blocks.raise_for_status()
    if not blocks.json():
        raise RuntimeError("Parsed document has no blocks")
    if version["status"] == "parsed":
        version = checked(session.post(base + f"/v1/research-library/versions/{version_id}/correction", timeout=30), 202)
    correction = None
    while correction is None and time.monotonic() < deadline:
        time.sleep(1)
        response = session.get(base + f"/v1/research-library/versions/{version_id}/correction", timeout=15)
        response.raise_for_status()
        correction = response.json()
    if not isinstance(correction, dict):
        raise RuntimeError("Document correction did not produce a review receipt")
    if version["status"] != "ready":
        version = checked(session.post(base + f"/v1/research-library/versions/{version_id}/correction/accept", timeout=30))
    if version["status"] != "ready":
        raise RuntimeError(f"Document did not become ready: {version['status']}")
    claims_response = session.get(base + f"/v1/research-library/versions/{version_id}/claims", timeout=15)
    claims_response.raise_for_status()
    claims = claims_response.json()
    if not isinstance(claims, list) or not claims:
        raise RuntimeError("Accepted correction did not extract an auditable claim")
    for claim in claims:
        if claim["status"] == "candidate":
            checked(
                session.post(
                    base + f"/v1/research-library/claims/{claim['claim_id']}/review",
                    json={
                        "schema_version": "claim-review-command.v1",
                        "request_id": f"product-acceptance-reject-{claim['claim_id']}",
                        "decision": "reject",
                        "reason": "Fictional acceptance fixture; must not be treated as market evidence.",
                    },
                    timeout=15,
                )
            )
    claims_response = session.get(base + f"/v1/research-library/versions/{version_id}/claims", timeout=15)
    claims_response.raise_for_status()
    if any(claim["status"] != "rejected" for claim in claims_response.json()):
        raise RuntimeError("Fixture claim review did not persist rejection")
    result: dict[str, object] = {
        "status": "passed",
        "document_id": document_id,
        "version_id": version_id,
        "blocks": len(blocks.json()),
        "rejected_fixture_claims": len(claims),
    }
    if args.start_research:
        request = {
            "schema_version": "research-request.v1",
            "research_family": "crypto_macro",
            "instrument_scope": ["BTC-USDT"],
            "focus": "获取最新操作建议；请独立核验 BTC 与宏观事实，并说明测试资料未被采纳为市场证据。",
            "horizon": "7d",
            "library_context_mode": "selected",
            "selected_version_ids": [version_id],
        }
        queued = checked(
            session.post(
                base + "/v1/research-library/research",
                headers={"Idempotency-Key": "product-document-acceptance-v1"},
                json=request,
                timeout=30,
            ),
            202,
        )
        result["research_run_id"] = queued["run_id"]
        result["research_status"] = queued["status"]
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
        print(f"product document acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
