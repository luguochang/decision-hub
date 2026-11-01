"""Identity-only provenance reader and deterministic presentation projection."""

from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from ..persistence.db import RunRoleOutputRecord
from ..persistence.research_method_trial_selection import census
from ..ports.market_path import canonical_hash
from ..ports.research_method_view import MethodTrialProvenance
from .research_skill_package import decoded, encoded, sha


def _wire(value):
    return value.model_dump(mode="json", by_alias=True)


def _original(value):
    return {"ref": value.id, "hash": value.payload_hash}


def _optional_ref(body, prefix):
    reference, digest = body[prefix + "_ref"], body[prefix + "_hash"]
    if (reference is None) != (digest is None):
        raise ValueError("method_trial_reference_pair_invalid")
    return None if reference is None else {"ref": reference, "hash": digest}


def _resource_changes(parent, candidate):
    prior = {item.path: item for item in parent.resources}
    current = {item.path: item for item in candidate.resources}
    refs = [
        {
            item["path"]: {"ref": item["body_ref"], "hash": item["body_hash"]}
            for item in package.version["files"]
        }
        for package in (parent, candidate)
    ]
    if set(prior) != set(current):
        raise ValueError("method_trial_resource_set_changed")
    result = []
    for path in sorted(prior):
        before, after = prior[path].body, current[path].body
        if before == after:
            continue
        removed = added = 0
        for kind, a, b, c, d in SequenceMatcher(
            None, before.decode().splitlines(), after.decode().splitlines(), autojunk=False
        ).get_opcodes():
            if kind != "equal":
                removed += b - a
                added += d - c
        result.append(
            dict(
                path=path,
                kind="changed",
                before_hash=sha(before),
                after_hash=sha(after),
                added_lines=added,
                removed_lines=removed,
                parent_resource=refs[0][path],
                candidate_resource=refs[1][path],
            )
        )
    return result


def _project(selection: dict[str, Any], prepared) -> MethodTrialProvenance:
    """Private pure projection. Caller has already read and verified all originals."""
    source, material, behavior = prepared.source, prepared.material, prepared.behavior
    outcome, execution = source.bodies["outcome"], source.bodies["execution_receipt"]
    identity = source.identity
    protocol, receipt = prepared.protocol, prepared.receipt
    behavior_receipt = behavior.receipt
    cases = [_wire(case) for case in behavior.protocol.cases]
    for case in cases:
        case.pop("known_at")
    instrument = identity.scope_id.split(":")[3]
    closed = outcome["status"] == "settled" and outcome["remaining_quantity"] == "0"
    return MethodTrialProvenance.model_validate(
        dict(
            receipt=_original(receipt),
            protocol=_original(protocol),
            parent=_wire(receipt.parent),
            candidate=_wire(receipt.candidate),
            target=_wire(protocol.target),
            pointer_version=selection["trial_pointer_version"],
            run_ordinal=selection["trial_run_ordinal"],
            start_at=protocol.start_at,
            end_at=protocol.end_at,
            max_runs=protocol.max_runs,
            admitted_at=receipt.admitted_at,
            parent_basis=material.learning_protocol.parent_basis,
            source=dict(
                kind="action_outcome",
                ref=_wire(identity.source),
                portfolio=_wire(identity.portfolio),
                scope_id=identity.scope_id,
                instrument=instrument,
                horizon=identity.horizon,
                method=_wire(identity.method),
                evaluation_protocol=_wire(identity.evaluation_protocol),
                mode=outcome["mode"],
                outcome_status=outcome["status"],
                execution_status=execution["status"],
                reason=execution["reason"],
                terminal_kind="closed_episode" if closed else "execution_attempt",
                available_at=material.binding.source_available_at,
                decided_at=execution["decided_at"],
                **{
                    name: outcome[name]
                    for name in (
                        "settled_at",
                        "entry_at",
                        "exit_at",
                        "net_pnl",
                        "cost",
                        "R",
                        "remaining_quantity",
                    )
                },
                execution_receipt={"ref": execution["id"], "hash": execution["payload_hash"]},
                intent=_optional_ref(outcome, "execution"),
                episode=_optional_ref(outcome, "episode"),
                financial_qualified=False,
            ),
            changes=_resource_changes(material.parent, material.candidate),
            package_stage=_wire(receipt.package_stage),
            behavior=dict(
                protocol=_wire(behavior_receipt.protocol),
                receipt=_wire(receipt.behavior),
                status=behavior_receipt.status,
                execution_kind=behavior_receipt.execution_kind,
                parent_passed=behavior_receipt.parent_passed,
                candidate_passed=behavior_receipt.candidate_passed,
                meets_trial_gate=True,
                case_count=len(cases),
                applies_case_count=sum(c["purpose"] == "applies" for c in cases),
                excludes_case_count=sum(c["purpose"] == "excludes" for c in cases),
                historical_pit_case_count=sum(
                    c["material_kind"] == "historical_pit" for c in cases
                ),
                synthetic_control_case_count=sum(
                    c["material_kind"] == "synthetic_control" for c in cases
                ),
                completed_at=behavior_receipt.completed_at,
                cases=cases,
                attempts=[_wire(item) for item in behavior_receipt.attempts],
                outputs=[_wire(item) for item in behavior_receipt.outputs],
            ),
        )
    )


class TrialMethodProjection:
    def __init__(self, database, admission, selections):
        if (
            database is not admission.database
            or database is not selections.packages.registry.database
        ):
            raise ValueError("method_trial_database_mismatch")
        self.database, self.admission, self.selections = database, admission, selections

    def read_in_session(
        self, session: Session, *, run_id: str, selection_id: str
    ) -> MethodTrialProvenance:
        selection = self.selections.read_in_session(session, run_id)
        row = session.get(RunRoleOutputRecord, selection_id, populate_existing=True)
        if (
            row is None
            or row.run_id != run_id
            or row.role != "research_skill_selection"
            or row.schema_version not in {
                "research-skill-selection.v2", "research-skill-selection.v3"
            }
            or row.schema_version != selection["schema_version"]
            or selection["selection_origin"] != "trial_registered"
            or row.output_id != selection["selection_id"]
            or decoded(row.payload_json) != selection
            or encoded(selection).decode() != row.payload_json
            or canonical_hash(selection) != row.payload_hash
        ):
            raise ValueError("method_trial_selection_original_mismatch")
        prepared = self.admission.prepare_receipt(selection["trial_receipt"]["ref"])
        prepared.verify(session)
        receipt, protocol = prepared.receipt, prepared.protocol
        if (
            selection["trial_receipt"] != _original(receipt)
            or selection["trial_protocol"] != _original(protocol)
            or selection["trial_parent"] != _wire(receipt.parent)
            or selection["trial_target"] != _wire(protocol.target)
            or selection["skill_version_id"] != receipt.candidate.ref
            or selection["version_hash"] != canonical_hash(prepared.material.candidate.version)
            or prepared.material.candidate.manifest.payload_hash != receipt.candidate.hash
            or selection["scope_id"] != protocol.scope_id
            or selection["horizon"] != protocol.horizon
        ):
            raise ValueError("method_trial_selection_admission_mismatch")
        runs = census(session, receipt.id, receipt.payload_hash)
        ordinal = selection["trial_run_ordinal"]
        if not 1 <= ordinal <= len(runs) or runs[ordinal - 1] != run_id:
            raise ValueError("method_trial_run_reservation_missing")
        result = _project(selection, prepared)
        prepared.verify_bytes()
        return result
