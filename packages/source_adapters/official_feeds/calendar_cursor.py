"""Durable per-entry calendar revisions, independent of occurrence ordering."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope

PREFIX = "calendar-v2:"


def select_calendar_changes(
    candidates: Sequence[TextEnvelope], *, source_id: str, cursor: str | None, max_batch: int
) -> tuple[tuple[TextEnvelope, ...], str]:
    """Advance only emitted versions; an earlier inserted date is still new.

    Old date-watermark cursors get one bounded rescan. The Hub's existing
    content idempotency still applies. Never reset RSS/news feed watermarks.
    """
    seen: dict[str, str] = {}
    if cursor and cursor.startswith(PREFIX):
        try:
            state = json.loads(cursor[len(PREFIX) :])
            if state["source_id"] != source_id or not isinstance(state["seen"], dict):
                raise ValueError
            for uid, digest in state["seen"].items():
                if not isinstance(uid, str) or not isinstance(digest, str):
                    raise ValueError
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError
                seen[uid] = digest
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("source_parse_error:calendar_cursor_invalid") from exc
    elif cursor and not re.match(r"^\d{4}-\d{2}-\d{2}T.+\|.+$", cursor):
        raise ValueError("source_parse_error:calendar_cursor_version_unknown")

    current: dict[str, TextEnvelope] = {}
    for item in candidates:
        uid = item.macro_event.external_event_id if item.macro_event else item.event_hint
        if not uid:
            raise ValueError("source_parse_error:calendar_identity_missing")
        if uid in current and current[uid].content_hash != item.content_hash:
            raise ValueError("source_parse_error:calendar_identity_conflict")
        current[uid] = item
    # Retain only the source's current coverage. Disappearance alone does not
    # assert cancellation; that needs an explicit source revision downstream.
    seen = {uid: digest for uid, digest in seen.items() if uid in current}
    emitted = []
    for uid, item in current.items():
        previous = seen.get(uid)
        if previous == item.content_hash or len(emitted) >= max_batch:
            continue
        if previous:
            item = item.model_copy(update={"revision_of": f"sha256:{previous}"})
            if item.macro_event:
                item = item.model_copy(
                    update={
                        "macro_event": item.macro_event.model_copy(update={"revision_flag": True})
                    }
                )
        emitted.append(item)
        seen[uid] = item.content_hash
    after = PREFIX + json.dumps(
        {"source_id": source_id, "seen": seen}, sort_keys=True, separators=(",", ":")
    )
    return tuple(emitted), after
