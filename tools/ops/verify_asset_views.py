"""Read-only smoke check of deployed W5/W6 views against a real saved Run."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx


def verify(base_url: str, run_id: str) -> dict:
    proof = {"checked_at": datetime.now(UTC).isoformat(), "run_id": run_id, "checks": {}}
    with httpx.Client(base_url=base_url, trust_env=False, timeout=30) as client:

        def read(path, params=None):
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

        outlooks = read("/v1/analytical-outlooks", {"run_id": run_id, "limit": 100})
        assert outlooks["items"] and all(row["run_id"] == run_id for row in outlooks["items"])
        cohorts = read("/v1/analytical-outlooks/cohorts", {"run_id": run_id, "limit": 100})
        assert sum(row["total"] for row in cohorts["items"]) == outlooks["total"]
        proof["checks"]["outlooks"] = outlooks
        proof["checks"]["cohorts"] = cohorts
        facts = read("/v1/facts/versions", {"limit": 1})
        version = facts["items"][0]
        history = read(
            "/v1/facts/versions",
            {"semantic_key": version["semantic_key"], "as_of": facts["as_of"], "limit": 100},
        )
        assert history["items"] and all(
            v["semantic_key"] == version["semantic_key"] for v in history["items"]
        )
        assert all(
            datetime.fromisoformat(v["known_at"]) <= datetime.fromisoformat(history["as_of"])
            for v in history["items"]
        )
        proof["checks"]["fact_history"] = {
            "semantic_key": version["semantic_key"],
            "versions": history["total"],
            "as_of": history["as_of"],
        }
        study = read(f"/v1/runs/{run_id}/study")
        timeline = read(f"/v1/studies/{study['study_id']}", {"limit": 100})
        assert any(r["source_run_id"] == run_id for r in timeline["revisions"])
        proof["checks"]["study"] = {"study_id": study["study_id"], "revisions": timeline["total"]}
        source_history = read("/v1/sources/history")
        assert source_history["timezone"] == "UTC"
        proof["checks"]["source_history"] = source_history
        proof["checks"]["source_captures"] = []
        for source in sorted({r["source_id"] for r in source_history["items"]}):
            attempts = read(f"/v1/sources/{source}/poll-attempts", {"limit": 1})
            if attempts:
                captures = read(f"/v1/source-poll-attempts/{attempts[0]['attempt_id']}/captures")
                proof["checks"]["source_captures"].append(
                    {
                        "source": source,
                        "attempt": attempts[0]["attempt_id"],
                        "captures": len(captures),
                    }
                )
        configs = read("/v1/operations/runtime-configurations", {"limit": 200})
        operations = read("/v1/operations")
        proof["checks"]["runtime_matches"] = [
            {
                "service": s["service_id"],
                "status": s["status"],
                "matching_configuration": any(
                    c["service_id"] == s["service_id"] and c["instance_id"] == s["instance_id"]
                    for c in configs
                ),
            }
            for s in operations["services"]
        ]
        jobs = read("/v1/data-jobs")
        job = next((j for j in jobs if j["coverage_manifest_ref"]), None)
        if job:
            coverage = read(f"/v1/data-jobs/{job['job_id']}/coverage")
            assert coverage["verified"] is True
            proof["checks"]["coverage"] = {"job_id": job["job_id"], "manifest": coverage}
        else:
            proof["checks"]["coverage"] = {"status": "not_available", "reason": "no_saved_manifest"}
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8047")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = verify(args.base_url, args.run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"saved": str(args.output), "checks": list(proof["checks"])}))


if __name__ == "__main__":
    main()
