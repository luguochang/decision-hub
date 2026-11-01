"""Pin the request-building Pack inputs, not a full Harness deployment."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)

ROLE = "research_request_pack"
SCHEMA = "research-request-pack.v1"


class ResearchPackError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


class ResearchPackService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database, self.clock = database, clock

    @staticmethod
    def read(row: RunRoleOutputRecord) -> dict[str, Any]:
        try:
            body = json.loads(row.payload_json)
            if (
                row.schema_version != SCHEMA
                or body["schema_version"] != SCHEMA
                or body["run_id"] != row.run_id
                or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
                or body["definition_hash"]
                != hashlib.sha256(canonical_recipe(body["definition"]).encode()).hexdigest()
            ):
                raise ValueError("research_pack_receipt_corrupt")
        except (ValueError, KeyError, TypeError) as exc:
            raise ResearchPackError("research_pack_receipt_corrupt") from exc
        return {**body, "recorded_at": cast(datetime, as_utc(row.created_at)).isoformat()}

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            return self.get_in_session(session, run_id)

    def get_in_session(self, session: Session, run_id: str) -> dict[str, Any] | None:
        """Borrow a caller session and explicitly dispatch the new bundle shape."""
        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=run_id, role=ROLE)
            .populate_existing()
            .one_or_none()
        )
        from .research_binding import (
            FrozenResearchBundle,
            has_prepared_bundle_in_session,
            read_research_bundle_in_session,
        )

        if has_prepared_bundle_in_session(session, run_id):
            bundle = read_research_bundle_in_session(session, run_id)
            if not isinstance(bundle, FrozenResearchBundle):
                raise ResearchPackError("research_pack_receipt_corrupt")
            return {
                **bundle.pack.model_dump(mode="json"),
                "recorded_at": bundle.recorded_at.isoformat(),
            }
        return self.read(row) if row else None

    def freeze(self, run_id: str, definition: dict[str, Any]) -> dict[str, Any] | None:
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.get(RunRecord, run_id) is None:
                raise KeyError(run_id)
            row = (
                session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=ROLE).one_or_none()
            )
            if row:
                return self.get_in_session(session, run_id)
            # Existing submissions must never gain a fabricated pre-execution receipt.
            if (
                session.get(DshSessionLinkRecord, run_id)
                or session.query(RunRoleOutputRecord)
                .filter_by(run_id=run_id, role="research_execution_method")
                .first()
            ):
                return None
            body = {
                "schema_version": SCHEMA,
                "run_id": run_id,
                "definition": definition,
                "definition_hash": hashlib.sha256(
                    canonical_recipe(definition).encode()
                ).hexdigest(),
            }
            encoded = canonical_recipe(body)
            row = RunRoleOutputRecord(
                output_id="pack_" + hashlib.sha256(run_id.encode()).hexdigest()[:32],
                run_id=run_id,
                role=ROLE,
                payload_json=encoded,
                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                schema_version=SCHEMA,
                created_at=self.clock(),
            )
            session.add(row)
            session.flush()
            return self.read(row)
