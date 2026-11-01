"""Compare a saved main-model binding with the linked DSH request headers."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.persistence.db import Database, DshSessionLinkRecord
from packages.runtime_adapters.dsh_runtime.session_archive import load_dsh_session_archive


def audit(database, sessions, run_id, *, include_descendants=False):
    binding = ModelDispatchService(database).get(run_id)
    if binding is None:
        raise ValueError("model_binding_not_recorded")
    method = ResearchMethodService(database).get(run_id)
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        if link is None or link.dsh_session_id != binding.dsh_session_id:
            raise ValueError("model_binding_session_mismatch")
        if method is None or not (
            method["request_hash"] == binding.request_hash == link.request_hash
        ):
            raise ValueError("model_binding_method_mismatch")
        link_request, link_build = link.request_hash, link.plugin_build_hash
    if not re.fullmatch(r"dsh_[a-f0-9]{64}", binding.dsh_session_id):
        raise ValueError("model_binding_session_identity_invalid")
    paths = list(sessions.glob(f"*/{binding.dsh_session_id}/session.jsonl.zstd"))
    if len(paths) != 1 or not paths[0].resolve().is_relative_to(sessions.resolve()):
        raise ValueError("session_archive_not_unique_or_outside_root")
    archive = load_dsh_session_archive(paths[0], expected_session_id=binding.dsh_session_id)
    headers = []
    for event in archive.events:
        if event["type"] != "request/header":
            continue
        config = event["data"]["header"]["config"]
        stop_hash = (
            None
            if "stop" not in config
            else hashlib.sha256(
                json.dumps(config["stop"], ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
        )
        headers.append(
            {
                "provider": config["provider"],
                "model": config["model"],
                "reasoning_effort": config.get("reasoningEffort"),
                "temperature": config.get("temperature"),
                "max_tokens": config.get("maxTokens"),
                "stop_sequences_hash": stop_hash,
            }
        )
    controls = binding.model_dump(
        include={
            "provider",
            "model",
            "reasoning_effort",
            "temperature",
            "max_tokens",
            "stop_sequences_hash",
        }
    )
    checks = {
        "request_identity_matches": binding.request_hash == link_request == method["request_hash"],
        "host_build_matches": binding.plugin_build_hash
        == link_build
        == method["definition"]["upstream_identity"]["plugin_build_hash"],
        "request_headers_present": bool(headers),
        "all_logged_headers_match": bool(headers) and all(h == controls for h in headers),
    }
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "binding": binding.model_dump(mode="json"),
        "checks": checks,
        "request_header_count": len(headers),
        "archive_sha256": archive.archive_sha256,
        "scope": "main conversation stream controls; excludes auxiliary and child sessions",
        "provider_configuration_attested": False,
        "deployment_connected": False,
    }

    if include_descendants:
        from tools.ops.audit_dsh_catalog import discover_archives

        candidates, selected, errors = discover_archives(sessions, binding.dsh_session_id)
        children = {
            b.execution_session_id: b for b in ModelDispatchService(database).children(run_id)
        }
        rows = []
        for sid in sorted(selected - {binding.dsh_session_id}):
            meta, path = candidates[sid]
            child_archive = load_dsh_session_archive(path, expected_session_id=sid)
            # Spawned sessions can contain copied parent requests predating birth.
            configs = [
                e["data"]["header"]["config"]
                for e in child_archive.events
                if e["type"] == "request/header" and e.get("time", 0) >= meta["createdAt"]
            ]
            child = children.get(sid)
            match = child is not None and (
                child.parent_session_id == meta.get("parentSession")
                and child.dsh_session_id == binding.dsh_session_id
                and child.request_hash == binding.request_hash
                and child.plugin_build_hash == binding.plugin_build_hash
            )
            controls = (
                child.model_dump(
                    include={
                        "provider",
                        "model",
                        "reasoning_effort",
                        "temperature",
                        "max_tokens",
                        "stop_sequences_hash",
                    }
                )
                if child
                else None
            )

            def actual(config):
                return {
                    "provider": config["provider"],
                    "model": config["model"],
                    "reasoning_effort": config.get("reasoningEffort"),
                    "temperature": config.get("temperature"),
                    "max_tokens": config.get("maxTokens"),
                    "stop_sequences_hash": None
                    if "stop" not in config
                    else hashlib.sha256(
                        json.dumps(
                            config["stop"], ensure_ascii=False, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                }

            rows.append(
                {
                    "session_id": sid,
                    "parent_session_id": meta.get("parentSession"),
                    "request_header_count": len(configs),
                    "binding": child.model_dump() if child else None,
                    "archive_sha256": child_archive.archive_sha256,
                    "identity_matches": bool(match),
                    "all_logged_headers_match": bool(configs)
                    and all(actual(c) == controls for c in configs),
                    # A child that never dispatched needs no model receipt.
                    "passed": not configs
                    and child is None
                    or bool(match)
                    and bool(configs)
                    and all(actual(c) == controls for c in configs),
                }
            )
        result["descendants"] = rows
        result["checks"].update(
            {
                "archive_headers_readable": errors == 0,
                "all_recorded_children_discovered": set(children).issubset(
                    selected - {binding.dsh_session_id}
                ),
                "all_descendant_dispatches_match": all(row["passed"] for row in rows),
            }
        )
        result["scope"] = (
            "root and all discovered descendant conversation streams; excludes compaction/title"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-descendants", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file() or args.output.exists():
        raise ValueError("existing_database_and_new_output_required")
    path = args.database.resolve().as_posix()
    database = Database(f"sqlite+pysqlite:///file:{path}?mode=ro&uri=true")
    try:
        proof = audit(
            database, args.sessions, args.run_id, include_descendants=args.include_descendants
        )
    finally:
        database.engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(args.output), "checks": proof["checks"]}))
    if not all(proof["checks"].values()):
        raise SystemExit("model_dispatch_audit_failed")


if __name__ == "__main__":
    main()
