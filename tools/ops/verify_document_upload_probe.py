"""Read-only database/asset evidence for a fresh PC upload probe; optional GETs only.

Exact receipt GETs made by this verifier are labelled evidence_only and never
count as a browser confirmation. No upload, migration, Worker or Provider starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.ops.serve_document_recovery_probe import (  # noqa: E402
    UPLOAD_LABEL,
    database_snapshot,
    validate_output,
)

REF = re.compile(r"^docbundle://sha256/([a-f0-9]{64})/([A-Za-z0-9_.-]+)$")
NO_EXECUTION = ("runs", "artifacts", "run_calls", "research_tool_call_reservations",
                "evolution_planning_calls")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def instant(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC).isoformat() if parsed.tzinfo is None \
        else parsed.astimezone(UTC).isoformat()


def bundle_path(probe: Path, reference: str) -> Path:
    match = REF.fullmatch(reference)
    if not match:
        raise ValueError("invalid document bundle reference")
    root = (probe / "runtime/document-bundles").resolve()
    asset = (root / match[1] / match[2]).resolve()
    if not asset.is_relative_to(root) or not asset.is_file():
        raise ValueError("asset must exist inside this probe's document bundle root")
    return asset


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def probe_inputs(seed_path: Path, manifest_path: Path):
    allowed = (REPO / "data/product-local/remediation").resolve()
    seed_path, manifest_path = seed_path.resolve(), manifest_path.resolve()
    if not seed_path.is_relative_to(allowed) or seed_path.name != "seed.json" \
            or not manifest_path.is_relative_to(allowed):
        raise ValueError("seed and manifest must be explicit remediation files")
    seed, manifest = load_json(seed_path), load_json(manifest_path)
    if seed.get("isolation_label") != UPLOAD_LABEL or seed.get("mode") != "upload" \
            or any(seed.get(key) is not False for key in (
                "production_data", "proxy", "paid_provider",
            )) or seed.get("worker_role") != "documents_only":
        raise ValueError("seed must declare the isolated upload probe")
    database = (seed_path.parent / "runtime/db/decision_hub.sqlite3").resolve()
    if Path(seed["database"]).resolve() != database or not database.is_file():
        raise ValueError("database must be exactly this probe's runtime database")
    origin = urlparse(seed["base_url"])
    if origin.scheme != "http" or origin.hostname != "127.0.0.1" or not origin.port \
            or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        raise ValueError("seed origin must be an explicit local probe without credentials")
    if manifest.get("paid_provider") is not False or manifest.get("production_data") is not False:
        raise ValueError("manifest must declare local fixtures")
    fixtures = {}
    for item in manifest["files"]:
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(manifest_path.parent) or not path.is_file() \
                or path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]:
            raise ValueError("fixture bytes/path/hash differ from the explicit manifest")
        fixtures[item["sha256"]] = item
    return seed, database, fixtures


def stable_versions(current: dict) -> list[dict]:
    fields = ("version_id", "document_id", "original_file_ref", "original_file_hash",
              "created_at", "received_at", "observed_at", "published_at")
    return sorted([{field: row[field] for field in fields} for row in current["versions"]],
                  key=lambda row: row["version_id"])


def write_identities(journal: list[dict]) -> list[dict]:
    # A pending POST may finish after navigation. Its response fields can change;
    # the request identity/sequence must remain fixed and no new write can appear.
    fields = ("sequence", "method", "path", "request_id", "owner_header", "received_at")
    return [{field: row.get(field) for field in fields} for row in journal
            if row["method"] not in {"GET", "HEAD"}]


def verify(seed_path: Path, manifest_path: Path, *, stage: str = "snapshot",
           previous: Path | None = None, no_auto_post: bool = False,
           require_confirmed: bool = False, expected_status: str | None = None,
           expected_documents: int | None = None, expected_receipts: int | None = None,
           require_worker_continued: bool = False, http: bool = False,
           client=None) -> dict:
    seed, database, fixtures = probe_inputs(seed_path, manifest_path)
    probe = seed_path.resolve().parent
    current = database_snapshot(database)
    journal = load_json(probe / "requests.json")
    worker = load_json(probe / "worker-status.json")
    events = load_json(probe / "worker-events.json")
    checks = []
    http_checks = []

    def check(name, passed, evidence=None):
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    def get(path: str, *, binary=False):
        headers = {"X-Owner-Id": "owner", "X-PC-Probe-Evidence": "readonly-verifier"}
        if client is not None:
            response = client.get(path, headers=headers)
            result = sha(response.content) if binary else response.json()
            code = response.status_code
        else:
            request = Request(seed["base_url"] + path, method="GET", headers=headers)
            with urlopen(request, timeout=15) as response:
                code = response.status
                if binary:
                    digest = hashlib.sha256()
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                    result = digest.hexdigest()
                else:
                    result = json.loads(response.read())
        http_checks.append({"method": "GET", "path": path, "status": code,
                            "evidence_only": True, "download_sha256": result if binary else None})
        return code, result

    check("no_research_or_model_execution", all(current["counts"][table] == 0
                                               for table in NO_EXECUTION))
    check("worker_loop_alive", worker.get("alive") is True, worker)
    if expected_documents is not None:
        check("exact_document_and_version_count", current["counts"]["research_documents"]
              == expected_documents == current["counts"]["research_document_versions"],
              current["counts"])
    if expected_receipts is not None:
        check("exact_upload_receipt_count", current["counts"]["product_commands"]
              == expected_receipts, current["counts"]["product_commands"])
    if stage == "before":
        check("fresh_probe_empty", current == seed["baseline"])
    if no_auto_post:
        if previous is None:
            raise ValueError("no-auto-post requires an explicit previous successful snapshot")
        prior_path = previous.resolve()
        allowed = (REPO / "data/product-local/remediation").resolve()
        if not prior_path.is_relative_to(allowed) or prior_path.name != "checks.json":
            raise ValueError("previous must be a remediation checks.json")
        prior = load_json(prior_path)
        if prior.get("seed") != str(seed_path.resolve()) or prior.get("passed") is not True:
            raise ValueError("previous must be this probe's successful audit snapshot")
        writes = write_identities(journal)
        prior_writes = write_identities(prior["request_journal"])
        check("no_automatic_write_since_previous", writes == prior_writes,
              {"prior_write_count": len(prior_writes), "current_write_count": len(writes)})
        check("no_duplicate_archival_or_changed_identity", stable_versions(current)
              == stable_versions(prior["current"])
              and current["documents"] == prior["current"]["documents"]
              and current["commands"] == prior["current"]["commands"])

    versions = {row["version_id"]: row for row in current["versions"]}
    for row in current["versions"]:
        original = bundle_path(probe, row["original_file_ref"])
        fixture = fixtures.get(row["original_file_hash"])
        check("original_matches_fixture:" + row["version_id"], fixture is not None
              and file_sha(original) == row["original_file_hash"]
              and original.stat().st_size == fixture["bytes"], {
                  "version_id": row["version_id"], "status": row["status"],
                  "sha256": row["original_file_hash"], "bytes": original.stat().st_size,
              })
        if row["parse_receipt_ref"]:
            receipt_asset = bundle_path(probe, row["parse_receipt_ref"])
            check("parse_receipt_content_address:" + row["version_id"],
                  file_sha(receipt_asset) == REF.fullmatch(row["parse_receipt_ref"])[1])
            parsed = load_json(receipt_asset)
            check("actual_parse_receipt_identity:" + row["version_id"],
                  parsed["version_id"] == row["version_id"]
                  and parsed["original_file_hash"] == row["original_file_hash"]
                  and parsed["parser_ref"] == "pypdf-text.v1")
            if fixture and fixture.get("page_count"):
                check("actual_pdf_pages_and_locators:" + row["version_id"],
                      parsed["page_count"] == fixture["page_count"]
                      and {block["page_no"] for block in current["blocks"]
                           if block["version_id"] == row["version_id"]} == {1, 2})
        elif row["status"] == "failed":
            check("bad_pdf_failed_without_fabricated_parse_receipt:" + row["version_id"],
                  fixture and fixture.get("intended_corruption")
                  and row["block_count"] == 0
                  and any(issue.startswith("document_processing_failed:parse:")
                          for issue in json.loads(row["quality_json"])["issues"]))
        if http:
            code, downloaded = get(f"/v1/research-library/versions/{row['version_id']}/original",
                                   binary=True)
            check("actual_original_get200_sha:" + row["version_id"], code == 200
                  and downloaded == row["original_file_hash"])

    for command in current["commands"]:
        receipt = json.loads(command["result_json"])
        intent = receipt.get("intent", {})
        canonical = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        frozen = receipt.get("version", {})
        row = versions.get(frozen.get("version_id"))
        check("actual_upload_command_contract:" + command["request_id"],
              command["kind"] == "library_upload" and command["owner_id"] == "owner"
              and command["payload_hash"] == command["target_id"] == sha(canonical.encode())
              and receipt["request_id"] == command["request_id"]
              and intent.get("owner_id") == "owner" and row is not None
              and frozen["original_file_hash"] == intent["file_sha256"]
              == row["original_file_hash"]
              and instant(frozen["received_at"]) == instant(row["received_at"])
              and instant(frozen["created_at"]) == instant(row["created_at"]), {
                  "request_id": command["request_id"], "version_id": frozen.get("version_id"),
                  "deduplicated": receipt.get("deduplicated"),
              })
        if http:
            code, projected = get("/v1/research-library/uploads/" + quote(command["request_id"],
                                                                       safe=""))
            check("exact_frozen_receipt_get:" + command["request_id"], code == 200
                  and projected == receipt)
    faults = [entry for entry in journal if entry.get("committed_response_withheld")]
    if faults or require_confirmed:
        check("one_expected_commit202_delivered503", len(faults) == 1 and all(
            entry["application_status"] == 202 and entry["delivered_status"] == 503
            and entry.get("expected_fault") is True for entry in faults
        ))
    if len(faults) == 1:
        original = faults[0]
        frozen = original["receipt"]["version"]
        row = versions[frozen["version_id"]]
        if expected_status:
            check("fault_upload_current_status", row["status"] == expected_status, row["status"])
        confirmations = [entry for entry in journal if entry.get("sequence", 0)
                         > original["sequence"] and entry.get("request_id")
                         == original["request_id"] and not entry.get("evidence_only")
                         and entry.get("delivered_status") in {200, 202}
                         and entry.get("receipt") == original["receipt"]]
        check("original_request_receipt_confirmation", bool(confirmations) or not require_confirmed,
              [{"method": entry["method"], "path": entry["path"], "sequence": entry["sequence"]}
               for entry in confirmations])
    if require_worker_continued:
        failures = [event["sequence"] for event in events if event["kind"] == "tick_failed"]
        later = [event for event in events if event["kind"] == "tick_processed"
                 and failures and event["sequence"] > max(failures)
                 and event["version_id"] in versions
                 and fixtures[versions[event["version_id"]]["original_file_hash"]].get("page_count")
                 == 2 and versions[event["version_id"]]["status"] in {"parsed", "review", "ready"}]
        check("worker_processed_real_pdf_after_bad_pdf", bool(failures) and bool(later), later)
    return {
        "stage": stage, "verified_at": datetime.now(UTC).isoformat(),
        "isolation_label": UPLOAD_LABEL,
        "seed": str(seed_path.resolve()), "database": str(database),
        "database_access": "mode=ro; PRAGMA query_only=ON", "http_writes_made": 0,
        "browser_navigation_verified": False, "passed": all(item["passed"] for item in checks),
        "fault_semantics": seed["fault_semantics"], "checks": checks, "current": current,
        "worker": worker, "worker_events": events, "request_journal": journal,
        "http_get_checks": http_checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", default="snapshot")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--no-auto-post", action="store_true")
    parser.add_argument("--require-confirmed", action="store_true")
    parser.add_argument("--expected-status", choices=("received", "parsing", "parsed", "failed",
                                                     "review", "ready"))
    parser.add_argument("--expected-documents", type=int)
    parser.add_argument("--expected-receipts", type=int)
    parser.add_argument("--require-worker-continued", action="store_true")
    parser.add_argument("--http", action="store_true")
    args = parser.parse_args()
    try:
        output = validate_output(args.output)
        if output.is_relative_to(args.seed.resolve().parent / "runtime"):
            raise ValueError("audit output must not modify the probe runtime")
        result = verify(args.seed, args.manifest, stage=args.stage, previous=args.previous,
                        no_auto_post=args.no_auto_post, require_confirmed=args.require_confirmed,
                        expected_status=args.expected_status,
                        expected_documents=args.expected_documents,
                        expected_receipts=args.expected_receipts,
                        require_worker_continued=args.require_worker_continued, http=args.http)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    output.mkdir(parents=True, exist_ok=False)
    (output / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "report": str(output / "checks.json"),
                      "http_writes_made": 0}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
