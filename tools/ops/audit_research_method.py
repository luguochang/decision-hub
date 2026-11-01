"""Read-only audit of an execution-method receipt against its DSH submission."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_

from packages.kernel.decision_hub_kernel.application.research_binding import (
    has_prepared_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    EXECUTION_SCHEMA,
    INTEGRITY_SCHEMA,
    METHOD_SCHEMA,
    method_v2_view,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE,
    ResearchMethodService,
)
from packages.kernel.decision_hub_kernel.application.research_pack import (
    ROLE as PACK_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_policy import policy_prompt_status
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)


def _required_utc(value: datetime | None) -> datetime:
    normalized = as_utc(value)
    if normalized is None:
        raise ValueError("research_receipt_original_time_missing")
    return normalized


def audit(database: Database, run_id: str) -> dict[str, Any]:
    """Public audit JSON; V2 fields are projected only after the typed full reader."""
    with database.session() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        run = session.get(RunRecord, run_id)
        if run is None:
            raise ValueError("run_not_found")
        row = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=ROLE).one_or_none()
        v2_execution = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == run_id,
            or_(
                and_(RunRoleOutputRecord.role.startswith("dsh_execution_request:"),
                     RunRoleOutputRecord.schema_version == EXECUTION_SCHEMA),
                and_(RunRoleOutputRecord.role.startswith("dsh_prompt_integrity:"),
                     RunRoleOutputRecord.schema_version == INTEGRITY_SCHEMA),
            ),
        ).first() is not None
        if row is None:
            if v2_execution or has_prepared_bundle_in_session(session, run_id) and (
                session.get(DshSessionLinkRecord, run_id) is not None
                or session.query(DshSessionPromptRecord).filter_by(run_id=run_id).first()
                or session.query(RunRoleOutputRecord).filter(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role.startswith("dsh_execution_request:"),
                ).first()
            ):
                raise ValueError("research_execution_method_missing")
            return {"run_id": run_id, "status": "method_not_recorded", "verified": False}
        prepared = None
        if row.schema_version == METHOD_SCHEMA or v2_execution or has_prepared_bundle_in_session(
            session, run_id,
        ):
            prepared = read_prepared_execution_in_session(session, run_id)
            receipt = method_v2_view(prepared.method)
        else:
            receipt = ResearchMethodService._read(row)  # pyright: ignore[reportPrivateUsage]
        link = session.get(DshSessionLinkRecord, run_id)
        result = session.get(ResearchResultRecord, run_id)
        prompts = session.query(DshSessionPromptRecord).filter_by(run_id=run_id).all()
        checks = {
            "request_matches_host_link": bool(
                link and link.request_hash == receipt["request_hash"]
            ),
            "host_build_matches": bool(
                link
                and json.loads(link.upstream_identity_json)
                == receipt["definition"]["upstream_identity"]
            ),
            "saved_before_host_acceptance": (
                _required_utc(row.created_at) <= _required_utc(link.accepted_at)
                if link and link.accepted_at
                else None
            ),
            "saved_prompts_match_request": bool(prompts)
            and all(p.request_hash == receipt["request_hash"] for p in prompts),
            "result_profile_matches": (
                json.loads(result.payload_json).get("profile_ref")
                == receipt["definition"]["profile_ref"]
                if result
                else None
            ),
        }
        pack_hash = receipt["definition"].get("request_pack_hash")
        pack = None
        if prepared is not None:
            # The borrowed full reader already validated the original raw Pack
            # identity and its definition hash, plus every saved generation.
            checks["complete_execution_preparation"] = True
            checks["request_pack_matches_method"] = True
            checks["request_pack_saved_before_method"] = (
                prepared.method.definition.bundle.recorded_at <= prepared.method.recorded_at
            )
        elif pack_hash is not None:
            pack_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=run_id, role=PACK_ROLE
            ).one_or_none()
            pack = ResearchPackService.read(pack_row) if pack_row else None
            checks["request_pack_matches_method"] = bool(
                pack and pack["definition_hash"] == pack_hash
            )
            checks["request_pack_saved_before_method"] = bool(
                pack_row and _required_utc(pack_row.created_at) <= _required_utc(row.created_at)
            )
        policy_status = "verified" if prepared is not None else policy_prompt_status(
            receipt["definition"], pack["definition"] if pack else None,
            [prompt.prompt for prompt in prompts],
        )
        if policy_status != "not_recorded":
            checks["frozen_policy_in_saved_prompts"] = policy_status == "verified"
        status = "submission_receipt_verified" if all(
            checks[k] is True
            for k in (
                "request_matches_host_link",
                "host_build_matches",
                "saved_before_host_acceptance",
                "saved_prompts_match_request",
            )
        ) and checks["result_profile_matches"] is not False and (
            checks.get("request_pack_matches_method", True)
            and checks.get("request_pack_saved_before_method", True)
            and checks.get("frozen_policy_in_saved_prompts", True)
        ) else "incomplete_or_mismatched"
        if prepared is not None and link is not None and link.accepted_at is None and all(
            checks[key] is True
            for key in (
                "request_matches_host_link", "host_build_matches", "saved_prompts_match_request",
                "complete_execution_preparation", "request_pack_matches_method",
                "request_pack_saved_before_method", "frozen_policy_in_saved_prompts",
            )
        ) and checks["result_profile_matches"] is not False:
            status = "submission_not_recorded"
        return {
            "run_id": run_id,
            "run_status": run.status,
            "status": status,
            "checks": checks,
            "research_policy_status": policy_status,
            "receipt": receipt,
            "prompt_generations": len(prompts),
            "result_saved": result is not None,
            "deployment_connected": False,
            "provider_configuration_attested": False,
            **({"research_policy_execution_status": "not_available",
                "research_policy_activation": (
                    prepared.method.definition.policy_binding.checker_activation
                )}
               if prepared is not None else {}),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error("existing database required")
    db = Database(f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true")
    try:
        value = audit(db, args.run_id)
    except (ValueError, KeyError, TypeError):
        value = {"run_id": args.run_id, "status": "method_receipt_invalid"}
    finally:
        db.engine.dispose()
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    print(json.dumps({k: v for k, v in value.items() if k != "receipt"}, ensure_ascii=False))
    return 0 if value["status"] == "submission_receipt_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
