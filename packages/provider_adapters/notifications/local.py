from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationEnvelopeV2,
    NotificationEnvelopeV3,
    NotificationMessage,
    NotificationResult,
    verify_notification_envelope,
)


class LocalNotificationAdapter:
    channel = "local"

    def __init__(self, path: Path) -> None:
        self.path = path

    async def deliver(
        self, message: NotificationMessage | NotificationEnvelopeV2 | NotificationEnvelopeV3
    ) -> NotificationResult:
        return self.deliver_sync(message)

    def deliver_sync(
        self, message: NotificationMessage | NotificationEnvelopeV2 | NotificationEnvelopeV3
    ) -> NotificationResult:
        now = datetime.now(UTC).isoformat()
        if isinstance(message, (NotificationEnvelopeV2, NotificationEnvelopeV3)):
            message = verify_notification_envelope(message)
            document = message.model_dump(mode="json")
            document["provider_accepted_at"] = now
        else:
            document = {
                "artifact_id": message.artifact_id,
                "channel": message.channel,
                "dedupe_key": message.dedupe_key,
                "subject": message.subject,
                "body": message.body,
                "delivered_at": now,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    document,
                    # Keep the JSONL portable when a consumer uses the Windows
                    # process default encoding instead of UTF-8 explicitly.
                    ensure_ascii=True,
                )
                + "\n"
            )
        return NotificationResult(delivered=True, provider_message_id=message.dedupe_key)
