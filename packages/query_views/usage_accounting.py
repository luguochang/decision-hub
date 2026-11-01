"""Pure conservative accounting over finalized provider usage metadata."""

from collections import Counter


def _count(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def _total(usage):
    if not isinstance(usage, dict):
        return None
    required = [usage.get(key) for key in ("inputTokens", "outputTokens", "totalTokens")]
    if not all(_count(value) for value in required):
        return None
    prompt, output, total = required
    cache = [usage.get(key) for key in ("cacheReadTokens", "cacheWriteTokens")]
    if any(value is not None and not _count(value) for value in cache):
        return None
    known = prompt + output + sum(value for value in cache if value is not None)
    if known > total or (all(value is not None for value in cache) and known != total):
        return None
    reasoning = usage.get("reasoningTokens")
    if reasoning is not None and (not _count(reasoning) or reasoning > output):
        return None
    return total


def summarize(events):
    """Summarize finalized main-session samples without double-counting chunks."""
    starts, ends, messages = Counter(), Counter(), {}
    turn_starts, turn_ends = Counter(), Counter()
    issues = set()
    types, tools = Counter(), Counter()
    for event in events:
        kind, data = event.get("type"), event.get("data", {})
        if not isinstance(kind, str):
            issues.add("malformed_event")
            continue
        types[kind] += 1
        if not isinstance(data, dict):
            issues.add("malformed_event")
            continue
        identity = (data.get("turn"), data.get("step"))
        if kind in {"step/start", "step/end", "assistant/message"} and not all(
            _count(value) for value in identity
        ):
            issues.add("invalid_attempt_identity")
            continue
        if kind == "step/start":
            starts[identity] += 1
        elif kind == "step/end":
            ends[identity] += 1
        elif kind == "turn/start":
            turn_starts[data.get("turn")] += 1
        elif kind == "turn/end":
            turn_ends[data.get("turn")] += 1
        elif kind == "assistant/message":
            if identity in messages:
                issues.add("multiple_messages_for_step")
            messages[identity] = _total(data.get("usage"))
        elif kind == "tool/call":
            # Tool names are metadata; arguments and results are never exported.
            tools[str(data.get("name", "unknown"))] += 1
        elif kind.startswith("llm/retry"):
            issues.add("retry_attempt_accounting_required")
    if not turn_starts or turn_starts != turn_ends or any(n != 1 for n in turn_starts.values()):
        issues.add("incomplete_turn_lifecycle")
    if not starts or starts != ends or any(n != 1 for n in starts.values()):
        issues.add("incomplete_step_lifecycle")
    if set(starts) != set(messages):
        issues.add("missing_final_usage_for_step")
    if any(value is None for value in messages.values()):
        issues.add("invalid_or_missing_usage")
    known = sum(value for value in messages.values() if value is not None)
    if not _count(known):
        issues.add("aggregate_overflow")
    return {
        "scope": "main_session_finalized_messages_only",
        "model_steps": sum(starts.values()),
        "finalized_messages": len(messages),
        "valid_usage_samples": sum(value is not None for value in messages.values()),
        "recorded_main_tokens": known
        if _count(known) and any(value is not None for value in messages.values())
        else None,
        "complete_main_tokens": known if not issues else None,
        "main_accounting_issues": sorted(issues),
        "tool_calls_by_name": dict(tools),
        "retry_events": sum(count for name, count in types.items() if name.startswith("llm/retry")),
        "total_run_tokens": None,
        "estimated_cost_usd": None,
        "remaining_reconciliation": [
            "subagent_and_fork_usage",
            "compaction_and_non_main_model_calls",
            "provider_pricing_and_bill_reconciliation",
        ],
    }
