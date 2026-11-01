"""Metadata-only call ledger over DSH's durable attempt and compaction events."""

import re
from datetime import UTC, datetime

from packages.query_views.usage_accounting import _count, _total

FIELDS = {
    "input_tokens": "inputTokens", "output_tokens": "outputTokens",
    "cache_read_tokens": "cacheReadTokens", "cache_write_tokens": "cacheWriteTokens",
    "reasoning_tokens": "reasoningTokens", "total_tokens": "totalTokens",
}


def _label(value):
    # Route names only: reject URLs, credentials and arbitrary log text.
    return value if (
        isinstance(value, str) and "://" not in value
        and re.fullmatch(r"[\w./:-]{1,100}", value)
    ) else None


def call_ledger(events):
    calls, active, compressions = [], {}, {}
    context = {"provider": None, "model": None}

    def event_time():
        value = event.get("time")
        if type(value) not in (int, float):
            return None
        try:
            return datetime.fromtimestamp(value / 1000, UTC).isoformat()
        except (ValueError, OSError, OverflowError):
            return None

    def new(kind, turn=None, step=None, attempt=1):
        row = {
            "ordinal": len(calls) + 1, "kind": kind, "turn": turn, "step": step,
            "attempt": attempt, **context, "state": "incomplete", "usage_source": "none",
            "started_at": event_time(), "observed_at": None,
            **{key: None for key in FIELDS}, "issues": [],
        }
        calls.append(row)
        return row

    def sample(row, usage, source):
        row["issues"] = [i for i in row["issues"] if i != "invalid_or_missing_usage"]
        row.update(context if row["kind"] == "model" else {})
        row["usage_source"] = source
        row["observed_at"] = event_time()
        valid = _total(usage) is not None
        # Optional full-call totals are not fabricated. Independently reported
        # components remain useful when the adapter did not provide a total.
        components_only = isinstance(usage, dict) and usage.get("totalTokens") is None
        for key, field in FIELDS.items():
            value = usage.get(field) if isinstance(usage, dict) else None
            row[key] = value if (valid or components_only) and _count(value) else None
        if (row["reasoning_tokens"] is not None and row["output_tokens"] is not None
                and row["reasoning_tokens"] > row["output_tokens"]):
            row["reasoning_tokens"] = None
        if not valid:
            row["issues"].append("invalid_or_missing_usage")

    for event in events:
        kind, data = event.get("type"), event.get("data", {})
        if not isinstance(data, dict):
            continue
        if kind == "request/context":
            context = {key: _label(data.get(key)) for key in ("provider", "model")}
            continue
        identity = (data.get("turn"), data.get("step"))
        if kind in {"step/start", "step/end", "assistant/chunk", "assistant/message",
                    "llm/retry", "llm/retry-started"}:
            if not all(_count(value) for value in identity):
                continue
            row = active.get(identity)
            if row is None:
                row = active[identity] = new("model", *identity)
            elif kind == "step/start":
                row["issues"].append("duplicate_step_start")
            if kind == "llm/retry-started":
                # The previous attempt retains its last sample, including partial streams.
                row["state"] = "failed"
                row["issues"].append("retry_attempt_not_bill_reconciled")
                active[identity] = new("model", *identity, row["attempt"] + 1)
            elif kind == "llm/retry":
                row["state"] = "failed"
                row["issues"].append("retry_attempt_not_bill_reconciled")
            elif kind == "assistant/chunk":
                chunk = data.get("chunk")
                if isinstance(chunk, dict) and chunk.get("type") == "usage":
                    if row["usage_source"] != "final":
                        sample(row, chunk.get("usage"), "stream")
            elif kind == "assistant/message":
                if row["usage_source"] == "final":
                    row["issues"].append("duplicate_final_message")
                row["state"] = "failed" if data.get("interrupted") else "finalized"
                if data.get("usage") is not None:
                    sample(row, data["usage"], "final")
                else:
                    row["issues"].append("final_usage_missing")
            elif kind == "step/end" and row["state"] == "incomplete":
                row["issues"].append("final_message_missing")
        elif kind in {"compaction/start", "compaction/summary", "compaction/end"}:
            identity = data.get("compactionId")
            if not isinstance(identity, str):
                continue
            row = compressions.get(identity)
            if row is None:
                row = compressions[identity] = new("compaction")
            if kind == "compaction/summary":
                if row["usage_source"] == "final":
                    row["issues"].append("duplicate_final_message")
                row.update({key: _label(data.get(key)) for key in ("provider", "model")})
                row["state"] = "finalized"
                sample(row, data.get("usage"), "final")
                if data.get("llmStreamCall") is not True:
                    row["issues"].append("compaction_call_identity_unverified")
            elif kind == "compaction/end" and data.get("error"):
                row["state"] = "failed"
                row["issues"].append("compaction_failed_usage_unknown")

    for row in calls:
        if row["usage_source"] != "final":
            row["issues"].append("final_usage_missing")
        if row["provider"] is None or row["model"] is None:
            row["issues"].append("model_route_unknown")
        if row["state"] == "incomplete":
            row["issues"].append("call_not_finalized")
        row["issues"] = sorted(set(row["issues"]))
    return calls
