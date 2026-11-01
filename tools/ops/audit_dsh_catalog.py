"""Verify model-visible tool catalogs in a Run's root and descendant archives."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.persistence.db import Database, DshSessionLinkRecord
from packages.query_views.research_usage import _header, _stamp
from packages.runtime_adapters.dsh_runtime.session_archive import load_dsh_session_archive


def _sources_in_scope(capability):
    return all(
        any(domain == allowed or domain.endswith("." + allowed) for allowed in scope)
        for route in capability.get("routes", [])
        for source in route.get("sources") or []
        for domain in source["domains"]
        for scope in (capability.get("allowed_domains", []), route.get("allowed_domains", []))
    )


def verify_tools(headers, method, root_session, request_hash, *, require_query_inputs=False):
    checks = dict(
        scope_identity=True, catalog_hash=True, allowed_capabilities=True, parameter_enum=True
    )
    records = []
    if require_query_inputs:
        checks["query_input_metadata"] = True
    for header in headers:
        for tool in header.get("tools", []):
            if tool.get("name") != "decision_hub_research":
                continue
            try:
                description = tool["description"]
                start = description.index('{"schema_version":"research-query-catalog.v1"')
                catalog, _ = json.JSONDecoder().raw_decode(description[start:])
                scope = catalog.get("scope", {})
                ids = [c["capability_id"] for c in catalog["capabilities"]]
                content = {k: v for k, v in catalog.items() if k != "catalog_hash"}
                digest = hashlib.sha256(
                    json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                checks["scope_identity"] &= (
                    scope.get("dsh_session_id") == root_session
                    and scope.get("run_id") == method["run_id"]
                    and scope.get("request_hash") == request_hash == method["request_hash"]
                    and scope.get("method_hash") == method["method_hash"]
                    and scope.get("status") == "bound"
                    and scope.get("execution_mode") == method["definition"]["execution_mode"]
                )
                checks["catalog_hash"] &= digest == catalog["catalog_hash"]
                checks["allowed_capabilities"] &= (
                    bool(ids)
                    and set(ids).issubset(method["definition"]["allowed_capabilities"])
                    and all(
                        c["modes"] == [method["definition"]["execution_mode"]]
                        for c in catalog["capabilities"]
                    )
                )
                checks["parameter_enum"] &= (
                    tool["parameters"]["properties"]["capability_id"].get("enum") == ids
                )
                if require_query_inputs:
                    required = {
                        "symbols",
                        "symbols_required",
                        "max_symbols",
                        "symbol_fields",
                        "sources",
                        "target_url_required",
                        "evidence_ref_required",
                        "usage_notes",
                    }
                    checks["query_input_metadata"] &= all(
                        bool(c.get("routes"))
                        and all(required <= set(r) for r in c["routes"])
                        and _sources_in_scope(c)
                        for c in catalog["capabilities"]
                    ) and {"evidence_ref", "content_ref"} <= set(tool["parameters"]["properties"])
                records.append({"catalog_hash": catalog["catalog_hash"], "capabilities": ids})
            except (KeyError, ValueError, TypeError):
                checks = {key: False for key in checks}
                records.append({"error": "catalog_missing_or_invalid"})
    return {"tool_definitions": len(records), "checks": checks, "catalogs": records}


def discover_archives(sessions, root_session):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", root_session):
        raise ValueError("session_identity_invalid")
    roots = list(sessions.glob(f"*/{root_session}/session.jsonl.zstd"))
    if len(roots) != 1:
        raise ValueError("root_archive_not_unique")
    # Inspect bounded headers across archive groups so lineage is not inferred
    # merely from sharing the root's directory.
    paths = list(sessions.glob("*/*/session.jsonl.zstd"))
    if len(paths) > 5000:
        raise ValueError("archive_directory_scan_limit")
    candidates, header_errors = {}, 0
    for path in paths:
        if not path.resolve().is_relative_to(sessions.resolve()):
            raise ValueError("archive_outside_root")
        try:
            meta = _header(*_stamp(path))
            if meta.get("id") != path.parent.name:
                raise ValueError("archive_header_identity_invalid")
            if meta["id"] in candidates:
                raise ValueError("duplicate_session_archive")
            candidates[meta["id"]] = (meta, path)
        except (ValueError, OSError):
            header_errors += 1
    if root_session not in candidates:
        raise ValueError("root_archive_header_invalid")
    selected = {root_session}
    while True:
        children = {
            sid for sid, (meta, _) in candidates.items() if meta.get("parentSession") in selected
        }
        if children.issubset(selected):
            break
        selected.update(children)
        if len(selected) > 128:
            raise ValueError("descendant_limit")
    return candidates, selected, header_errors


def audit(database, sessions, run_id, *, require_query_inputs=False):
    method = ResearchMethodService(database).get(run_id)
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        if link is None or method is None:
            raise ValueError("method_and_session_link_required")
        root_session, request_hash = link.dsh_session_id, link.request_hash
    candidates, selected, header_errors = discover_archives(sessions, root_session)
    rows = []
    for sid in sorted(selected):
        meta, path = candidates[sid]
        archive = load_dsh_session_archive(path, expected_session_id=sid)
        headers = [
            e["data"]["header"]
            for e in archive.events
            if e["type"] == "request/header" and e.get("time", 0) >= meta["createdAt"]
        ]
        result = verify_tools(
            headers, method, root_session, request_hash, require_query_inputs=require_query_inputs
        )
        rows.append(
            {
                "session_id": sid,
                "parent_session": meta.get("parentSession"),
                "request_headers": len(headers),
                "archive_sha256": archive.archive_sha256,
                **result,
            }
        )
    root = next(row for row in rows if row["session_id"] == root_session)
    return {
        "run_id": run_id,
        "observed_at": datetime.now(UTC).isoformat(),
        "sessions": rows,
        "directory_header_errors": header_errors,
        "passed": header_errors == 0
        and root["tool_definitions"] > 0
        and all(all(row["checks"].values()) for row in rows),
        "scope": "actual research tool definitions in discovered root/descendant requests",
        "provider_configuration_attested": False,
        "deployment_connected": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-query-inputs", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file() or args.output.exists():
        raise ValueError("existing_database_and_new_output_required")
    db = Database(f"sqlite+pysqlite:///file:{args.database.resolve().as_posix()}?mode=ro&uri=true")
    try:
        proof = audit(
            db, args.sessions, args.run_id, require_query_inputs=args.require_query_inputs
        )
    finally:
        db.engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": proof["passed"], "sessions": len(proof["sessions"])}))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
