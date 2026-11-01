from packages.query_views.usage_calls import call_ledger


def event(kind, **data):
    return {"type": kind, "time": 1790740000000, "data": data}


def usage(tokens=20):
    return dict(inputTokens=tokens - 8, outputTokens=3, cacheReadTokens=5,
                totalTokens=tokens, reasoningTokens=2)


def start():
    return [event("step/start", turn=1, step=1),
            event("request/context", provider="codexai", model="gpt-5.5")]


def test_final_replaces_stream_and_uses_context_route_without_exporting_content():
    rows = call_ledger(start() + [
        event("assistant/chunk", turn=1, step=1, chunk={"type": "usage", "usage": usage(15)}),
        event("assistant/message", turn=1, step=1, usage=usage(), message="PRIVATE"),
    ])
    assert len(rows) == 1
    row = rows[0]
    assert row["total_tokens"] == 20 and row["input_tokens"] == 12
    assert row["cache_read_tokens"] == 5 and row["cache_write_tokens"] is None
    assert row["reasoning_tokens"] == 2 and row["output_tokens"] == 3
    assert row["model"] == "gpt-5.5" and row["usage_source"] == "final"
    assert row["started_at"] == row["observed_at"] == "2026-09-30T03:46:40+00:00"
    assert "PRIVATE" not in str(rows)


def test_retry_opens_separate_attempt_and_keeps_failed_stream_sample():
    rows = call_ledger(start() + [
        event("assistant/chunk", turn=1, step=1, chunk={"type": "usage", "usage": usage(15)}),
        event("llm/retry", turn=1, step=1, failure={"message": "SECRET"}),
        event("llm/retry-started", turn=1, step=1, retry=1),
        event("assistant/message", turn=1, step=1, usage=usage()),
    ])
    assert len(rows) == 2
    assert [row["total_tokens"] for row in rows] == [15, 20]
    assert [row["attempt"] for row in rows] == [1, 2]
    assert rows[0]["state"] == "failed" and rows[0]["usage_source"] == "stream"
    assert rows[1]["state"] == "finalized" and rows[1]["issues"] == []
    assert "SECRET" not in str(rows)


def test_compaction_is_separate_and_model_free_pruning_is_excluded():
    rows = call_ledger([
        event("compaction/start", compactionId="comp-1"),
        event("compaction/summary", compactionId="comp-1", provider="local-route",
              model="model-b", usage=usage(), llmStreamCall=True, rawOutput="PRIVATE"),
        event("compaction/end", compactionId="comp-1"),
        event("compaction/prune", shadowedTokenCount=9000),
        event("compaction/start", compactionId="comp-2"),
        event("compaction/end", compactionId="comp-2", error="PRIVATE ERROR"),
    ])
    assert len(rows) == 2
    assert rows[0]["kind"] == "compaction" and rows[0]["total_tokens"] == 20
    assert rows[0]["provider"] == "local-route" and rows[0]["issues"] == []
    assert rows[1]["state"] == "failed" and rows[1]["total_tokens"] is None
    assert "PRIVATE" not in str(rows)


def test_missing_conflicting_and_interrupted_calls_remain_visible():
    bad = usage()
    bad["totalTokens"] = 1
    rows = call_ledger(start() + [
        event("assistant/message", turn=1, step=1, usage=bad, interrupted=True),
        event("step/start", turn=1, step=2), event("step/end", turn=1, step=2),
    ])
    assert len(rows) == 2
    assert rows[0]["total_tokens"] is None and rows[0]["state"] == "failed"
    assert "invalid_or_missing_usage" in rows[0]["issues"]
    assert "final_message_missing" in rows[1]["issues"]


def test_duplicate_final_and_untrusted_route_do_not_look_clean():
    rows = call_ledger([
        event("request/context", provider="https://private.example/key", model="PRIVATE TEXT"),
        event("assistant/message", turn=1, step=1, usage=usage()),
        event("assistant/message", turn=1, step=1, usage=usage()),
    ])
    assert len(rows) == 1 and rows[0]["model"] is None and rows[0]["provider"] is None
    assert "duplicate_final_message" in rows[0]["issues"]
    assert "private.example" not in str(rows)


def test_reported_components_survive_missing_total_without_inventing_a_sum():
    partial = usage()
    partial.pop("totalTokens")
    rows = call_ledger(start() + [event("assistant/message", turn=1, step=1, usage=partial)])
    assert rows[0]["input_tokens"] == 12 and rows[0]["cache_read_tokens"] == 5
    assert rows[0]["total_tokens"] is None
    assert "invalid_or_missing_usage" in rows[0]["issues"]
