"""Bounded pure parsing hints, used only after a fresh body read and SHA check."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Lock
from typing import Any


class MarketOriginalParse:
    MAX_BODIES = 4096
    MAX_ROWS = 4096
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, int | None], tuple[int | bytes, int]] = OrderedDict()
        self._bodies = 0
        self._rows = 0
        self._bytes = 0
        self._lock = Lock()

    def _get(self, key: tuple[str, int | None]) -> int | bytes | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def _put(self, key: tuple[str, int | None], value: int | bytes) -> None:
        # Charge ample fixed object/container overhead as well as every retained byte.
        charge = 512 + len(key[0]) + (len(value) if isinstance(value, bytes) else 32)
        if charge > self.MAX_BYTES:
            return
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return
            while self._entries and (
                self._bytes + charge > self.MAX_BYTES
                or (key[1] is None and self._bodies >= self.MAX_BODIES)
                or (key[1] is not None and self._rows >= self.MAX_ROWS)
            ):
                old_key, (_, old_charge) = self._entries.popitem(last=False)
                self._bytes -= old_charge
                if old_key[1] is None:
                    self._bodies -= 1
                else:
                    self._rows -= 1
            self._entries[key] = (value, charge)
            self._bytes += charge
            if key[1] is None:
                self._bodies += 1
            else:
                self._rows += 1

    def selected_rows(self, body: bytes, digest: str, indices: tuple[int, ...]) -> dict[int, Any]:
        """Authenticate supplied fresh bytes; retain no body, DTO or source metadata."""
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("market_store_original_hash_mismatch")
        envelope = self._get((digest, None))
        hits = {index: self._get((digest, index)) for index in indices}
        if isinstance(envelope, int) and all(isinstance(value, bytes) for value in hits.values()):
            rows = {}
            for index, value in hits.items():
                assert isinstance(value, bytes)
                rows[index] = json.loads(value)
            return rows
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("market_store_original_json_invalid") from exc
        if (
            not isinstance(payload, dict)
            or str(payload.get("code")) != "0"
            or not isinstance(payload.get("data"), list)
        ):
            raise ValueError("market_store_original_shape_invalid")
        data = payload["data"]
        # Missing rows are never memoized; a later request for them must parse again.
        rows = {index: data[index] for index in indices}
        self._put((digest, None), len(data))
        for index, row in rows.items():
            if isinstance(row, list) and len(row) == 9 and str(row[8]) == "1":
                encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                self._put((digest, index), encoded)
        return rows
