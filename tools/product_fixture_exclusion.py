"""Confirm the fictional acceptance document stayed out of committed evidence."""

from __future__ import annotations

import argparse
import json
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8047")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--version-id", required=True)
    args = parser.parse_args()
    base = args.api_url.rstrip("/")
    detail_response = requests.get(base + f"/v1/research/runs/{args.run_id}", timeout=30)
    detail_response.raise_for_status()
    detail = detail_response.json()
    claims_response = requests.get(base + f"/v1/research-library/versions/{args.version_id}/claims", timeout=30)
    claims_response.raise_for_status()
    claims = claims_response.json()
    if not claims or any(claim.get("status") != "rejected" for claim in claims):
        raise RuntimeError("Fictional fixture claims were not all rejected")
    if not detail.get("run", {}).get("artifact_id") or not detail.get("context_manifest"):
        raise RuntimeError("Research Run has no committed Artifact or context manifest")
    evidence = detail.get("evidence", [])
    if not evidence:
        raise RuntimeError("Research Run has no independently acquired evidence")
    for item in evidence:
        serialized = json.dumps(item, ensure_ascii=False).casefold()
        if any(marker in serialized for marker in ("acceptance-note", "automated acceptance fixture", "自动化验收用的虚构观点")):
            raise RuntimeError(f"Fictional fixture leaked into Evidence {item.get('evidence_id')}")
    print(json.dumps({"status": "passed", "run_id": args.run_id, "version_id": args.version_id,
                      "rejected_claims": len(claims), "independent_evidence": len(evidence)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
        print(f"fixture exclusion failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
