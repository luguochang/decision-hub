from tools.ops.audit_dsh_usage import summarize


def _events():
    usage = {"inputTokens": 12, "outputTokens": 3, "totalTokens": 20, "cacheReadTokens": 5}
    return [
        {"type": "turn/start", "data": {"turn": 1}},
        {"type": "step/start", "data": {"turn": 1, "step": 1}},
        {"type": "assistant/chunk", "data": {"turn": 1, "step": 1, "usage": usage}},
        {"type": "assistant/message", "data": {"turn": 1, "step": 1, "usage": usage}},
        {"type": "step/end", "data": {"turn": 1, "step": 1}},
        {"type": "turn/end", "data": {"turn": 1}},
    ]


def test_final_samples_are_not_added_to_streaming_samples_or_promoted_to_full_bill():
    result = summarize(_events())
    assert result["recorded_main_tokens"] == result["complete_main_tokens"] == 20
    assert result["total_run_tokens"] is None
    assert result["estimated_cost_usd"] is None


def test_missing_usage_and_retry_do_not_look_complete():
    events = _events()
    events[3]["data"].pop("usage")
    events.insert(3, {"type": "llm/retry-started", "data": {"turn": 1, "step": 1}})
    result = summarize(events)
    assert result["complete_main_tokens"] is None
    assert result["main_accounting_issues"] == [
        "invalid_or_missing_usage",
        "retry_attempt_accounting_required",
    ]


def test_duplicate_message_missing_end_and_contradictory_usage_are_unknown():
    events = _events()
    events[3]["data"]["usage"]["totalTokens"] = 2
    events.insert(4, events[3])
    result = summarize(events[:-1])
    assert result["complete_main_tokens"] is None
    assert set(result["main_accounting_issues"]) == {
        "incomplete_turn_lifecycle",
        "invalid_or_missing_usage",
        "multiple_messages_for_step",
    }


def test_aborted_step_cannot_disappear_from_the_accounting():
    events = _events()
    events.insert(-1, {"type": "step/start", "data": {"turn": 1, "step": 2}})
    result = summarize(events)
    assert result["complete_main_tokens"] is None
    assert result["recorded_main_tokens"] == 20
    assert result["main_accounting_issues"] == [
        "incomplete_step_lifecycle",
        "missing_final_usage_for_step",
    ]
