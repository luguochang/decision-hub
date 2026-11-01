"""Bounded read-only accounting projection over Hub-linked DSH archives."""

import hashlib
import io
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import zstandard

from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord, RunRecord
from packages.query_views.usage_accounting import summarize
from packages.query_views.usage_calls import call_ledger
from packages.query_views.usage_tools import tool_ledger

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def _stamp(path):
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=4096)
def _header(path, size, modified):
    del size, modified
    with Path(path).open("rb") as source:
        with io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(source)) as stream:
            line = stream.readline(65537)
    if len(line) > 65536:
        raise ValueError("archive_header_too_large")
    row = json.loads(line)
    if not isinstance(row, dict) or row.get("type") != "session":
        raise ValueError("invalid_session_header")
    return row


@lru_cache(maxsize=64)
def _session_usage(path, size, modified):
    del modified
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("archive_too_large")
    raw = Path(path).read_bytes()
    with zstandard.ZstdDecompressor().stream_reader(
        io.BytesIO(raw), read_across_frames=True
    ) as stream:
        decoded = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(decoded) > MAX_ARCHIVE_BYTES:
        raise ValueError("expanded_archive_too_large")
    events = [json.loads(line) for line in decoded.splitlines() if line.strip()]
    if not events or not isinstance(events[0], dict):
        raise ValueError("empty_session_archive")
    meta = events[0]
    # Forks may contain copied context. It must not be billed again as new work.
    created = meta.get("createdAt")
    if type(created) not in (int, float):
        raise ValueError("missing_session_creation_time")
    local_events = [
        event
        for event in events[1:]
        if isinstance(event, dict)
        and isinstance(event.get("time"), (int, float))
        and event["time"] >= created
    ]
    audit = summarize(local_events)
    audit["calls"] = call_ledger(local_events)
    audit["tools"] = tool_ledger(local_events)
    return meta, audit, hashlib.sha256(raw).hexdigest()


def usage_view(database, sessions_root: Path, run_id: str):
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        if run is None:
            raise KeyError(run_id)
        link = session.get(DshSessionLinkRecord, run_id)
        sid = link.dsh_session_id if link else None
    return usage_for_session(sessions_root, run_id, sid)


def usage_for_session(sessions_root: Path, run_id: str, sid: str | None):
    """Project one trusted Hub session binding, including historical generations."""
    result = {
        "schema_version": "research-usage-view.v1",
        "run_id": run_id,
        "status": "unavailable",
        "sessions": [],
        "total_run_tokens": None,
        "estimated_cost_usd": None,
        "limitations": ["complete_run_accounting_pending", "provider_pricing_unavailable"],
        "observed_at": datetime.now(UTC),
    }
    if sid is None or not re.fullmatch(r"[a-zA-Z0-9_-]+", sid):
        result["limitations"].append("session_link_unavailable")
        return result
    roots = list(sessions_root.glob(f"*/{sid}/session.jsonl.zstd"))
    if len(roots) != 1:
        result["limitations"].append("archive_unavailable")
        return result
    if not roots[0].resolve().is_relative_to(sessions_root.resolve()):
        result["limitations"].append("archive_unavailable")
        return result
    try:
        root_meta = _header(*_stamp(roots[0]))
        if root_meta.get("id") != sid or type(root_meta.get("createdAt")) not in (int, float):
            raise ValueError("archive_identity_mismatch")
    except (OSError, ValueError, zstandard.ZstdError):
        result["limitations"].append("archive_unreadable")
        return result
    found = {sid: roots[0]}
    candidates = {}
    unreadable = False
    # Only examine sibling archives in the linked workspace. A global session
    # directory can contain unrelated workspaces and must not be exposed.
    for path in roots[0].parent.parent.glob("*/session.jsonl.zstd"):
        try:
            if not path.resolve().is_relative_to(roots[0].parent.parent.resolve()):
                unreadable = True
                continue
            if path.stat().st_mtime * 1000 < root_meta["createdAt"]:
                continue
            meta = _header(*_stamp(path))
            if isinstance(meta.get("parentSession"), str) and meta.get("id") == path.parent.name:
                candidates[meta["id"]] = (meta["parentSession"], path)
        except (OSError, ValueError, KeyError, TypeError, zstandard.ZstdError):
            unreadable = True
    for _ in range(len(candidates) + 1):
        added = {
            key: path
            for key, (parent, path) in candidates.items()
            if parent in found and key not in found
        }
        if not added:
            break
        found.update(added)
    for identity, path in found.items():
        try:
            meta, audit, digest = _session_usage(*_stamp(path))
            if meta.get("id") != identity:
                raise ValueError("archive_identity_mismatch")
            issues = sorted(set(audit["main_accounting_issues"]) | {
                issue for call in audit["calls"] for issue in call["issues"]
            })
            result["sessions"].append(
                {
                    "session_id": identity,
                    "role": "main" if identity == sid else "delegated",
                    "model_steps": audit["model_steps"],
                    "usage_samples": audit["valid_usage_samples"],
                    "recorded_tokens": audit["recorded_main_tokens"],
                    "status": "accounted"
                    if audit["complete_main_tokens"] is not None and not issues
                    else "partial",
                    "issues": issues,
                    "archive_sha256": digest,
                    "calls": audit["calls"],
                    "tool_attempts": audit["tools"]["calls"],
                    "tool_issues": audit["tools"]["issues"],
                }
            )
        except (OSError, ValueError, TypeError, zstandard.ZstdError):
            unreadable = True
    if unreadable:
        result["limitations"].append("some_archives_unreadable")
    if result["sessions"]:
        result["status"] = "partial"
    return result
