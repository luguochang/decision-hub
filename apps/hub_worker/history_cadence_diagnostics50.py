"""Optional bounded metadata for the unchanged hourly outcome callback.

This observer grants nothing, produces nothing and never reads database/model
material. Protected Runtime40 remains the only callback executor.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic

MAX_LINE_BYTES = 8192
MAX_FILE_BYTES = 2 * 1024 * 1024
_APPEND_LOCK = Lock()
_OUTCOMES = frozenset(("blocked", "unconfirmed", "no_due_opportunity",
    "final_score_saved", "final_score_already_saved", "accepted",
    "publication_saved", "partial_saved", "readonly_prepare_rejected"))
_REASONS = frozenset(("history_outcome_runtime_preparation_unavailable",
    "history_outcome_action_requires_readonly_reconciliation"))


class HistoryCadenceDiagnostics50:
    def __init__(self, outcome):
        self._sequence = 0
        self._path = None
        configured = os.getenv("DECISION_HUB_REALTIME_DIAGNOSTICS_PATH")
        try:
            parent = Path(configured) if configured else None
            if parent is not None and parent.is_absolute() and parent.parent.is_dir():
                self._path = parent.with_name(parent.stem + f".history-outcome50-{os.getpid()}.jsonl")
        except (OSError, ValueError):
            pass
        self._emit("bound", self._snapshot(outcome))

    @staticmethod
    def _snapshot(outcome):
        try:
            grants = getattr(getattr(outcome, "bootstrap", None), "protocol_ids", None)
            cursor = getattr(outcome, "cursor", None)
            receipt = getattr(outcome, "last_receipt", None)
            value = {
                "outcome_attached": outcome is not None,
                "outcome_type": type(outcome).__name__ if outcome is not None else None,
                "runtime_busy": getattr(outcome, "_busy", None) is True,
                "protocol_grants_are_tuple": type(grants) is tuple,
                "protocol_grant_count": len(grants) if type(grants) is tuple else None,
                "cursor": cursor if type(cursor) is int else None,
                "outcome_config_environment_present": os.getenv("DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG") is not None,
                "outcome_hash_environment_present": os.getenv("DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG_SHA256") is not None,
                "last_outcome": None,
                "last_reason_code": None,
            }
            if type(receipt) is dict:
                if receipt.get("outcome") in _OUTCOMES:
                    value["last_outcome"] = receipt["outcome"]
                if receipt.get("reason_code") in _REASONS:
                    value["last_reason_code"] = receipt["reason_code"]
            return value
        except Exception:
            return {"metadata_unavailable": True}

    def _emit(self, stage, values):
        try:
            if self._path is None:
                return
            with _APPEND_LOCK:
                self._sequence += 1
                value = {"schema_version":"history-outcome-cadence-diagnostics50.v1",
                    "observed_at":datetime.now(UTC).isoformat(), "process_id":os.getpid(),
                    "sequence":self._sequence, "stage":stage, "commit_verified":False,
                    "proof_scope":"callback_metadata_only", **values}
                raw = json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode() + b"\n"
                if len(raw) > MAX_LINE_BYTES:
                    return
                with self._path.open("a+b") as stream:
                    stream.seek(0,2)
                    if stream.tell() + len(raw) <= MAX_FILE_BYTES:
                        stream.write(raw)
        except Exception:
            pass  # Optional log failure cannot change execution or admission.

    def enter(self, outcome):
        value = self._snapshot(outcome)
        self._emit("enter", value)
        return monotonic(), value

    def exit(self, outcome, token, processed):
        value = self._snapshot(outcome)
        before = token[1]
        if before.get("outcome_attached") is False:
            branch = "no_outcome_binding"
        elif before.get("runtime_busy") is True:
            branch = "already_busy_skip"
        elif before.get("protocol_grant_count") == 0:
            branch = "empty_protocol_grants"
        else:
            branch = value.get("last_outcome") or "receipt_unavailable"
        self._emit("exit", {**value, "elapsed_seconds":max(0.0,monotonic()-token[0]),
            "before_cursor":before.get("cursor"), "observed_branch":branch,
            "processed":processed if type(processed) is int else None,
            "result_type":type(processed).__name__})

    def raised(self, outcome, token, error):
        try:
            orig = getattr(error,"orig",None)
            native = orig if orig is not None else error
            code = getattr(native,"sqlite_errorcode",None)
            name = getattr(native,"sqlite_errorname",None)
            value = {**self._snapshot(outcome),
                "elapsed_seconds":max(0.0,monotonic()-token[0]),
                "exception_type":type(error).__name__, "exception_module":type(error).__module__,
                "message_sha256":hashlib.sha256(str(error).encode()).hexdigest(),
                "sqlite_errorcode":code if type(code) is int else None,
                "sqlite_errorname":name if type(name) is str and name.startswith("SQLITE_") and len(name)<=64 else None}
            self._emit("raised",value)
        except Exception:
            pass
