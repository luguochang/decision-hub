from __future__ import annotations

import asyncio
import collections
import json
import os
import tempfile
from pathlib import Path

from packages.runtime_adapters.dsh_runtime import DshRuntimeConfig, DshSdkClient


async def _run() -> dict[str, object]:
    if os.getenv("DECISION_HUB_DSH_LIVE_CANARY") != "1":
        raise RuntimeError("set DECISION_HUB_DSH_LIVE_CANARY=1 to authorize external model use")

    base = DshRuntimeConfig.from_env()
    with tempfile.TemporaryDirectory(prefix="decision-hub-dsh-live-") as raw_temp:
        temp = Path(raw_temp)
        config = base.model_copy(update={"workspace": temp, "session_root": temp / "sessions"})
        client = DshSdkClient(config)
        try:
            run = await client.run(
                "This is a runtime canary. First call todo_write with two explicit canary tasks. "
                "Then call the subagent tool and ask it to return SUBAGENT_CANARY_OK. "
                "After reading that result, call todo_write again to mark both tasks completed. "
                "Finally reply with exactly DSH_CANARY_OK. Do not use any unavailable tool.",
                session_id="decision-hub-r2-r01-live-canary",
            )
        finally:
            await client.close()

        event_types: collections.Counter[str] = collections.Counter()
        notification_methods: collections.Counter[str] = collections.Counter()
        for notification in run.notifications:
            notification_methods[notification.method] += 1
            if notification.method != "session.event":
                continue
            event = notification.payload.get("event")
            if isinstance(event, dict) and isinstance(event.get("type"), str):
                event_types[event["type"]] += 1

        passed = (
            run.finish_reason == "completed"
            and run.final_response.strip() == "DSH_CANARY_OK"
            and event_types["tool/call"] >= 2
            and event_types["tool/result"] >= 2
            and notification_methods["subagent.started"] >= 1
            and notification_methods["subagent.finished"] >= 1
        )
        return {
            "status": "passed" if passed else "failed",
            "sdk_version": config.sdk_version,
            "provider": config.provider,
            "model": config.model,
            "finish_reason": run.finish_reason,
            "final_response_matched": run.final_response.strip() == "DSH_CANARY_OK",
            "tool_calls": event_types["tool/call"],
            "tool_results": event_types["tool/result"],
            "subagents_started": notification_methods["subagent.started"],
            "subagents_finished": notification_methods["subagent.finished"],
            "turns_completed": event_types["turn/end"],
            "steps_completed": event_types["step/end"],
            "session_files": sum(1 for item in (temp / "sessions").rglob("*") if item.is_file()),
        }


def main() -> int:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
