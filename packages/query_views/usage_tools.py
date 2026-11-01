"""Metadata-only Host attempts, including failures before gateway execution."""


def tool_ledger(events):
    calls = {}
    results = {}
    issues = set()
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        if event.get("type") == "tool/call":
            identity = data.get("callId")
            if not isinstance(identity, str) or not identity:
                issues.add("tool_call_identity_missing")
                continue
            if identity in calls:
                issues.add("duplicate_tool_call")
                continue
            name = data.get("name")
            calls[identity] = {
                "ordinal": len(calls) + 1,
                "call_id": identity,
                "kind": "research" if name == "decision_hub_research" else "other",
                "state": "incomplete",
                "failure_kind": None,
            }
        elif event.get("type") == "tool/result":
            message = data.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                issues.add("tool_result_unreadable")
                continue
            for part in content:
                if not isinstance(part, dict) or not isinstance(part.get("toolCallId"), str):
                    continue
                identity = part["toolCallId"]
                if type(part.get("isError")) is not bool:
                    issues.add("tool_result_state_missing")
                    continue
                failed = part["isError"]
                # Classify only the Host's well-known validation prefix. Never
                # return arguments, result bodies or arbitrary error messages.
                body = part.get("content")
                texts = (
                    [body]
                    if isinstance(body, str)
                    else [
                        item.get("text")
                        for item in body
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    if isinstance(body, list)
                    else []
                )
                validation = any(
                    isinstance(text, str) and text.startswith("Error: invalid arguments:")
                    for text in texts
                )
                outcome = (
                    "failed" if failed else "succeeded",
                    "argument_validation"
                    if failed and validation
                    else "tool_error"
                    if failed
                    else None,
                )
                if identity in results and results[identity] != outcome:
                    issues.add("conflicting_tool_results")
                    results[identity] = ("incomplete", None)
                else:
                    results[identity] = outcome
    for identity, call in calls.items():
        if identity in results:
            call["state"], call["failure_kind"] = results[identity]
    if results.keys() - calls.keys():
        issues.add("tool_result_without_call")
    return {"calls": list(calls.values()), "issues": sorted(issues)}
