"""Short action command transactions and opportunity registration.

HTTP transports authenticate the owner before calling this service. Publication
from DSH requires a separately authenticated research source; this module offers
no endpoint for supplying an arbitrary action plan.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from ..persistence.action_repository import (
    ActionRepository,
    StoredCommandReceipt,
    make_command_receipt,
)
from ..persistence.db import Database, utcnow
from ..persistence.original_preparation import begin_immediate
from ..ports.action_plan import ActionPlanHead, Opportunity
from ..ports.market_path import aware_utc, canonical_hash


def _identity(value: object) -> str:
    if (not isinstance(value, str) or not value.strip()
            or value != value.strip() or len(value) > 128):
        raise ValueError("action_command_identity_invalid")
    return value


class ActionPlanService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def register_opportunity(self, value: Opportunity) -> Opportunity:
        """Internal scheduling entry; a replay retains the original denominator."""
        checked = Opportunity.model_validate(value.model_dump(mode="python"))
        with self.database.session() as session:
            begin_immediate(session)
            return ActionRepository(session).register_opportunity(checked)

    def get_command(self, *, owner_id: str, request_id: str) -> StoredCommandReceipt | None:
        """Read back the original submission; no new command key or model call."""
        owner, request = _identity(owner_id), _identity(request_id)
        with self.database.session() as session:
            return ActionRepository(session).get_command(owner, request)

    def record_command(
        self, *, owner_id: str, request_id: str, plan_id: str,
        expected_revision_id: str, expected_state_version: int,
        operation: str, payload: Mapping | None = None,
    ) -> StoredCommandReceipt:
        owner, request, plan, revision = (
            _identity(value) for value in (owner_id, request_id, plan_id, expected_revision_id)
        )
        if type(expected_state_version) is not int or expected_state_version < 1:
            raise ValueError("action_command_version_invalid")
        if not isinstance(operation, str) or operation not in {"pause", "resume", "display", "ack"}:
            raise ValueError("action_command_operation_unsupported")
        # These four commands have no client-authored timestamps or economics.
        # Adoption/exit commands have a distinct later user-reported contract.
        if payload is not None and (not isinstance(payload, Mapping) or payload):
            raise ValueError("action_command_payload_invalid")
        digest = canonical_hash({
            "owner_id": owner, "request_id": request, "plan_id": plan,
            "expected_revision_id": revision, "expected_state_version": expected_state_version,
            "operation": operation, "payload": {},
        })
        with self.database.session() as session:
            begin_immediate(session)
            repository = ActionRepository(session)
            original = repository.get_command(owner, request)
            if original is not None:
                if original.payload_hash != digest:
                    raise ValueError("action_command_key_conflict")
                return original
            head = repository.get_head(plan)
            if head is None:
                raise ValueError("action_plan_not_found")
            if head.current_revision_id != revision:
                raise ValueError("action_revision_superseded")
            if head.state_version != expected_state_version:
                raise ValueError("action_state_version_conflict")
            now = aware_utc(self.clock())
            if now >= head.valid_until:
                raise ValueError("action_plan_expired")
            if operation in {"pause", "resume"}:
                if head.state not in {"armed", "triggered"}:
                    raise ValueError("action_terminal_plan_restart_forbidden")
                monitor_state = "paused" if operation == "pause" else "active"
                # A user command cannot certify restored data. The monitor
                # leaves degraded only after validating a new input snapshot.
                keeps_data_gap = operation == "resume" and head.monitor_state == "degraded"
                if head.monitor_state != monitor_state and not keeps_data_gap:
                    updated = head.model_dump(mode="python")
                    updated.update(
                        monitor_state=monitor_state,
                        state_version=head.state_version + 1,
                        lease_generation=head.lease_generation + 1,
                        lease_owner=None, lease_expires_at=None,
                        enter_streak=0,
                        next_due=None if operation == "pause" else now,
                    )
                    head = repository.change_head(
                        plan, expected_revision_id=revision,
                        expected_state_version=expected_state_version,
                        next_head=ActionPlanHead.model_validate(updated),
                    )
            # A display/ack is a server-received UI event. Neither is an order,
            # a price fill, a provider delivery receipt or a profit observation.
            response = {
                "status": "recorded", "operation": operation,
                "server_time": now.isoformat().replace("+00:00", "Z"),
                "head": head.model_dump(mode="json"),
            }
            receipt = make_command_receipt(
                owner_id=owner, request_id=request, plan_id=plan, operation=operation,
                payload_hash=digest, response=response, accepted_at=now,
            )
            return repository.append_command_receipt(receipt)
