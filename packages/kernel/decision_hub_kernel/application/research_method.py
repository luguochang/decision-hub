"""Immutable, pre-submission DSH method receipts in the existing Run ledger.

The receipt covers the Hub prompt policy and attested Host build. It does not
claim a frozen provider configuration or consumption of an Evolution pointer.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import (
    ResearchExecutionMethodReceiptV2,
)
from packages.kernel.decision_hub_kernel.application.research_context import CONTEXT_POLICY_HASH
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)

ROLE = "research_execution_method"
SCHEMA = "research-execution-method.v1"


def canonical_recipe(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def recipe_hash(value: Any) -> str:
    return hashlib.sha256(canonical_recipe(value).encode("utf-8")).hexdigest()


def method_definition(
    request: ResearchSessionRequest,
    submit: DshSessionSubmit,
    identity: DshUpstreamIdentity,
    *,
    runtime_id: str,
    runtime_version: str,
    profile_ref: str,
    prompt_policy_hash: str,
) -> dict[str, Any]:
    budget = request.execution_budget.model_dump(mode="json")
    # Evidence rounds consume a fixed initial ceiling, not a new method each round.
    budget["max_tool_calls"] = submit.max_tool_calls
    return {
        "schema_version": SCHEMA,
        "runtime_id": runtime_id,
        "runtime_version": runtime_version,
        "profile_ref": profile_ref,
        "prompt_policy_hash": prompt_policy_hash,
        "upstream_identity": identity.model_dump(mode="json"),
        "domain_pack_ref": request.domain_pack_ref,
        "role_profile_ref": request.role_profile_ref,
        "execution_mode": request.execution_mode,
        "research_mode": request.research_mode,
        # Selection receipts change as gaps close; bind the implementation here.
        # Each round's selected policy reference remains in its persisted prompt.
        "context_policy_hash": CONTEXT_POLICY_HASH,
        "allowed_capabilities": sorted(set(request.allowed_capabilities)),
        "execution_budget": budget,
        "output_schema_ref": request.output_schema_ref,
        "agent_preset": submit.agent_preset,
        "permission_ref": submit.permission_ref,
        "provider_configuration_attested": False,
        "evolution_pointer_consumed": False,
        "candidate_id": None,
    }


class ResearchMethodService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database, self.clock = database, clock

    @staticmethod
    def _read(row: RunRoleOutputRecord) -> dict[str, Any]:
        try:
            body = json.loads(row.payload_json)
            if (
                row.schema_version != SCHEMA
                or row.payload_hash != hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
                or body["method_hash"] != recipe_hash(body["definition"])
                or body["run_id"] != row.run_id
                or body["schema_version"] != SCHEMA
            ):
                raise ValueError("research_method_receipt_corrupt")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("research_method_receipt_corrupt") from exc
        return {
            "output_id": row.output_id,
            # The legacy row's created_at column is non-null; keep its original projection.
            "recorded_at": (
                as_utc(row.created_at).isoformat()  # pyright: ignore[reportOptionalMemberAccess]
            ),
            **body,
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            return self.get_in_session(session, run_id)

    def get_in_session(self, session: Session, run_id: str) -> dict[str, Any] | None:
        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=run_id, role=ROLE)
            .populate_existing()
            .one_or_none()
        )
        if row is None:
            return None
        if row.schema_version == "research-execution-method.v3":
            from .research_execution_binding import (
                method_v2_view,
                read_prepared_execution_in_session,
            )

            return method_v2_view(read_prepared_execution_in_session(session, run_id).method)
        if row.schema_version == "research-execution-method.v2":
            from .research_execution_binding import method_v2_view, read_method_v2_in_session

            receipt = read_method_v2_in_session(session, run_id)
            return method_v2_view(receipt) if receipt is not None else None
        return self._read(row)

    def freeze_in_session(
        self, session: Session, receipt: ResearchExecutionMethodReceiptV2
    ) -> ResearchExecutionMethodReceiptV2:
        """Explicit V2 borrowed transaction API; legacy freeze remains separate."""
        from .research_execution_binding import freeze_method_v2_in_session

        frozen = freeze_method_v2_in_session(session, receipt)
        if not isinstance(frozen, ResearchExecutionMethodReceiptV2):
            raise ValueError("research_execution_method_schema_conflict")
        return frozen

    def freeze(
        self,
        run_id: str,
        definition: dict[str, Any],
        *,
        request_hash: str,
        existing_session: bool = False,
    ) -> dict[str, Any] | None:
        body = {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "request_hash": request_hash,
            "method_hash": recipe_hash(definition),
            "definition": definition,
        }
        encoded = canonical_recipe(body)
        with self.database.session() as session:
            if session.bind.dialect.name == "sqlite":  # pyright: ignore[reportOptionalMemberAccess]
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.get(RunRecord, run_id) is None:
                raise KeyError(run_id)
            row = (
                session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=ROLE).one_or_none()
            )
            if row:
                stored = self._read(row)
                if (
                    stored["method_hash"] != body["method_hash"]
                    or stored["request_hash"] != request_hash
                ):
                    raise ValueError("research_method_changed_during_run")
                return stored
            if existing_session:
                # Never invent a pre-execution receipt for an already submitted legacy Run.
                return None
            row = RunRoleOutputRecord(
                output_id="method_" + hashlib.sha256(run_id.encode()).hexdigest()[:32],
                run_id=run_id,
                role=ROLE,
                payload_json=encoded,
                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                schema_version=SCHEMA,
                created_at=self.clock(),
            )
            session.add(row)
            session.flush()
            return self._read(row)
