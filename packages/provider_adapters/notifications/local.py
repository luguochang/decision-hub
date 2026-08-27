from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationMessage,
    NotificationResult,
)


class LocalNotificationAdapter:
    channel = "local"

    def __init__(self, path: Path) -> None:
        self.path = path

    async def deliver(self, message: NotificationMessage) -> NotificationResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "artifact_id": message.artifact_id,
                        "channel": message.channel,
                        "dedupe_key": message.dedupe_key,
                        "subject": message.subject,
                        "body": message.body,
                        "delivered_at": datetime.now(UTC).isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return NotificationResult(delivered=True, provider_message_id=message.dedupe_key)
