"""Read-only diagnostics over exact native emission and frozen server scope.

This endpoint neither accepts a candidate from the caller nor grants execution,
publication, calibration or financial qualification. Terminal originals remain
diagnosable; current execution permission remains the Host/Publisher's concern.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..decision.action_research_policy import (
    action_catalog_problems,
    policy_hash,
    research_action_catalog,
)
from ..persistence.db import Database, RunRecord
from ..ports.research_action_synthesis import normalize_action_synthesis
from ..ports.research_synthesis_protocol import DECISION_OUTPUT_SCHEMA_REF
from .provider_input_receipts import (
    SYNTHESIS_TOOL,
    ProviderInputReceiptService,
    read_provider_emission_in_session,
    strict_json,
)
from .research_execution_binding import (
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from .research_recipe_read_scope import recipe_original_read_scope
from .research_run_scope import read_run_spot_admission_in_session

MAX_ISSUES = 96


def frozen_prompt_catalog(prompt: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Read the unique product-delimited catalog from an authenticated prompt.

    Embedded source text cannot substitute another segment: missing/duplicate
    delimiter lines and drift against the authenticated admission scope fail.
    """
    lines = prompt.splitlines()
    begin, end = "BEGIN_FROZEN_ACTION_POLICY_JSON", "END_FROZEN_ACTION_POLICY_JSON"
    if lines.count(begin) != 1 or lines.count(end) != 1:
        raise ValueError("action_preflight_frozen_catalog_section_invalid")
    start, stop = lines.index(begin), lines.index(end)
    if stop != start + 2:
        raise ValueError("action_preflight_frozen_catalog_section_invalid")
    try:
        catalog = strict_json(lines[start + 1])
    except (ValueError, TypeError) as exc:
        raise ValueError("action_preflight_frozen_catalog_invalid") from exc
    if (
        not isinstance(catalog, dict)
        or catalog != expected
        or policy_hash(catalog) != policy_hash(expected)
    ):
        raise ValueError("action_preflight_frozen_catalog_mismatch")
    return catalog


class ActionSynthesisPreflight:
    def __init__(self, database: Database) -> None:
        self.database = database

    def read(
        self,
        run_id: str,
        call_id: str,
        *,
        generation: int,
        execution_session_id: str,
        request_check: Callable[[], None] | None = None,
    ) -> dict[str, Any] | None:
        check = request_check or (lambda: None)
        check()
        if (
            not isinstance(run_id, str)
            or not 1 <= len(run_id) <= 128
            or not isinstance(call_id, str)
            or not 1 <= len(call_id) <= 256
            or type(generation) is not int
            or generation < 1
            or not isinstance(execution_session_id, str)
            or not 1 <= len(execution_session_id) <= 256
        ):
            raise ValueError("action_preflight_identity_invalid")
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with recipe_original_read_scope(session):
                if session.get(RunRecord, run_id) is None:
                    raise KeyError(run_id)
                prepared = read_prepared_execution_in_session(session, run_id, generation)
                check()
                bundle, _ = read_bundle_identity_in_session(session, run_id)
                request = prepared.execution.request
                if (
                    bundle.request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
                    or request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
                    or bundle.request.research_mode != "current_decision"
                    or request.research_mode != "current_decision"
                ):
                    raise ValueError("action_preflight_protocol_unsupported")
                root = prepared.execution.dsh_session_id
                if execution_session_id != root:
                    raise ValueError("action_preflight_root_session_required")
                admission = read_run_spot_admission_in_session(session, run_id)
                if admission is None:
                    raise ValueError("action_preflight_spot_admission_missing")
                catalog = research_action_catalog(admission.instrument)
                if catalog["scope_id"] != admission.scope.scope_id:
                    raise ValueError("action_preflight_scope_original_mismatch")
                catalog = frozen_prompt_catalog(prepared.prompt.prompt, catalog)
                check()
                saved = read_provider_emission_in_session(
                    session,
                    run_id=run_id,
                    generation=generation,
                    root_session_id=root,
                    execution_session_id=execution_session_id,
                    call_id=call_id,
                )
                check()
                if saved is None:
                    return None
                original, reference = saved
                submit = original.submit
                if (
                    submit.run_id != run_id
                    or submit.generation != generation
                    or submit.root_session_id != root
                    or submit.execution_session_id != execution_session_id
                    or submit.request_hash != prepared.method.request_hash
                    or submit.plugin_build_hash
                    != prepared.method.definition.upstream_identity.plugin_build_hash
                    or submit.tool_name != SYNTHESIS_TOOL
                ):
                    raise ValueError("action_preflight_emission_identity_mismatch")
                arguments = strict_json(submit.raw_arguments_json)
                if not isinstance(arguments, dict) or set(arguments) != {"candidate"}:
                    raise ValueError("action_preflight_candidate_arguments_invalid")
                candidate = normalize_action_synthesis(arguments["candidate"])
                if (
                    candidate.request_id != request.request_id
                    or candidate.research_candidate_v1.request_id != request.request_id
                ):
                    raise ValueError("action_preflight_candidate_request_mismatch")
                issues: list[dict[str, Any]] = []
                for index, action in enumerate(candidate.action_intents):
                    for issue in action_catalog_problems(action, catalog):
                        issues.append(
                            {
                                "path": f"action_intents[{index}].{issue['path']}",
                                "code": issue["code"],
                                "actual": issue["actual"],
                                "expected": issue["expected"],
                            }
                        )
                # Use the identical existing emission wire projection. It is
                # pure; this read path never calls the original writer.
                emission = ProviderInputReceiptService._emission_recorded(  # pyright: ignore[reportPrivateUsage]
                    original, reference
                )
                check()
                return {
                    "schema_version": "action-synthesis-preflight.v1",
                    "emission": emission.model_dump(mode="json"),
                    "catalog": catalog,
                    "catalog_hash": policy_hash(catalog),
                    "issues": issues[:MAX_ISSUES],
                    "issues_truncated": len(issues) > MAX_ISSUES,
                }
