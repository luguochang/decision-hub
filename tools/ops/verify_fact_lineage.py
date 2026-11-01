"""Read-only real API/DB proof for every fact adopted by one committed Run."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx


def verify(database: Path, run_id: str, api_url: str):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        refs = db.execute(
            "SELECT fact_version_id,use_mode FROM run_input_refs WHERE run_id=?", (run_id,)
        ).fetchall()
        proof = {
            "run_id": run_id,
            "fact_refs": len(refs),
            "ref_modes": sorted({r[1] for r in refs}),
            "raw_captures": [],
            "archived_inputs": [],
            "missing": [],
        }
        seen, seen_assets = set(), set()
        with httpx.Client(trust_env=False, timeout=30, base_url=api_url) as client:
            response = client.get("/v1/research/runs/" + run_id)
            response.raise_for_status()
            detail = response.json()
            proof["run"] = detail["run"]
            proof["evidence_count"] = len(detail["evidence"])
            for version_id, _mode in refs:
                response = client.get("/v1/facts/versions/" + version_id)
                response.raise_for_status()
                view = response.json()
                assert any(item["run_id"] == run_id for item in view["run_inputs"])
                asset = view.get("structured_payload_ref") or view["content_ref"]
                if not view["raw_captures"] and not asset:
                    proof["missing"].append(version_id)
                for raw in view["raw_captures"]:
                    if raw["capture_id"] in seen:
                        continue
                    seen.add(raw["capture_id"])
                    body = client.get("/v1/raw-captures/" + raw["capture_id"] + "/body")
                    body.raise_for_status()
                    assert hashlib.sha256(body.content).hexdigest() == raw["body_hash"]
                    assert body.headers["content-type"].startswith("text/plain")
                    assert body.headers["x-content-type-options"] == "nosniff"
                    proof["raw_captures"].append(raw)
                if asset and asset not in seen_assets:
                    seen_assets.add(asset)
                    response = client.get("/v1/facts/versions/" + version_id + "/input")
                    response.raise_for_status()
                    payload = response.json()
                    proof["archived_inputs"].append(
                        {
                            "version_id": version_id,
                            "kind": payload["kind"],
                            "verified": payload["verified"],
                            "ref": payload["ref"],
                        }
                    )
            counts = {
                table: db.execute(
                    f"SELECT count(*) FROM {table} WHERE {column}=?", (run_id,)
                ).fetchone()[0]
                for table, column in (
                    ("artifacts", "run_id"),
                    ("report_revisions", "source_run_id"),
                    ("research_facts", "run_id"),
                )
            }
            counts["outbox"] = db.execute(
                "SELECT count(*) FROM outbox WHERE artifact_id=?", (detail["run"]["artifact_id"],)
            ).fetchone()[0]
            assert counts["artifacts"] == counts["report_revisions"] == counts["outbox"] == 1
            assert counts["research_facts"] == len(refs) > 0
            proof["counts"] = counts
            proof["status"] = "passed" if not proof["missing"] else "incomplete_archive"
            return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8047")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = verify(args.database, args.run_id, args.api_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": proof["status"],
                "facts": proof["fact_refs"],
                "raw_verified": len(proof["raw_captures"]),
                "archived_input_sets": len(proof["archived_inputs"]),
                "missing": len(proof["missing"]),
                "counts": proof["counts"],
            }
        )
    )
    return 0 if proof["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
