"""Execute six deterministic and two trajectory rules from originals.

Probability provenance authenticates approved first-freeze production references,
actual Kernel math and the separately preserved model claim. Missing samples stay
unavailable. The other five handlers remain unavailable. This receipt does not
grant execution permission or complete the policy. Legacy prepared bindings are
not upgraded. Writer callers retain their own physical transaction and fences.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult, EvidenceCandidate, FactEnvelope, ResearchCapabilityInvocation,
    ResearchCapabilityQuery, ResearchSessionRequest, ResearchSessionResult,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyCheckReceipt,
    ResearchPolicyMethodBinding,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
    _columns,  # pyright: ignore[reportPrivateUsage]
    _local_original_read,  # pyright: ignore[reportPrivateUsage]
    _ReadSourceGuard,  # pyright: ignore[reportPrivateUsage]
    _read_round_receipt,  # pyright: ignore[reportPrivateUsage]
    read_graph_final_in_session,
    read_graph_round_in_session,
    verify_graph_citation_membership_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
    graph_recipe_manifest_v2,
)
from packages.kernel.decision_hub_kernel.application.research_method import recipe_hash
from packages.kernel.decision_hub_kernel.application.research_observability import (
    read_trace_projection,
)
from packages.kernel.decision_hub_kernel.application.research_policy_receipts import (
    policy_receipt_id,
    policy_receipt_identity_hash,
    read_policy_check_receipt,
    store_policy_check_receipt,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha, read_mapped_observation, read_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application import research_evidence
from . import research_probability_binding
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    _yahoo_daily_freshness,  # pyright: ignore[reportPrivateUsage]
    _source_public_time_unknown_v2,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.decision import (
    citation_membership_v2, gate as kernel_gate, horizon_contract,
    research_policy_registry, research_loop, sufficiency,
)
from packages.kernel.decision_hub_kernel.decision.research_loop import assess_research_loop
from packages.kernel.decision_hub_kernel.decision.gate import evaluate_gate
from packages.kernel.decision_hub_kernel.decision.horizon_contract import validate_horizon_contract
from packages.kernel.decision_hub_kernel.decision.sufficiency import assess_evidence_sufficiency
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord, DshSessionPromptRecord, ResearchCommandRecord,
    ResearchToolCallReservationRecord, ResearchTraceRecord, ResearchUsageSnapshotRecord, RunRecord,
    FactVersionRecord,
    ObservationRecord,
    RunInputRefRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    as_utc,
)

Phase = Literal["finalization", "commit"]
LEGACY_VERSION = "research-policy-checker-registry.v1"
_DETERMINISTIC_RULE_IDS = (
    "frozen-hard-coverage", "causal-chain-structure", "horizon-contract", "kernel-cap",
)
_PURE_MODULES = (kernel_gate, horizon_contract, sufficiency, research_evidence, research_loop,
                 research_probability_binding)
_LOADED_FILES = tuple(Path(module.__file__) for module in (
    citation_membership_v2, research_policy_registry, *_PURE_MODULES,
)) + (Path(__file__),)
_LOADED_HASHES = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in _LOADED_FILES)


def _require_loaded_callables() -> None:
    # Scope is this dispatcher and its direct imported call targets. This is
    # not protection against arbitrary interpreter or operating-system control.
    if (
        any(globals().get(name) is not function or function.__code__ is not code
            for name, function, code in _LOADED_EXECUTION)
        or any(getattr(globals().get(name), "__wrapped__", None) is not function
               or function.__code__ is not code for name, function, code in _LOADED_WRAPPED)
        or any(globals().get(name) is not cls or cls.__init__ is not constructor
               or constructor.__code__ is not code
               for name, cls, constructor, code in _LOADED_POLICY_TYPES)
        or citation_membership_v2.check_citation_membership_v2 is not _LOADED_MEMBERSHIP
        or _LOADED_MEMBERSHIP.__code__ is not _LOADED_MEMBERSHIP_CODE
        or any(getattr(module, name, None) is not function or function.__code__ is not code
               for module, name, function, code in _LOADED_PURE_TARGETS)
    ):
        raise ValueError("research_policy_checker_current_implementation_unavailable")


def citation_execution_identity() -> dict[str, str]:
    """Freeze the actual executable and its transitive original-reader closure."""
    from . import commit as publication_consumer
    from packages.evals import analytical_assessment as evaluation_consumer

    _require_loaded_callables()
    if tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in _LOADED_FILES) != _LOADED_HASHES:
        raise ValueError("research_policy_checker_current_implementation_unavailable")
    return {
        "version": research_policy_registry.CHECKER_REGISTRY_VERSION,
        "code_hash": recipe_hash({
            "checker_version": research_policy_registry.CHECKER_REGISTRY_VERSION,
            "implementation_version": research_policy_registry.CITATION_EXECUTOR_VERSION,
            "supported_rule_ids": sorted(("citation-membership", "pit", *_DETERMINISTIC_RULE_IDS,
                                          *_TRAJECTORY_IDS, "probability-provenance")),
            "deterministic_helper_sources": {
                module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                for module in _PURE_MODULES
            },
            "consumer_sources": {
                "publication": hashlib.sha256(Path(publication_consumer.__file__).read_bytes()).hexdigest(),
                "evaluation": hashlib.sha256(Path(evaluation_consumer.__file__).read_bytes()).hexdigest(),
            },
            "graph_reader_manifest": graph_recipe_manifest_v2().model_dump(mode="json"),
        }),
    }


def _binding_originals(session: Session, run_id: str) -> tuple[Any, Any, Any, ResearchPolicyMethodBinding | None]:
    _require_loaded_callables()
    prepared = read_prepared_execution_in_session(session, run_id)
    bundle, identity = read_bundle_identity_in_session(session, run_id)
    body = prepared.method.definition.policy_binding.model_dump(mode="json")
    binding = ResearchPolicyMethodBinding.model_validate(body)
    # v1 names the unchanged closed registry protocol, not an executor proof.
    # Only the authenticated original compiler's registry-only digest denotes
    # the old prepared implementation. Version alone never authorizes bypass.
    if (binding.checker_version == LEGACY_VERSION
            and binding.checker_code_hash == bundle.compiled.compiled.code_identity.registry_source_sha256):
        return prepared, bundle, identity, None
    execution_identity = citation_execution_identity()
    if (binding.checker_version, binding.checker_code_hash) != (
        execution_identity["version"], execution_identity["code_hash"],
    ):
        raise ValueError("research_policy_checker_current_implementation_unavailable")
    return prepared, bundle, identity, binding


def _binding(session: Session, run_id: str) -> ResearchPolicyMethodBinding | None:
    return _binding_originals(session, run_id)[3]


def _claim_body(result: ResearchSessionResult) -> dict[str, object]:
    # Commit separately authenticates reviews and calibration. Only these two
    # display fields may be normalized; all claim bodies, refs and values stay.
    body = result.model_dump(mode="json", exclude={"reviews"})
    for horizon in body["horizons"]:
        horizon.pop("probability_status", None)
        horizon.pop("confidence_cap_reason", None)
    return body


@dataclass(frozen=True)
class _PitMaterials:
    input_json: str
    input_hash: str
    references: tuple[tuple[str, str, str], ...]
    unavailable_reason: str | None = None


def _pit_materials(body: dict[str, Any], references: tuple[tuple[str, str, str], ...],
                   unavailable_reason: str | None = None) -> _PitMaterials:
    # Only private original capture constructs this immutable value. It is not
    # a caller proof, permission cache, or a replacement for the Graph reader.
    return _PitMaterials(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False),
                         recipe_hash(body), references, unavailable_reason)


def _pit_unavailable(bundle: Any, original: Any, reason: str) -> _PitMaterials:
    return _pit_materials({
        "schema_version": "research-policy-pit-inputs.v1",
        "scope": bundle.task.scope.model_dump(mode="json"),
        "task_request_hash": bundle.task.request_hash,
        "graph_final_hash": recipe_hash(original.receipt.model_dump(mode="json")),
        "unavailable_reason": reason,
    }, (), reason)


def _pit_fingerprinted_row(session: Session, model: Any, identifier: str,
                           fingerprints: dict[tuple[str, str], str]) -> dict[str, object]:
    row = session.get(model, identifier, populate_existing=True)
    if row is None:
        raise ValueError("research_policy_pit_original_missing")
    columns = _columns(row)
    if fingerprints.get((model.__tablename__, identifier)) != observation_sha(columns):
        raise ValueError("research_policy_pit_original_mismatch")
    return columns


def _pit_trigger_originals(session: Session, trigger: SnapshotRecord,
                          evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Decode only authenticated internal Snapshot UTC times against originals.

    SnapshotService writes SQLite datetime.isoformat() without an offset. The
    external DTO parser remains strict; no caller-supplied naive time gets this
    codec. The raw snapshot and its hash are never normalized or rewritten.
    """
    normalized, observations = [], []
    for item in evidence:
        # Observation.content_hash is unique. The complete row is captured in
        # the same private physical snapshot and bound into the PIT input hash.
        observation = session.query(ObservationRecord).filter_by(
            content_hash=item.get("content_hash"),
        ).populate_existing().one_or_none()
        if (observation is None or observation.event_id != trigger.event_id
                or any(item.get(name) != getattr(observation, name)
                       for name in ("text", "source_id", "source_type", "content_hash"))):
            raise ValueError("research_policy_pit_trigger_original_mismatch")
        expected = {name: as_utc(getattr(observation, name))
                    for name in ("published_at", "observed_at", "received_at")}
        expected["cutoff_at"] = as_utc(trigger.cutoff_at)
        if expected["cutoff_at"] != expected["received_at"]:
            raise ValueError("research_policy_pit_trigger_original_mismatch")
        private = dict(item)
        for name, instant in expected.items():
            encoded = item.get(name)
            if instant is None:
                if encoded is not None:
                    raise ValueError("research_policy_pit_trigger_original_mismatch")
                private[name] = None
                continue
            if not isinstance(encoded, str):
                raise ValueError("research_policy_pit_trigger_original_mismatch")
            value = datetime.fromisoformat(encoded)
            # A naive internal spelling must match the original DB UTC wall
            # value exactly, including microseconds. Aware spellings compare
            # exact instants; no tolerance or external timezone is invented.
            if (value.tzinfo is None and value != instant.replace(tzinfo=None)
                    or value.tzinfo is not None and as_utc(value) != instant):
                raise ValueError("research_policy_pit_trigger_original_mismatch")
            private[name] = instant.isoformat()
        normalized.append(private)
        observations.append({"observation_id": observation.observation_id,
                             "payload_hash": observation_sha(_columns(observation))})
    return normalized, observations


def _pit_originals(session: Session, bundle: Any, original: Any, round_receipt: Any,
                   trigger: SnapshotRecord, decision: SnapshotRecord) -> _PitMaterials:
    scope = bundle.task.scope
    if (scope.execution_mode != "live"
            or scope.scope_kind not in {"event", "current_snapshot"}):
        # Replay wall-clock admission/recording cannot prove historical known-at.
        return _pit_unavailable(bundle, original, "pit_historical_provenance_unavailable")
    trigger_body = json.loads(trigger.evidence_json)
    accepted = sorted((e for e in round_receipt.evidence if e.quality == "accepted"),
                      key=lambda e: e.evidence_id)
    if (not isinstance(trigger_body, list) or not trigger_body
            or any(not isinstance(item, dict) for item in trigger_body)
            or json.loads(decision.evidence_json) != [
                *trigger_body, *(e.model_dump(mode="json") for e in accepted),
            ]):
        raise ValueError("research_policy_pit_snapshot_original_mismatch")
    if (trigger.snapshot_id != scope.trigger_snapshot_id
            or trigger.snapshot_hash != scope.trigger_snapshot_hash
            or trigger.event_id != scope.event_id or trigger.run_id not in {None, scope.run_id}
            or trigger.snapshot_type != "trigger"
            or decision.run_id != scope.run_id or decision.event_id != scope.event_id
            or decision.parent_snapshot_id != trigger.snapshot_id
            or decision.snapshot_type != "decision" or decision.generation != 2
            or decision.snapshot_id != original.receipt.decision_snapshot_id
            or decision.snapshot_hash != original.receipt.decision_snapshot_hash):
        raise ValueError("research_policy_pit_snapshot_original_mismatch")
    # First authenticate raw trigger/decision composition and scope unchanged;
    # only then create a separate private PIT view of internal UTC encodings.
    trigger_evidence, trigger_observations = _pit_trigger_originals(session, trigger, trigger_body)
    fingerprints = {(r.table, r.row_id): r.payload_hash for r in round_receipt.material_rows}
    evidence_ids = {e.evidence_id for e in accepted}
    facts = sorted((f for f in round_receipt.facts if f.quality == "accepted"),
                   key=lambda f: f.fact_id)
    if any(f.evidence_id not in evidence_ids for f in facts):
        return _pit_unavailable(bundle, original, "pit_fact_evidence_scope_unavailable")
    versions: dict[str, dict[str, object]] = {}
    lineage = []
    for fact in facts:
        adopted = _pit_fingerprinted_row(session, RunInputRefRecord, fact.fact_id, fingerprints)
        version_id = str(adopted["fact_version_id"])
        if version_id not in versions:
            versions[version_id] = _pit_fingerprinted_row(
                session, FactVersionRecord, version_id, fingerprints,
            )
        version = versions[version_id]
        payload = json.loads(str(version["payload_json"]))
        if (adopted["run_id"] != scope.run_id or adopted["fact_id"] != fact.fact_id
                or version["source_id"] != fact.source_id
                or not isinstance(payload, dict) or payload.get("payload_hash") != fact.payload_hash):
            raise ValueError("research_policy_pit_fact_lineage_mismatch")
        lineage.append({
            "fact": fact.model_dump(mode="json"),
            "input_row_hash": fingerprints[(RunInputRefRecord.__tablename__, fact.fact_id)],
            "fact_version_row_hash": fingerprints[(FactVersionRecord.__tablename__, version_id)],
            "fact_version_id": version_id, "use_mode": adopted["use_mode"],
            "as_of": adopted["as_of"], "version_observed_at": version["observed_at"],
            "known_at": version["known_at"],
        })
    trigger_columns, decision_columns = _columns(trigger), _columns(decision)
    return _pit_materials({
        "schema_version": "research-policy-pit-inputs.v1",
        "scope": scope.model_dump(mode="json"),
        "task_request_hash": bundle.task.request_hash,
        "task_inputs": [item.model_dump(mode="json") for item in bundle.request.input_evidence],
        "trigger_row_hash": observation_sha(trigger_columns),
        "trigger_cutoff_at": trigger_columns["cutoff_at"], "trigger_evidence": trigger_evidence,
        "trigger_observation_rows": trigger_observations,
        "decision_row_hash": observation_sha(decision_columns),
        "decision_cutoff_at": decision_columns["cutoff_at"],
        "graph_decision_cutoff_at": original.receipt.decision_cutoff_at.isoformat(),
        "round_ref": original.receipt.last_admission_ref.model_dump(mode="json"),
        "accepted_evidence": [e.model_dump(mode="json") for e in accepted],
        "fact_lineage": lineage,
    }, (
        ("task", scope.run_id, bundle.task.request_hash),
        ("snapshot", trigger.snapshot_id, trigger.snapshot_hash),
        ("snapshot", decision.snapshot_id, decision.snapshot_hash),
        ("trace", original.receipt.last_admission_ref.row.output_id,
         original.receipt.last_admission_ref.row.payload_hash),
    ))


def _pit_instant(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("research_policy_pit_time_original_invalid")
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("research_policy_pit_time_original_invalid")
    return as_utc(instant)


def _pit_order(item: dict[str, Any], cutoff: datetime, *, published_before_observed: bool) -> bool | None:
    observed, received = _pit_instant(item.get("observed_at")), _pit_instant(item.get("received_at"))
    if observed is None or received is None:
        return None
    published = _pit_instant(item.get("published_at"))
    boundary = observed if published_before_observed else received
    return observed <= received <= cutoff and (published is None or published <= boundary)


def _pit_verdict(materials: _PitMaterials) -> tuple[str, str]:
    if materials.unavailable_reason is not None:
        return "not_available", materials.unavailable_reason
    body = json.loads(materials.input_json)
    scope = body["scope"]
    trigger = _pit_instant(body["trigger_cutoff_at"])
    decision = _pit_instant(body["decision_cutoff_at"])
    declared = _pit_instant(body["graph_decision_cutoff_at"])
    pit, deadline = _pit_instant(scope["pit_cutoff_at"]), _pit_instant(scope["deadline_at"])
    if any(value is None for value in (trigger, decision, declared, pit, deadline)):
        return "not_available", "pit_original_time_unavailable"
    if decision != declared or not trigger <= decision <= pit <= deadline:
        return "fail", "pit_snapshot_cutoff_rejected"
    outcomes = []
    for item in body["trigger_evidence"]:
        outcomes.append(_pit_instant(item.get("cutoff_at")) == trigger)
        outcomes.append(_pit_order(item, trigger, published_before_observed=False))
    for item in body["task_inputs"]:
        outcomes.append(_pit_order(item, decision, published_before_observed=False))
    for item in body["accepted_evidence"]:
        outcomes.append(_pit_order(item, decision, published_before_observed=True))
    unavailable = False
    for item in body["fact_lineage"]:
        outcomes.append(_pit_order(item["fact"], decision, published_before_observed=True))
        observed, known, ceiling = (_pit_instant(item[name]) for name in
                                     ("version_observed_at", "known_at", "as_of"))
        if item["use_mode"] not in {"fresh", "reused"} or any(
            value is None for value in (observed, known, ceiling)
        ):
            unavailable = True
        else:
            # Kernel acceptance may follow the fresh source completion cutoff.
            # The version must still be known before the actual decision.
            outcomes.append(observed <= known <= decision)
            if item["use_mode"] == "reused":
                # An old version must have been available by its lookup ceiling.
                outcomes.append(known <= ceiling)
            else:
                # Fresh as_of records the source/PIT admission cutoff, not the
                # later Kernel accepted_at included in FactVersion.known_at.
                outcomes.append(_pit_order(item["fact"], ceiling,
                                           published_before_observed=True))
    if any(value is False for value in outcomes):
        return "fail", "pit_material_time_rejected"
    if unavailable or any(value is None for value in outcomes):
        return "not_available", "pit_original_known_at_unavailable"
    return "pass", "pit_frozen_lineage_verified"


@dataclass(frozen=True)
class _DeterministicOutcome:
    status: Literal["pass", "fail", "not_available"]
    reason: str
    input_hash: str
    references: tuple[tuple[str, str, str], ...]


def _deterministic_outcome(rule_id: str, status: Literal["pass", "fail", "not_available"],
                           reason: str, body: dict[str, Any],
                           references: tuple[tuple[str, str, str], ...] = ()) -> _DeterministicOutcome:
    if len(references) > 256:
        raise ValueError("research_policy_evidence_reference_limit")
    return _DeterministicOutcome(status, reason, recipe_hash({
        "schema_version": "research-policy-deterministic-inputs.v1",
        "rule_id": rule_id, "originals": body,
    }), references)


def _deterministic_unavailable(bundle: Any, original: Any) -> dict[str, _DeterministicOutcome]:
    # Do not combine separately read originals into an executable rule pass.
    body = {"task_request_hash": bundle.task.request_hash,
            "graph_final_hash": recipe_hash(original.receipt.model_dump(mode="json")),
            "scope": bundle.task.scope.model_dump(mode="json")}
    return {name: _deterministic_outcome(name, "not_available",
            "deterministic_physical_snapshot_unavailable", body)
            for name in _DETERMINISTIC_RULE_IDS}


def _coverage_outcome(bundle: Any, round_receipt: Any, rule: Any,
                      reference: datetime) -> _DeterministicOutcome:
    requirements = bundle.request.evidence_requirements
    evidence, facts = round_receipt.evidence, round_receipt.facts
    body = {"task_request_hash": bundle.task.request_hash,
            "requirements": [item.model_dump(mode="json") for item in requirements],
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "facts": [item.model_dump(mode="json") for item in facts],
            "reference_at": reference.isoformat(), "params": rule.params.model_dump(mode="json")}
    refs = tuple(("evidence", item.evidence_id, recipe_hash(item.model_dump(mode="json")))
                 for item in evidence if item.quality == "accepted")
    params = rule.params
    if (params.requirements_basis != "frozen_task" or params.freshness_basis != "frozen_requirement"
            or params.reject_open_hard_conflicts is not True):
        return _deterministic_outcome(rule.rule_id, "not_available", "frozen_coverage_policy_unavailable", body, refs)
    if not any(item.importance == "hard" for item in requirements):
        return _deterministic_outcome(rule.rule_id, "not_available", "frozen_hard_requirements_unavailable", body, refs)
    by_requirement = {item.requirement_id: item for item in requirements}
    recalculated, invalid = [], []
    for item in evidence:
        requirement = by_requirement.get(item.requirement_id)
        if requirement is None:
            invalid.append("coverage_requirement_unknown")
            recalculated.append(item)
            continue
        # Recompute freshness at the original decision; never promote old
        # candidate/rejected/stale evidence or use receive time to refresh it.
        freshness = item.freshness_status
        if item.quality == "accepted":
            selected = [fact for fact in facts if fact.evidence_id == item.evidence_id]
            offsets = {(fact.requirement_id, fact.event_offset) for fact in selected}
            baseline = (offsets == {(item.requirement_id, "baseline")}
                        and "baseline" in (requirement.required_event_offsets or []))
            if item.source_id == "yahoo-finance-public":
                try:
                    freshness = _yahoo_daily_freshness(item, selected, reference,
                                                     requirement.freshness_seconds)
                except ValueError:
                    freshness = "unknown"
                    invalid.append("coverage_daily_original_invalid")
            else:
                age = (reference - (item.published_at or item.observed_at)).total_seconds()
                freshness = ("unknown" if _source_public_time_unknown_v2(item)
                             else "fresh" if baseline or 0 <= age <= requirement.freshness_seconds
                             else "stale")
        recalculated.append(item.model_copy(update={"freshness_status": freshness}))
    coverage = assess_evidence_sufficiency(requirements, recalculated, cutoff_at=reference,
                                          assessed_at=reference, facts=facts)
    body["recomputed_coverage"] = coverage.model_dump(mode="json")
    body["invalid_originals"] = sorted(set(invalid))
    passed = (not invalid and coverage.status == "sufficient"
              and not any(item.importance == "hard" for item in coverage.gaps)
              and not any(item.severity == "hard" and item.resolution_status == "open"
                          for item in coverage.conflicts))
    return _deterministic_outcome(rule.rule_id, "pass" if passed else "fail",
                                 "frozen_hard_coverage_verified" if passed else "frozen_hard_coverage_rejected",
                                 body, refs)


def _structure_outcome(bundle: Any, round_receipt: Any, result: ResearchSessionResult,
                       rule: Any) -> _DeterministicOutcome:
    accepted = [item for item in round_receipt.evidence if item.quality == "accepted"]
    inputs = bundle.request.input_evidence
    allowed = {item.evidence_id for item in (*accepted, *inputs)}
    case = result.causal_case
    body = {"causal_case": case.model_dump(mode="json") if case is not None else None,
            "accepted_evidence": [item.model_dump(mode="json") for item in accepted],
            "original_inputs": [item.model_dump(mode="json") for item in inputs],
            "task_request_hash": bundle.task.request_hash, "params": rule.params.model_dump(mode="json")}
    refs = tuple(("evidence", item.evidence_id, recipe_hash(item.model_dump(mode="json")))
                 for item in accepted)
    if rule.params.input_basis != "causal_links_and_accepted_refs":
        return _deterministic_outcome(rule.rule_id, "not_available", "causal_structure_policy_unavailable", body, refs)
    passed = bool(case is not None and case.case_id.strip() and case.thesis.strip()
                  and case.main_chain and case.opposite_chain and case.evidence_refs
                  and all(ref.strip() and ref in allowed for ref in case.evidence_refs))
    if case is not None:
        links = [*case.main_chain, *case.opposite_chain]
        ids = [item.link_id.strip() for item in links]
        passed = bool(passed and len(ids) == len(set(ids)) and all(ids)
                      and all(question.strip() for question in case.unresolved_questions))
        for link in links:
            passed = bool(passed and link.claim_type in {"fact", "inference", "scenario"}
                          and link.statement.strip() and link.confirmation.strip() and link.invalidation.strip()
                          and link.affected_horizons and set(link.affected_horizons) <= {"30m", "24h", "72h"}
                          and link.evidence_refs and all(ref.strip() and ref in allowed for ref in link.evidence_refs))
    # Structure and reference membership only; no causal or counter quality,
    # claim precision, factual truth, authority or independent review is attested.
    return _deterministic_outcome(rule.rule_id, "pass" if passed else "fail",
                                 "causal_structure_verified" if passed else "causal_structure_rejected", body, refs)


def _horizon_outcome(bundle: Any, result: ResearchSessionResult, rule: Any,
                     reference: datetime, selected: ResearchSessionResult | None) -> _DeterministicOutcome:
    # Display-only calibration labels may differ at Commit; original windows,
    # claim text, references, actions and probability values may not.
    horizons = _claim_body(result)["horizons"]
    body = {"task_request_hash": bundle.task.request_hash, "horizons": horizons,
            "selected_mapped_horizons": _claim_body(selected)["horizons"] if selected is not None else None,
            "reference_at": reference.isoformat(), "params": rule.params.model_dump(mode="json")}
    if (rule.params.reference_basis != "decision_snapshot_or_case_cutoff"
            or rule.params.signature_version != horizon_contract.HORIZON_CONTRACT_VERSION):
        return _deterministic_outcome(rule.rule_id, "not_available", "horizon_contract_policy_unavailable", body)
    check = validate_horizon_contract(result.horizons, reference_at=reference)
    body["contract_version"] = check.contract_version
    body["issue_codes"] = list(check.issue_codes)
    original = validate_horizon_contract(selected.horizons, reference_at=reference) if selected is not None else None
    body["selected_mapped_issue_codes"] = list(original.issue_codes) if original is not None else None
    passed = check.valid and original is not None and original.valid
    return _deterministic_outcome(rule.rule_id, "pass" if passed else "fail",
                                 "horizon_contract_verified" if passed else "horizon_contract_rejected", body)


def _cap_outcome(bundle: Any, result: ResearchSessionResult, rule: Any,
                 selected: ResearchSessionResult | None) -> _DeterministicOutcome:
    maximum = rule.params.maximum_subjective_probability
    body = {"task_request_hash": bundle.task.request_hash, "params": rule.params.model_dump(mode="json"),
            "horizons": [{"horizon": item.horizon, "action": item.action,
                          "subjective_probability": item.subjective_probability} for item in result.horizons]}
    if not 0 < maximum <= 0.65:
        return _deterministic_outcome(rule.rule_id, "not_available", "kernel_cap_policy_unavailable", body)
    body["selected_mapped_horizons"] = [
        {"horizon": item.horizon, "action": item.action, "subjective_probability": item.subjective_probability}
        for item in selected.horizons] if selected is not None else None
    if selected is None or not selected.horizons:
        return _deterministic_outcome(rule.rule_id, "not_available", "kernel_cap_original_horizons_unavailable", body)
    if not result.horizons:
        return _deterministic_outcome(rule.rule_id, "not_available", "kernel_cap_result_horizons_unavailable", body)
    decisions = []
    for basis, horizons in (("selected_mapped_synthesis", selected.horizons), ("graph_or_commit_result", result.horizons)):
        for item in horizons:
            # The original mapped AI probability and normalized Result both
            # remain independent inputs. Graph can lower bounded probabilities
            # to 0.5; that must not erase an original cap failure. Forecast is
            # not an input, and neither saved original is changed by this rule.
            gate = evaluate_gate({"probability": item.subjective_probability})
            cap = [decision for decision in gate.decisions if decision["rule_id"] == "probability_cap"]
            if len(cap) != 1:
                return _deterministic_outcome(rule.rule_id, "not_available", "kernel_cap_implementation_unavailable", body)
            decisions.append({"basis": basis, "horizon": item.horizon, "kernel_status": cap[0]["status"],
                              "frozen_maximum_passed": item.subjective_probability <= maximum})
    body["per_window_cap"] = decisions
    passed = all(item["kernel_status"] == "pass" and item["frozen_maximum_passed"] for item in decisions)
    return _deterministic_outcome(rule.rule_id, "pass" if passed else "fail",
                                 "kernel_cap_verified" if passed else "kernel_cap_rejected", body)


def _deterministic_outcomes(bundle: Any, round_receipt: Any, result: ResearchSessionResult,
                            rules: Any, reference: datetime) -> dict[str, _DeterministicOutcome]:
    # Inputs have just been authenticated in this same no-write physical
    # snapshot. Each phase independently recomputes each rule's own predicate.
    definitions = {item.definition.rule_id: item.definition for item in rules}
    selected = None
    if round_receipt.selected_synthesis_observation_ref is not None:
        raw = round_receipt.projected_state.get("latest_result")
        if raw is None:
            raise ValueError("research_policy_selected_synthesis_original_missing")
        # Full Graph authentication has reconstructed this state and its exact
        # selected original mapping. This is not an unparsed Provider reply or
        # caller assertion, and reading it adds no mixed transaction or SQL.
        selected = ResearchSessionResult.model_validate_json(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    outcomes = {
        "frozen-hard-coverage": _coverage_outcome(bundle, round_receipt, definitions["frozen-hard-coverage"], reference),
        "causal-chain-structure": _structure_outcome(bundle, round_receipt, result, definitions["causal-chain-structure"]),
        "horizon-contract": _horizon_outcome(bundle, result, definitions["horizon-contract"], reference, selected),
        "kernel-cap": _cap_outcome(bundle, result, definitions["kernel-cap"], selected),
    }
    selected_ref = (round_receipt.selected_synthesis_observation_ref.model_dump(mode="json")
                    if round_receipt.selected_synthesis_observation_ref is not None else None)
    # Bind the selected original locator and already authenticated state hash
    # into every independent predicate, including cap/horizon normalization.
    return {name: _DeterministicOutcome(item.status, item.reason, recipe_hash({
                "rule_input_hash": item.input_hash, "state_hash": round_receipt.state_hash,
                "selected_synthesis_observation_ref": selected_ref,
            }), item.references) for name, item in outcomes.items()}


# Private immutable trajectory originals. They carry no permission or native
# qualification. All construction takes place within _policy_materials' actual
# physical snapshot; storage takes place only after its no-write scope closes.
_TRAJECTORY_IDS = ("gap-to-tool-continuation", "bounded-stop-consistency")
_TRAJECTORY_SCHEMA = "research-policy-trajectory-inputs.v1"
_TRAJECTORY_MAX_BYTES = 8 * 1024 * 1024


class _TrajectoryClosed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _TrajectoryRow(_TrajectoryClosed):
    table: Literal["run_role_outputs", "research_tool_call_reservations", "research_trace_events",
                   "research_usage_snapshots", "research_commands", "dsh_session_links", "runs",
                   "dsh_session_prompts"]
    locator: str
    payload_json: str
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class _TrajectoryCall(_TrajectoryClosed):
    request_id: str
    capability_id: str
    requirement_id: str
    generation: int = Field(ge=1)
    research_session_id: str
    invocation_json: str
    query_json: str | None
    timing_output_id: str | None
    adapter_output_id: str | None
    native_call_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_result_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: Literal["running", "completed", "failed", "cancelled", "denied", "timed_out", "unknown"]


class _TrajectoryRound(_TrajectoryClosed):
    logical_round: int = Field(ge=1, le=10)
    generation: int = Field(ge=1)
    research_session_id: str
    round_ref_json: str
    request_json: str
    result_json: str | None
    transport_ref_json: str
    evidence_json: str
    facts_json: str
    calls: tuple[_TrajectoryCall, ...] = Field(max_length=100)
    incomplete_reasons: tuple[str, ...] = Field(max_length=32)


class _TrajectorySnapshot(_TrajectoryClosed):
    schema_version: Literal["research-policy-trajectory-inputs.v1"] = _TRAJECTORY_SCHEMA
    run_id: str
    event_id: str
    task_request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    method_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    checker_code_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_ref_json: str
    phase: Phase
    assessed_at: AwareDatetime
    rounds: tuple[_TrajectoryRound, ...] = Field(max_length=10)
    reservations: tuple[_TrajectoryRow, ...] = Field(max_length=100)
    immutable_rows: tuple[_TrajectoryRow, ...] = Field(max_length=241)
    traces: tuple[_TrajectoryRow, ...] = Field(max_length=1200)
    usage_rows: tuple[_TrajectoryRow, ...]
    link: _TrajectoryRow
    run: _TrajectoryRow
    command_rows: tuple[_TrajectoryRow, ...] = Field(max_length=32)
    incomplete_reasons: tuple[str, ...] = Field(max_length=32)
    # A self-hashed usage snapshot and an accepted cancellation command are
    # not complete pricing provenance or authenticated resource-drain proof.
    verified_total_tokens: None = None
    verified_estimated_cost_usd: None = None
    verified_cancel_drain: None = None


@dataclass(frozen=True)
class _TrajectoryMaterials:
    current_json: str
    saved_json: tuple[tuple[str, str], ...]
    existing_policy_phases: tuple[str, ...]


def _trajectory_json(body: object) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _trajectory_row(row: Any, locator: str) -> _TrajectoryRow:
    # _columns applies the established internal SQLite UTC codec. External
    # native times are validated by the contracts and are never restamped.
    body = _trajectory_json(_columns(row))
    return _TrajectoryRow(table=row.__tablename__, locator=locator,
                          payload_json=body, payload_hash=hashlib.sha256(body.encode()).hexdigest())


def _trajectory_role(run_id: str, graph_ref_json: str, method_hash: str, phase: Phase) -> str:
    return "policy_trajectory:" + recipe_hash({"run_id": run_id, "graph_ref": graph_ref_json,
                                              "method_hash": method_hash, "phase": phase})[:32]


def _trajectory_encoded(value: _TrajectorySnapshot) -> str:
    value = _TrajectorySnapshot.model_validate_json(value.model_dump_json())
    for row in (*value.reservations, *value.immutable_rows, *value.traces, *value.usage_rows,
                *value.command_rows, value.link, value.run):
        if (row.payload_json != _trajectory_json(json.loads(row.payload_json))
                or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()):
            raise ValueError("research_policy_trajectory_row_fingerprint_invalid")
    payload = _trajectory_json(value.model_dump(mode="json"))
    if len(payload.encode("utf-8")) > _TRAJECTORY_MAX_BYTES:
        raise ValueError("research_policy_trajectory_bytes_exceeded")
    return payload


def _trajectory_read_saved(session: Session, *, run_id: str, graph_ref_json: str,
                           method_hash: str, phase: Phase) -> str | None:
    role = _trajectory_role(run_id, graph_ref_json, method_hash, phase)
    row = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=role
        ).populate_existing().one_or_none()
    if row is None:
        return None
    if len(row.payload_json.encode("utf-8")) > _TRAJECTORY_MAX_BYTES:
        raise ValueError("research_policy_trajectory_bytes_exceeded")
    value = _TrajectorySnapshot.model_validate_json(row.payload_json)
    digest = hashlib.sha256(row.payload_json.encode()).hexdigest()
    if (row.schema_version != _TRAJECTORY_SCHEMA or row.payload_hash != digest
            or row.output_id != "policy_trajectory_" + digest
            or _trajectory_encoded(value) != row.payload_json
            or (value.run_id, value.graph_ref_json, value.method_hash, value.phase)
            != (run_id, graph_ref_json, method_hash, phase)
            or as_utc(row.created_at) != as_utc(value.assessed_at)):
        raise ValueError("research_policy_trajectory_saved_invalid")
    return row.payload_json


def _trajectory_with_usage_rows(
    session: Session, value: _TrajectorySnapshot,
) -> _TrajectorySnapshot:
    # These are internal append-only audit observations, not research actions.
    # Charge their complete canonical encoding against the existing whole
    # snapshot budget. Fetch one row at a time; the iterator is not a quota.
    size = len(_trajectory_encoded(value).encode("utf-8"))
    usage: list[_TrajectoryRow] = []
    rows = session.query(ResearchUsageSnapshotRecord).filter_by(run_id=value.run_id
        ).populate_existing().order_by(ResearchUsageSnapshotRecord.recorded_at,
            ResearchUsageSnapshotRecord.snapshot_id)
    for original in rows.yield_per(1):
        if original.payload_sha256 != hashlib.sha256(original.payload_json.encode()).hexdigest():
            raise ValueError("research_policy_trajectory_usage_original_invalid")
        row = _trajectory_row(original, original.snapshot_id)
        size += len(_trajectory_json(row.model_dump(mode="json")).encode("utf-8"))
        size += int(bool(usage))  # JSON array separator; empty [] is already charged.
        if size > _TRAJECTORY_MAX_BYTES:
            raise ValueError("research_policy_trajectory_bytes_exceeded")
        usage.append(row)
    return value.model_copy(update={"usage_rows": tuple(usage)})


def _trajectory_store(session: Session, value: _TrajectorySnapshot) -> None:
    # Called only by the existing fresh writer, after the read boundary exits.
    native = session.connection().connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("research_policy_trajectory_writer_required")
    payload = _trajectory_encoded(value)
    prior = _trajectory_read_saved(session, run_id=value.run_id, graph_ref_json=value.graph_ref_json,
                                   method_hash=value.method_hash, phase=value.phase)
    if prior is not None:
        if prior != payload:
            raise ValueError("research_policy_trajectory_original_conflict")
        return
    role = _trajectory_role(value.run_id, value.graph_ref_json, value.method_hash, value.phase)
    if session.query(RunRoleOutputRecord).filter(RunRoleOutputRecord.run_id == value.run_id,
            RunRoleOutputRecord.role.like("policy_check:" + value.phase + ":%")
            ).first() is not None:
        raise ValueError("research_policy_trajectory_missing_for_saved_phase")
    digest = hashlib.sha256(payload.encode()).hexdigest()
    if session.get(RunRoleOutputRecord, "policy_trajectory_" + digest, populate_existing=True) is not None:
        raise ValueError("research_policy_trajectory_output_collision")
    session.add(RunRoleOutputRecord(output_id="policy_trajectory_" + digest, run_id=value.run_id,
        role=role, schema_version=_TRAJECTORY_SCHEMA, payload_json=payload,
        payload_hash=digest, created_at=value.assessed_at))
    session.flush()


def _trajectory_native_calls(raw: Any) -> tuple[list[tuple[str, dict[str, Any], str, str | None]], list[str]]:
    if (raw.phase != "result" or not isinstance(raw.reply, DshSessionResult)
            or raw.events_json_hash != hashlib.sha256(raw.reply.events_json.encode()).hexdigest()):
        return [], ["trajectory_native_result_unavailable"]
    events = json.loads(raw.reply.events_json)
    if not isinstance(events, list) or len(events) > 50000:
        return [], ["trajectory_native_census_unavailable"]
    result, reasons, seen, terminals = [], [], set(), {}
    for notification in events:
        payload = notification.get("payload") if isinstance(notification, dict) else None
        event = payload.get("event") if isinstance(payload, dict) else None
        data = event.get("data") if isinstance(event, dict) else None
        if (isinstance(notification, dict) and notification.get("method") == "session.event" and isinstance(payload, dict)
                and payload.get("sessionId") == raw.context.dsh_session_id
                and isinstance(event, dict) and event.get("type") == "tool/result" and isinstance(data, dict)
                and isinstance(data.get("callId"), str)):
            terminals.setdefault(data["callId"], []).append((event.get("seq"), recipe_hash(event)))
    for notification in events:
        if not isinstance(notification, dict) or notification.get("method") != "session.event":
            continue
        payload = notification.get("payload")
        if not isinstance(payload, dict) or payload.get("sessionId") != raw.context.dsh_session_id:
            continue
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") != "tool/call":
            continue
        data = event.get("data")
        if not isinstance(data, dict) or data.get("name") != "decision_hub_research":
            continue
        call_id, args = data.get("callId"), data.get("arguments")
        if isinstance(args, str):
            if len(args.encode("utf-8")) > 131072:
                reasons.append("trajectory_native_arguments_unavailable")
                continue
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if not isinstance(call_id, str) or not call_id or not isinstance(args, dict):
            reasons.append("trajectory_native_arguments_unavailable")
            continue
        if call_id in seen:
            reasons.append("trajectory_native_call_ambiguous")
            continue
        seen.add(call_id)
        terminal = terminals.get(call_id, [])
        completed = (len(terminal) == 1 and isinstance(event.get("seq"), int)
                     and isinstance(terminal[0][0], int) and terminal[0][0] > event["seq"]
                     and terminal[0][0] <= raw.reply.last_seq)
        if not completed:
            reasons.append("trajectory_native_terminal_unavailable")
        result.append((call_id, args, recipe_hash(event), terminal[0][1] if completed else None))
    # No child-call absence, whole-host completeness, or drain is inferred from
    # this correlated root archive. Reservation-counter equality is checked
    # separately and is required before the route ledger may be complete.
    return result, reasons


def _trajectory_anchor(session: Session, bundle: Any, bundle_identity: Any,
                       prepared: Any) -> tuple[dict[str, Any], list[Any]]:
    run_id, generation = bundle.task.run_id, prepared.execution.generation
    rows, raw = [], []
    for role in ("research_execution_method", f"dsh_execution_request:{generation}",
                 f"dsh_prompt_integrity:{generation}"):
        row = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=role
            ).populate_existing().one()
        rows.append(row)
        raw.append({"output_id": row.output_id, "run_id": row.run_id, "role": row.role,
                    "row_schema_version": row.schema_version, "payload_hash": row.payload_hash,
                    "recorded_at": as_utc(row.created_at).isoformat()})
    prompt = session.get(DshSessionPromptRecord, (run_id, generation), populate_existing=True)
    if prompt is None:
        raise ValueError("research_policy_trajectory_prompt_missing")
    rows.append(prompt)
    return {"bundle": bundle_identity.model_dump(mode="json"), "raw_roles": raw,
        "task_request_hash": bundle.task.request_hash, "method_hash": prepared.method.method_hash,
        "actual_full_hash": prepared.execution.request_hash,
        "actual_reduced_hash": prepared.execution.execution_request_hash,
        "whole_prompt_hash": prepared.integrity.prompt_hash,
        "prompt": {"run_id": prompt.run_id, "generation": prompt.generation,
            "dsh_session_id": prompt.dsh_session_id, "request_id": prompt.request_id,
            "request_hash": prompt.request_hash, "recorded_at": as_utc(prompt.created_at).isoformat()},
        "link": {"run_id": prepared.link.run_id, "dsh_session_id": prepared.link.dsh_session_id,
            "request_hash": prepared.link.request_hash, "generation": generation,
            "max_tool_calls": prepared.link.max_tool_calls,
            "deadline_at": prepared.link.deadline_at.isoformat() if prepared.link.deadline_at else None,
            "model_step_timeout_ms": prepared.link.model_step_timeout_ms}}, rows


def _trajectory_call(session: Session, reservation: Any, call_id: str, args: dict[str, Any],
                     call_hash: str, result_hash: str | None, prepared: Any,
                     anchor: dict[str, Any]) -> tuple[_TrajectoryCall | None, list[Any], str | None]:
    value, rows, problem = read_capability_admission_in_session(
        session, reservation, call_id, args, prepared, anchor)
    return (_TrajectoryCall(**value, native_call_hash=call_hash, native_result_hash=result_hash)
            if value is not None else None), rows, problem


def read_capability_admission_in_session(
    session: Session, reservation: Any, call_id: str, args: dict[str, Any],
    prepared: Any, anchor: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[Any], str | None]:
    """Authenticate one Gateway admission independently of its native/server origin."""
    if (reservation.request_id != call_id or reservation.generation != prepared.execution.generation
            or reservation.research_session_id != prepared.link.dsh_session_id):
        return None, [], "trajectory_call_generation_unavailable"
    actual = prepared.execution.request
    managed = actual.execution_mode == "live"
    if "research_session_id" in args:
        raise ValueError("research_policy_trajectory_native_identity_forbidden")
    body = {key: value for key, value in args.items() if key not in {
        "request_id", "mode", "round", "observed_at", "requested_observed_at", "cutoff_at", "deadline_at"
    }} if managed else {key: value for key, value in args.items() if key != "request_id"}
    body.update(request_id=call_id, research_session_id=reservation.research_session_id)
    rows, timing_id, adapter_id, query_json, timing_identity = [], None, None, None, None
    if managed:
        invocation = ResearchCapabilityInvocation.model_validate({**body,
            "schema_version": "research-capability-invocation.v1"})
        intent_json = _trajectory_json(invocation.model_dump(mode="json"))
        role = "research_tool_time:" + hashlib.sha256(call_id.encode()).hexdigest()[:40]
        timing = session.query(RunRoleOutputRecord).filter_by(run_id=reservation.run_id, role=role
            ).populate_existing().one_or_none()
        if timing is None:
            return None, [], "trajectory_timing_original_unavailable"
        value = json.loads(timing.payload_json)
        issued = _pit_instant(value.get("issued_at")) if isinstance(value, dict) else None
        deadline = _pit_instant(value.get("deadline_at")) if isinstance(value, dict) else None
        if (not isinstance(value, dict) or set(value) != {"schema_version", "run_id", "research_session_id",
                "request_id", "intent_hash", "generation", "deadline_at", "issued_at"}
                or timing.schema_version != "research-tool-time.v1"
                or timing.payload_hash != hashlib.sha256(timing.payload_json.encode()).hexdigest()
                or timing.payload_json != _trajectory_json(value)
                or timing.output_id != "timing_" + hashlib.sha256((reservation.run_id + ":" + role).encode()).hexdigest()[:32]
                or (value["run_id"], value["request_id"], value["research_session_id"], value["generation"])
                != (reservation.run_id, call_id, reservation.research_session_id, reservation.generation)
                or value["intent_hash"] != hashlib.sha256(intent_json.encode()).hexdigest()
                or issued is None or deadline is None or issued >= deadline
                or as_utc(timing.created_at) != issued or deadline != prepared.link.deadline_at):
            raise ValueError("research_policy_trajectory_timing_invalid")
        timing_id, rows = timing.output_id, [timing]
        timing_identity = {"output_id": timing.output_id, "run_id": timing.run_id, "role": timing.role,
            "schema_version": timing.schema_version, "payload_hash": timing.payload_hash,
            "created_at": as_utc(timing.created_at).isoformat()}
    else:
        # Explicit replay query times are external aware values. Never decode
        # them with SQLite's naive-UTC codec or choose today's source window.
        invocation = ResearchCapabilityQuery.model_validate({**body,
            "schema_version": "research-capability-query.v1"})
        if (invocation.mode != actual.execution_mode
                or invocation.cutoff_at > actual.pit_cutoff_at):
            raise ValueError("research_policy_trajectory_query_scope_invalid")
        intent_json = _trajectory_json(invocation.model_dump(mode="json"))
        query_json = intent_json
    expected_query = (ResearchCapabilityQuery.model_validate({
        **invocation.model_dump(mode="json", exclude={"schema_version"}),
        "schema_version": "research-capability-query.v1", "round": reservation.generation,
        "mode": "live", "observed_at": issued.isoformat(),
        "cutoff_at": prepared.link.deadline_at.isoformat(),
        "deadline_at": prepared.link.deadline_at.isoformat()}) if managed else
        invocation.model_copy(update={"round": reservation.generation}))
    if expected_query.requested_event_offsets:
        watch = actual.event_watch
        offsets = [offset for offset in expected_query.requested_event_offsets if offset != "baseline"]
        samples = {item.offset: item for item in (actual.event_window_samples or [])}
        selected = [samples[offset] for offset in offsets if offset in samples]
        if (watch is None or expected_query.event_id != actual.event_id or not selected
                or any(offset not in watch.window_offsets for offset in offsets)):
            raise ValueError("research_policy_trajectory_frozen_event_window_invalid")
        update = {"event_at": watch.scheduled_at,
                  "window_start_at": min(item.target_at for item in selected),
                  "window_end_at": max(item.target_at for item in selected)}
        if any(getattr(expected_query, key) is not None and getattr(expected_query, key) != instant
               for key, instant in update.items()):
            raise ValueError("research_policy_trajectory_native_event_window_changed")
        expected_query = expected_query.model_copy(update=update)
    requirements = {item.requirement_id: item for item in actual.evidence_requirements}
    requirement = requirements.get(invocation.requirement_id)
    if (requirement is None or invocation.capability_id != reservation.capability_id
            or invocation.capability_id not in actual.allowed_capabilities
            or invocation.capability_id not in {*requirement.preferred_capabilities, *requirement.allowed_fallbacks}):
        raise ValueError("research_policy_trajectory_route_invalid")
    adapter = session.query(RunRoleOutputRecord).filter_by(run_id=reservation.run_id,
        role="research_adapter_return:" + hashlib.sha256(call_id.encode()).hexdigest()[:40]
        ).populate_existing().one_or_none()
    if adapter is not None:
        value = json.loads(adapter.payload_json)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "run_id", "request_id",
            "research_session_id", "generation", "capability_id", "reservation_query_hash",
            "effective_adapter_query_json", "effective_adapter_query_hash", "server_received_at",
            "adapter_result_json", "adapter_result_hash"}
            or adapter.schema_version != "research-adapter-return-observation.v1"
            or value["schema_version"] != adapter.schema_version
            or adapter.payload_hash != hashlib.sha256(adapter.payload_json.encode()).hexdigest()
            or adapter.output_id != "adapter_return_" + adapter.payload_hash
            or adapter.payload_json != _trajectory_json(value)
            or (value["run_id"], value["request_id"], value["research_session_id"], value["generation"],
                value["capability_id"], value["reservation_query_hash"])
            != (reservation.run_id, call_id, reservation.research_session_id, reservation.generation,
                reservation.capability_id, reservation.query_hash)
            or hashlib.sha256(value["effective_adapter_query_json"].encode()).hexdigest() != value["effective_adapter_query_hash"]
            or hashlib.sha256(value["adapter_result_json"].encode()).hexdigest() != value["adapter_result_hash"]):
            raise ValueError("research_policy_trajectory_adapter_invalid")
        effective = ResearchCapabilityQuery.model_validate_json(value["effective_adapter_query_json"])
        received = _pit_instant(value["server_received_at"])
        if effective.observed_at != received:
            raise ValueError("research_policy_trajectory_adapter_clock_invalid")
        if effective.model_dump(mode="json") != expected_query.model_copy(update={
                "requested_observed_at": expected_query.observed_at, "observed_at": received}).model_dump(mode="json"):
            raise ValueError("research_policy_trajectory_adapter_query_changed")
        adapter_id, query_json = adapter.output_id, value["effective_adapter_query_json"]
        rows.append(adapter)
    if reservation.query_hash is None:
        return None, rows, "trajectory_effective_query_unavailable"
    admitted_query = expected_query
    query_binding = {"schema_version": "research-tool-query-binding.v2",
        "query": admitted_query.model_dump(mode="json"), "admitted_anchor": anchor,
        "timing_identity": timing_identity}
    if reservation.query_hash != hashlib.sha256(_trajectory_json(query_binding).encode()).hexdigest():
        raise ValueError("research_policy_trajectory_reservation_query_changed")
    if query_json is None:
        # A failed adapter need not return. Full native intent + exact timing,
        # original Prepared anchor and reservation hash authenticate its query.
        query_json = _trajectory_json(expected_query.model_dump(mode="json"))
    if reservation.status not in {"running", "completed", "failed", "cancelled", "denied", "timed_out", "unknown"}:
        raise ValueError("research_policy_trajectory_reservation_status_invalid")
    return dict(request_id=call_id, capability_id=reservation.capability_id,
        requirement_id=invocation.requirement_id, generation=reservation.generation,
        research_session_id=reservation.research_session_id, invocation_json=intent_json,
        query_json=query_json, timing_output_id=timing_id, adapter_output_id=adapter_id,
        status=reservation.status), rows, None


def _trajectory_originals(session: Session, bundle: Any, prepared: Any, original: Any,
                          last_round: Any, bundle_identity: Any) -> _TrajectoryMaterials:
    run_id = bundle.task.run_id
    graph_json = _trajectory_json(original.reference.model_dump(mode="json"))
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None or link is None:
        raise ValueError("research_policy_trajectory_run_missing")
    budget = bundle.request.execution_budget
    reservations = session.query(ResearchToolCallReservationRecord).filter_by(run_id=run_id
        ).populate_existing().order_by(ResearchToolCallReservationRecord.reserved_at,
            ResearchToolCallReservationRecord.request_id).limit(budget.max_tool_calls + 1).all()
    reasons = []
    if len(reservations) > budget.max_tool_calls or len(reservations) != link.tool_calls_started:
        reasons.append("trajectory_reservation_census_unavailable")
    if len(reservations) > 100:
        raise ValueError("research_policy_trajectory_reservation_limit")
    by_request = {item.request_id: item for item in reservations}
    for row in reservations:
        # These are freshly read internal SQLite rows. Decode their storage
        # UTC only here; native argument clocks always use aware DTOs above.
        reserved, completed = as_utc(row.reserved_at), as_utc(row.completed_at)
        if reserved is None or (completed is not None and completed < reserved):
            raise ValueError("research_policy_trajectory_reservation_clock_invalid")
    rounds, current, current_ref, immutable, all_calls = [], last_round, original.receipt.last_admission_ref.row, {}, {}
    server_calls = {}
    if prepared.method.definition.profile_ref == "decision-research.web.baseline.v1":
        from .research_baseline import read_baseline_slots_in_session, read_baseline_delivery_in_session, BATCH_ROLE
        from ..ports.research_baseline import BaselineRequest
        try:
            _, _, baseline_rows, _, _, baseline_calls = read_baseline_slots_in_session(
                session, run_id=run_id, recorded_at=original.receipt.recorded_at)
        except ValueError as exc:
            if str(exc) not in {"research_baseline_plan_missing", "research_baseline_slot_not_terminal"}:
                raise
            reasons.append(str(exc))
        else:
            server_calls = {call["request_id"]: call for call in baseline_calls}
            batch = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=BATCH_ROLE).populate_existing().one_or_none()
            if batch is None:
                reasons.append("trajectory_server_baseline_delivery_unavailable")
            else:
                authenticated_batch = read_baseline_delivery_in_session(session,
                    request=BaselineRequest(schema_version="research-baseline-request.v1",
                        run_id=run_id, research_session_id=prepared.execution.dsh_session_id,
                        generation=prepared.execution.generation,
                        request_hash=prepared.method.request_hash),
                    as_of=original.receipt.recorded_at)
                if authenticated_batch is None:
                    raise ValueError("research_policy_server_baseline_original_missing")
                baseline_rows.append(batch)
            for row in baseline_rows:
                locator = row.output_id if isinstance(row, RunRoleOutputRecord) else f"{run_id}:1"
                immutable[locator] = _trajectory_row(row, locator)
    while current is not None:
        if len(rounds) >= budget.max_evidence_rounds or len(rounds) >= 10:
            raise ValueError("research_policy_trajectory_round_limit")
        generation = current.native_context.generation
        saved = read_prepared_execution_in_session(session, run_id, generation)
        anchor, anchor_rows = _trajectory_anchor(session, bundle, bundle_identity, saved)
        for row in anchor_rows:
            locator = row.output_id if isinstance(row, RunRoleOutputRecord) else f"{run_id}:{generation}"
            immutable[locator] = _trajectory_row(row, locator)
        raw = (read_transport_observation(session, current.observation_ref) if current.observation_kind == "failure"
               else read_transport_observation(session,
                   read_mapped_observation(session, current.observation_ref).mapping_input_ref))
        mapped = (read_mapped_observation(session, current.observation_ref)
                  if current.observation_kind == "mapped" else None)
        native_calls, missing = _trajectory_native_calls(raw)
        calls = []
        for call_id, args, call_hash, result_hash in native_calls:
            reservation = by_request.get(call_id)
            if reservation is None:
                missing.append("trajectory_native_call_not_reserved")
                continue
            # A cumulative native archive can contain calls from prior rounds.
            # Only the original timing/reservation generation identifies them.
            if reservation.generation != generation:
                continue
            call, rows, problem = _trajectory_call(session, reservation, call_id, args, call_hash,
                                                  result_hash, saved, anchor)
            for row in rows:
                immutable[row.output_id] = _trajectory_row(row, row.output_id)
            if problem:
                missing.append(problem)
            elif call is not None:
                if (reservation.completed_at is not None and isinstance(raw.reply, DshSessionResult)
                        and as_utc(reservation.completed_at) > raw.reply.finished_at):
                    missing.append("trajectory_completion_after_native_result")
                prior = all_calls.get(call_id)
                if prior is not None and prior != call:
                    raise ValueError("research_policy_trajectory_call_conflict")
                all_calls[call_id] = call
                calls.append(call)
        if {item.request_id for item in reservations if item.generation == generation} != (
            {item.request_id for item in calls}
            | {key for key, call in server_calls.items() if call["generation"] == generation}
        ):
            missing.append("trajectory_generation_call_census_unavailable")
        rounds.append(_TrajectoryRound(logical_round=current.logical_round, generation=generation,
            research_session_id=current.native_context.dsh_session_id,
            round_ref_json=_trajectory_json(current_ref.model_dump(mode="json")),
            request_json=_trajectory_json(saved.execution.request.model_dump(mode="json")),
            result_json=_trajectory_json(mapped.result.model_dump(mode="json")) if mapped is not None else None,
            transport_ref_json=_trajectory_json((mapped.mapping_input_ref if mapped is not None else current.observation_ref).model_dump(mode="json")),
            evidence_json=_trajectory_json([item.model_dump(mode="json") for item in current.evidence]),
            facts_json=_trajectory_json([item.model_dump(mode="json") for item in current.facts]),
            calls=tuple(calls), incomplete_reasons=tuple(sorted(set(missing)))))
        current_ref = current.previous_ref.row if current.previous_ref else None
        current = _read_round_receipt(session, current_ref, run_id) if current_ref else None
    if set(by_request) != set(all_calls) | set(server_calls):
        # For example, a structured-repair generation may have reservations but
        # no admitted Graph Round. Its mutable count cannot exhaust a route;
        # this slice leaves it NA instead of borrowing another generation's args.
        reasons.append("trajectory_unobserved_generation_call_census_unavailable")
    traces = session.query(ResearchTraceRecord).filter_by(run_id=run_id, reference_type="capability_call"
        ).populate_existing().order_by(ResearchTraceRecord.sequence_no).limit(1201).all()
    if len(traces) > 1200:
        raise ValueError("research_policy_trajectory_trace_limit")
    session_ids = {row.research_session_id for row in rounds}
    for row in traces:
        # Match the original writer's typed JSON codec, including UTC Z.
        # Its sole historical null-array projection verifies the original hash;
        # restore that exact raw error before this independent comparison.
        projected = read_trace_projection(row)
        semantic = projected.model_dump(mode="json", exclude={"sequence_no", "error_verification"})
        if projected.error_verification == "legacy_provider_attempts_unverifiable":
            semantic["error"] = json.loads(row.error_provenance_json)
        if row.research_session_id not in session_ids or row.event_hash != recipe_hash(semantic):
            raise ValueError("research_policy_trajectory_trace_original_invalid")
    commands = session.query(ResearchCommandRecord).filter_by(run_id=run_id
        ).populate_existing().order_by(ResearchCommandRecord.created_at).limit(33).all()
    if len(commands) > 32:
        raise ValueError("research_policy_trajectory_audit_limit")
    # Scope/identity, canonical payload and hash are retained. They deliberately
    # do not certify billing completeness or physical cancellation/drain.
    value = _TrajectorySnapshot(run_id=run_id, event_id=bundle.request.event_id,
        task_request_hash=bundle.task.request_hash, method_hash=prepared.method.method_hash,
        checker_code_hash=prepared.method.definition.policy_binding.checker_code_hash,
        graph_ref_json=graph_json, phase="finalization", assessed_at=original.receipt.recorded_at,
        rounds=tuple(reversed(rounds)), reservations=tuple(_trajectory_row(row, row.request_id) for row in reservations),
        immutable_rows=tuple(immutable[key] for key in sorted(immutable)),
        traces=tuple(_trajectory_row(row, str(row.id)) for row in traces),
        usage_rows=(),
        link=_trajectory_row(link, run_id), run=_trajectory_row(run, run_id),
        command_rows=tuple(_trajectory_row(row, row.request_id) for row in commands),
        incomplete_reasons=tuple(sorted(set(reasons))))
    value = _trajectory_with_usage_rows(session, value)
    saved = tuple((phase, payload) for phase in ("finalization", "commit")
                  if (payload := _trajectory_read_saved(session, run_id=run_id, graph_ref_json=graph_json,
                     method_hash=value.method_hash, phase=phase)) is not None)
    phases = tuple(phase for phase in ("finalization", "commit") if session.query(RunRoleOutputRecord).filter(
        RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role.like("policy_check:" + phase + ":%")
        ).first() is not None)
    for _, payload in saved:
        _trajectory_monotonic(_TrajectorySnapshot.model_validate_json(payload), value)
    return _TrajectoryMaterials(_trajectory_encoded(value), saved, phases)


def _trajectory_monotonic(prior: _TrajectorySnapshot, current: _TrajectorySnapshot) -> None:
    if (prior.run_id, prior.event_id, prior.task_request_hash, prior.method_hash,
        prior.checker_code_hash, prior.graph_ref_json, prior.rounds) != (
        current.run_id, current.event_id, current.task_request_hash, current.method_hash,
        current.checker_code_hash, current.graph_ref_json, current.rounds):
        # The saved rounds include original call statuses. They are historical
        # values, so compare their immutable identities separately below.
        if (prior.run_id, prior.event_id, prior.task_request_hash, prior.method_hash,
            prior.checker_code_hash, prior.graph_ref_json) != (
            current.run_id, current.event_id, current.task_request_hash, current.method_hash,
            current.checker_code_hash, current.graph_ref_json):
            raise ValueError("research_policy_trajectory_binding_changed")
        for left, right in zip(prior.rounds, current.rounds, strict=True):
            if left.model_dump(exclude={"calls", "incomplete_reasons"}) != right.model_dump(exclude={"calls", "incomplete_reasons"}):
                raise ValueError("research_policy_trajectory_round_changed")
            current_calls = {item.request_id: item for item in right.calls}
            for call in left.calls:
                new = current_calls.get(call.request_id)
                if new is None or call.model_dump(exclude={"status"}) != new.model_dump(exclude={"status"}):
                    raise ValueError("research_policy_trajectory_call_original_changed")
    immutable = {row.locator: row for row in current.immutable_rows}
    if any(immutable.get(row.locator) != row for row in prior.immutable_rows):
        raise ValueError("research_policy_trajectory_immutable_original_changed")
    for old_rows, new_rows in ((prior.traces, current.traces), (prior.usage_rows, current.usage_rows)):
        indexed = {row.locator: row for row in new_rows}
        if any(indexed.get(row.locator) != row for row in old_rows):
            raise ValueError("research_policy_trajectory_audit_original_changed")
    reservations = {row.locator: json.loads(row.payload_json) for row in current.reservations}
    for row in prior.reservations:
        left, right = json.loads(row.payload_json), reservations.get(row.locator)
        if right is None or any(left[key] != right[key] for key in (
            "run_id", "request_id", "research_session_id", "generation", "capability_id", "query_hash", "reserved_at")):
            raise ValueError("research_policy_trajectory_reservation_identity_changed")
        if left["status"] != "running" and left != right:
            raise ValueError("research_policy_trajectory_terminal_original_changed")
        if left["status"] == "running" and right["status"] not in {"running", "completed", "failed", "cancelled"}:
            raise ValueError("research_policy_trajectory_reservation_transition_invalid")


def _trajectory_for_phase(materials: _TrajectoryMaterials | None, phase: Phase, assessed_at: datetime,
                          *, allow_freeze: bool = False) -> _TrajectorySnapshot | None:
    if materials is None:
        return None
    payload = dict(materials.saved_json).get(phase)
    if payload is not None:
        value = _TrajectorySnapshot.model_validate_json(payload)
        if as_utc(value.assessed_at) != as_utc(assessed_at):
            raise ValueError("research_policy_trajectory_phase_time_changed")
        return value
    if not allow_freeze:
        return None
    if phase in materials.existing_policy_phases:
        raise ValueError("research_policy_trajectory_missing_for_saved_phase")
    value = _TrajectorySnapshot.model_validate_json(materials.current_json)
    return _TrajectorySnapshot.model_validate_json(_trajectory_json({
        **value.model_dump(mode="json"), "phase": phase, "assessed_at": as_utc(assessed_at).isoformat()}))


def _gap_trajectory_outcome(rule: Any, body: dict[str, Any], refs: tuple[tuple[str, str, str], ...],
                            *, code: str, bounded: bool, needs: bool, legal_bound: bool) -> _DeterministicOutcome:
    if code == "sufficient" and (bounded or needs):
        return _deterministic_outcome(rule.rule_id, "fail",
            "trajectory_actionable_gap_claimed_sufficient", body, refs)
    if not needs or legal_bound:
        return _deterministic_outcome(rule.rule_id, "pass",
            "trajectory_gap_paths_or_bound_verified", body, refs)
    # No inference that money/time remains, or that owner ACK drained the
    # execution. The independently verified path stays visible as unknown.
    return _deterministic_outcome(rule.rule_id, "not_available",
        "trajectory_gap_stop_budget_unverified", body, refs)


def _bounded_stop_trajectory_outcome(rule: Any, body: dict[str, Any],
                                    refs: tuple[tuple[str, str, str], ...], *, code: str,
                                    bounded: bool, round_end: bool, no_progress_rounds: int) -> _DeterministicOutcome:
    conflict = ((code == "round_budget" and not round_end)
        or (code == "no_progress" and no_progress_rounds < 2) or (code == "sufficient" and bounded))
    # tool_budget can also be a subagent limit. Without its complete native
    # census we must not refute it merely because capability count is lower.
    return _deterministic_outcome(rule.rule_id, "fail" if conflict else "not_available",
        "trajectory_stop_original_budget_conflict" if conflict else "trajectory_complete_usage_or_drain_unavailable",
        body, refs)


def _trajectory_outcomes(value: _TrajectorySnapshot | None, bundle: Any, result: ResearchSessionResult,
                         rules: Any) -> dict[str, _DeterministicOutcome]:
    body = {"task_request_hash": bundle.task.request_hash, "trajectory": value.model_dump(mode="json") if value else None}
    outcomes = {name: _deterministic_outcome(name, "not_available",
        "trajectory_original_snapshot_unavailable", body) for name in _TRAJECTORY_IDS}
    if value is None:
        return outcomes
    encoded = _trajectory_encoded(value)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    refs = (("policy", "policy_trajectory_" + digest, digest),) + tuple(
        ("tool_reservation", "reservation_" + recipe_hash({"run_id": value.run_id, "request_id": row.locator}),
         row.payload_hash) for row in value.reservations) + tuple(
        ("trace", "trace_" + recipe_hash({"run_id": value.run_id, "id": row.locator}), row.payload_hash)
        for row in value.traces)
    if len(refs) > 256:
        return {name: _deterministic_outcome(name, "not_available", "trajectory_reference_limit",
            body, (("policy", "policy_trajectory_" + digest, digest),)) for name in _TRAJECTORY_IDS}
    for name in outcomes:
        outcomes[name] = _deterministic_outcome(name, "not_available", "trajectory_originals_incomplete", body, refs)
    by_rule = {rule.definition.rule_id: rule.definition for rule in rules}
    gap = by_rule["gap-to-tool-continuation"]
    stop = by_rule["bounded-stop-consistency"]
    if (gap.params.input_basis != "durable_requests_coverage_reservations"
            or stop.params.input_basis != "original_budget_and_verified_cancel"
            or stop.params.unknown_usage != "not_available"):
        return outcomes
    if value.incomplete_reasons or any(row.incomplete_reasons for row in value.rounds):
        return outcomes
    # A real reservation cancellation is a terminal ledger status, but cannot
    # establish path exhaustion, complete billing, or native child/drain proof.
    if any(json.loads(row.payload_json)["status"] == "cancelled" for row in value.reservations):
        return {name: _deterministic_outcome(name, "not_available", "trajectory_cancelled_drain_unavailable", body, refs)
                for name in _TRAJECTORY_IDS}
    if any(json.loads(row.payload_json)["status"] in {"running", "unknown"} for row in value.reservations):
        return {name: _deterministic_outcome(name, "not_available", "trajectory_calls_not_settled", body, refs)
                for name in _TRAJECTORY_IDS}
    if any(json.loads(row.payload_json)["completed_at"] is None for row in value.reservations):
        return {name: _deterministic_outcome(name, "not_available", "trajectory_completion_clock_unavailable", body, refs)
                for name in _TRAJECTORY_IDS}
    attempted, handled, observations, previous_ids, previous_coverage, idle = {}, [], [], set(), None, 0
    if not bundle.request.input_evidence:
        return {name: _deterministic_outcome(name, "not_available", "trajectory_original_trigger_cutoff_unavailable",
                                            body, refs) for name in _TRAJECTORY_IDS}
    prior_cutoff = max(item.received_at for item in bundle.request.input_evidence)
    for row in value.rounds:
        if row.result_json is None:
            return outcomes
        actual = ResearchSessionRequest.model_validate_json(row.request_json)
        original_result = ResearchSessionResult.model_validate_json(row.result_json)
        evidence = [EvidenceCandidate.model_validate(item) for item in json.loads(row.evidence_json)]
        facts = [FactEnvelope.model_validate(item) for item in json.loads(row.facts_json)]
        if row.generation == 1:
            reservations = {json.loads(item.payload_json)["request_id"] for item in value.reservations}
            for original_row in value.immutable_rows:
                original_body = json.loads(original_row.payload_json)
                if original_row.table != "run_role_outputs" or original_body.get("role") != "research_baseline_plan:1":
                    continue
                # This plan entered immutable_rows only after full server admission
                # authentication above. It adds attempts, never native Tool hashes.
                plan = json.loads(original_body["payload_json"])
                for slot in plan["slots"]:
                    invocation = slot["invocation"]
                    if invocation["request_id"] in reservations:
                        attempted.setdefault(invocation["requirement_id"], []).append(invocation["capability_id"])
        for call in row.calls:
            attempted.setdefault(call.requirement_id, []).append(call.capability_id)
        cutoff = actual.pit_cutoff_at if actual.execution_mode == "replay" else max(prior_cutoff, original_result.finished_at)
        prior_cutoff = cutoff
        coverage = assess_evidence_sufficiency(bundle.request.evidence_requirements, evidence,
            cutoff_at=cutoff, assessed_at=cutoff, facts=facts)
        loop = assess_research_loop(requirements=bundle.request.evidence_requirements, coverage=coverage,
            result=original_result, evidence=evidence, attempted_capabilities_by_requirement=attempted,
            allowed_capabilities=bundle.request.allowed_capabilities, handled_review_fingerprints=handled,
            input_evidence_ids=[item.evidence_id for item in bundle.request.input_evidence])
        ids = {item.evidence_id for item in evidence if item.quality == "accepted"}
        progressed = bool(ids - previous_ids) or (previous_coverage is not None
            and set(previous_coverage.covered_requirement_ids) != set(coverage.covered_requirement_ids))
        idle = 0 if progressed else idle + 1
        previous_ids, previous_coverage = ids, coverage
        observations.append({"round": row.logical_round, "generation": row.generation,
            "coverage": coverage.model_dump(mode="json"), "loop": asdict(loop), "no_progress_rounds": idle})
        if loop.reviewer_fingerprint is not None:
            handled.append(loop.reviewer_fingerprint)
    body["independent_loops"] = observations
    last = observations[-1] if observations else None
    if last is None or result.stop_reason is None:
        return outcomes
    budget = bundle.request.execution_budget
    count = len(value.reservations)
    code, bounded = result.stop_reason.code, result.stop_reason.bounded
    tool_end, round_end = count >= budget.max_tool_calls, len(value.rounds) >= budget.max_evidence_rounds
    if count > budget.max_tool_calls or len(value.rounds) > budget.max_evidence_rounds:
        return {name: _deterministic_outcome(name, "fail", "trajectory_original_budget_exceeded", body, refs)
                for name in _TRAJECTORY_IDS}
    needs = bool(last["loop"]["should_continue"] and last["loop"]["blocks_completion"])
    legal_bound = ((code == "tool_budget" and tool_end) or (code == "round_budget" and round_end)
        or (code == "no_progress" and idle >= 2) or (code == "no_actionable_path"
            and last["loop"]["reason_code"] == "no_actionable_path")) and bounded
    outcomes[gap.rule_id] = _gap_trajectory_outcome(gap, body, refs, code=code,
        bounded=bounded, needs=needs, legal_bound=legal_bound)
    outcomes[stop.rule_id] = _bounded_stop_trajectory_outcome(stop, body, refs, code=code,
        bounded=bounded, round_end=round_end, no_progress_rounds=idle)
    return outcomes
def _probability_phase(materials: Any, bundle: Any, prepared: Any, original: Any, round_receipt: Any,
                       phase: Phase, assessed: datetime, allow_freeze: bool = False) -> Any:
    if materials is None:
        return None
    from .research_graph_binding import authenticated_graph_recipe_identity

    if authenticated_graph_recipe_identity(original.receipt) is None:
        if materials.binding.selection is not None:
            raise ValueError("research_probability_graph_recipe_unavailable")
        # An absent identity keeps the report but never grants probability
        # qualification or creates a placeholder recipe hash.
        return None
    selected = None
    if round_receipt.selected_synthesis_observation_ref is not None:
        raw = round_receipt.projected_state.get("latest_result")
        if raw is None:
            raise ValueError("research_policy_selected_synthesis_original_missing")
        selected = ResearchSessionResult.model_validate_json(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    return research_probability_binding.probability_phase(materials, prepared=prepared, bundle=bundle,
        original=original, selected=selected, phase=phase, assessed_at=assessed, allow_freeze=allow_freeze)


def _probability_outcome(materials: Any, bundle: Any, prepared: Any, round_receipt: Any,
                         result: ResearchSessionResult, rules: Any,
                         reference: datetime, values: Any) -> _DeterministicOutcome:
    rule = next(item.definition for item in rules if item.definition.rule_id == "probability-provenance")
    if materials is None:
        return _deterministic_outcome("probability-provenance", "not_available",
            "probability_physical_snapshot_unavailable", {"task_request_hash": bundle.task.request_hash})
    if values is None:
        if materials.binding.selection is not None:
            raise ValueError("research_probability_graph_recipe_unavailable")
        return _deterministic_outcome("probability-provenance", "not_available",
            "legacy_graph_identity_unavailable", {
                "task_request_hash": bundle.task.request_hash,
                "first_binding_payload_hash": materials.binding_payload_hash,
                "state_hash": round_receipt.state_hash,
            })
    selected = None
    if round_receipt.selected_synthesis_observation_ref is not None:
        raw = round_receipt.projected_state.get("latest_result")
        if raw is None:
            raise ValueError("research_policy_selected_synthesis_original_missing")
        selected = ResearchSessionResult.model_validate_json(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    value = research_probability_binding.probability_outcome(
        materials, selected=selected, result=result, decision_at=reference, rule=rule, values=values)
    # Bind the actual authenticated raw synthesis locator as well as its body;
    # neither the final Graph's display downgrade nor Commit can erase it.
    selected_ref = (round_receipt.selected_synthesis_observation_ref.model_dump(mode="json")
                    if round_receipt.selected_synthesis_observation_ref is not None else None)
    return _DeterministicOutcome(value.status, value.reason, recipe_hash({
        "probability_input_hash": value.input_hash, "state_hash": round_receipt.state_hash,
        "selected_synthesis_observation_ref": selected_ref,
        "prepared_ai_method_hash": prepared.method.method_hash,
        "prepared_ai_method_definition": prepared.method.definition.model_dump(mode="json"),
    }), value.references)


def _policy_rule_checks(rules: Any, membership: Any, refs: list[dict[str, str]],
                        input_hash: str, pit: _PitMaterials,
                        reference: datetime, assessed: datetime,
                        deterministic: dict[str, _DeterministicOutcome],
                        trajectory: dict[str, _DeterministicOutcome] | None = None,
                        probability: _DeterministicOutcome | None = None) -> list[dict[str, Any]]:
    checks = []
    for compiled in rules:
        rule, applicability = compiled.definition, compiled.applicability
        status, reason = "not_available", "canonical_handler_not_available"
        rule_input = recipe_hash({"rule": rule.model_dump(mode="json"), "input_set_hash": input_hash})
        rule_refs = refs if rule.rule_id == "citation-membership" else []
        if rule.rule_id == "citation-membership":
            reason = "citation_scope_unavailable"
            if applicability == "applicable":
                if membership.disposition == "not_applicable":
                    applicability, reason = "not_applicable", "citation_not_applicable"
                else:
                    status = "pass" if membership.disposition == "accepted" else "fail"
                    reason = "citation_membership_verified" if status == "pass" else "citation_membership_rejected"
        elif rule.rule_id == "pit":
            reason = "pit_scope_unavailable"
            rule_input = recipe_hash({"rule": rule.model_dump(mode="json"), "pit_input_hash": pit.input_hash})
            rule_refs = [dict(asset_kind=kind, asset_id=identifier, payload_hash=digest)
                         for kind, identifier, digest in pit.references]
            if applicability == "applicable":
                status, reason = _pit_verdict(pit)
        elif rule.rule_id in _DETERMINISTIC_RULE_IDS:
            outcome = deterministic[rule.rule_id]
            rule_input = recipe_hash({"rule": rule.model_dump(mode="json"),
                                      "deterministic_input_hash": outcome.input_hash})
            rule_refs = [dict(asset_kind=kind, asset_id=identifier, payload_hash=digest)
                         for kind, identifier, digest in outcome.references]
            reason = "deterministic_scope_unavailable"
            if applicability == "applicable":
                status, reason = outcome.status, outcome.reason
        elif rule.rule_id in _TRAJECTORY_IDS:
            outcome = (trajectory or {}).get(rule.rule_id)
            reason = "trajectory_original_snapshot_unavailable"
            if outcome is not None:
                rule_input = recipe_hash({"rule": rule.model_dump(mode="json"),
                                          "trajectory_input_hash": outcome.input_hash})
                rule_refs = [dict(asset_kind=kind, asset_id=identifier, payload_hash=digest)
                             for kind, identifier, digest in outcome.references]
                reason = "trajectory_scope_unavailable"
                if applicability == "applicable":
                    status, reason = outcome.status, outcome.reason
        elif rule.rule_id == "probability-provenance":
            reason = "probability_physical_snapshot_unavailable"
            if probability is not None:
                rule_input = recipe_hash({"rule": rule.model_dump(mode="json"),
                                          "probability_input_hash": probability.input_hash})
                rule_refs = [dict(asset_kind=kind, asset_id=identifier, payload_hash=digest)
                             for kind, identifier, digest in probability.references]
                reason = "probability_scope_unavailable"
                if applicability == "applicable":
                    status, reason = probability.status, probability.reason
        checks.append(dict(
            rule_id=rule.rule_id, rule_version=rule.rule_version, status=status,
            applicability=applicability, blocking_for=rule.blocking_for, reason_code=reason,
            input_hash=rule_input, evidence_refs=rule_refs, reference_at=reference, observed_at=assessed,
        ))
    return checks


def _assessment_without_snapshot(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, phase: Phase, assessed_at: datetime,
    expected_lease_owner: str | None,
    expected_artifact_id: str | None = None, writer: bool = False,
) -> ResearchPolicyCheckReceipt | None:
    if phase not in {"finalization", "commit"}:
        raise ValueError("research_policy_phase_not_supported")
    binding = _binding(session, run_id)
    if binding is None:
        return None
    if writer:
        original = verify_graph_citation_membership_in_session(
            session, run_id=run_id, reference=graph_ref,
            expected_lease_owner=expected_lease_owner,
            expected_artifact_id=expected_artifact_id,
        )
    else:
        original = read_graph_final_in_session(
            session, run_id=run_id, reference=graph_ref,
            expected_lease_owner=None, require_current_permission=False,
        )
    result = ResearchSessionResult.model_validate_json(result.model_dump_json())
    if (
        phase == "finalization" and result != original.result
        or phase == "commit" and _claim_body(result) != _claim_body(original.result)
    ):
        raise ValueError("research_policy_result_original_mismatch")
    assessed, reference = as_utc(assessed_at), as_utc(original.receipt.decision_cutoff_at)
    if assessed is None or reference is None or assessed < reference:
        raise ValueError("research_policy_assessment_time_invalid")
    if phase == "finalization" and assessed != as_utc(original.receipt.recorded_at):
        raise ValueError("research_policy_finalization_time_mismatch")
    round_original = read_graph_round_in_session(
        session, run_id=run_id, reference=original.receipt.last_admission_ref,
        expected_lease_owner=None, require_current_permission=False,
    )
    bundle, bundle_identity = read_bundle_identity_in_session(session, run_id)
    prepared = read_prepared_execution_in_session(session, run_id)
    rules = bundle.compiled.compiled.compiled_rules
    if tuple(rule.definition.rule_id for rule in rules) != research_policy_registry.REGISTERED_RULE_IDS:
        raise ValueError("research_policy_canonical_rule_set_invalid")
    trigger = session.get(SnapshotRecord, bundle.request.trigger_snapshot_id, populate_existing=True)
    decision = session.get(SnapshotRecord, original.receipt.decision_snapshot_id, populate_existing=True)
    if (
        trigger is None or decision is None or trigger.event_id != bundle.request.event_id
        or decision.run_id != run_id or decision.snapshot_hash != original.receipt.decision_snapshot_hash
    ):
        raise ValueError("research_policy_snapshot_original_mismatch")
    accepted = [e for e in round_original.receipt.evidence if e.quality == "accepted"]
    membership = citation_membership_v2.check_citation_membership_v2(
        result, accepted_evidence_ids=[e.evidence_id for e in accepted],
        original_input_ids=[e.evidence_id for e in bundle.request.input_evidence],
    )
    refs = [dict(asset_kind="evidence", asset_id=e.evidence_id,
                 payload_hash=recipe_hash(e.model_dump(mode="json"))) for e in accepted]
    if len(refs) > 256:
        raise ValueError("research_policy_evidence_reference_limit")
    pit = _pit_unavailable(bundle, original, "pit_physical_snapshot_unavailable")
    deterministic = _deterministic_unavailable(bundle, original)
    input_hash = recipe_hash({
        "graph_ref": graph_ref.model_dump(mode="json"),
        "round_ref": original.receipt.last_admission_ref.model_dump(mode="json"),
        "bundle_identity": bundle_identity.model_dump(mode="json"),
        "binding": binding.model_dump(mode="json"),
        "accepted_evidence": [e.model_dump(mode="json") for e in accepted],
        "original_input_ids": [e.evidence_id for e in bundle.request.input_evidence],
        "membership": membership.model_dump(mode="json"),
        "pit_input_hash": pit.input_hash,
        "deterministic_input_hashes": {name: item.input_hash for name, item in deterministic.items()},
        "result_payload_hash": recipe_hash(result.model_dump(mode="json")),
    })
    checks = _policy_rule_checks(rules, membership, refs, input_hash, pit, reference, assessed, deterministic)
    body = dict(
        schema_version="research-policy-check.v1", receipt_id="pending", full_identity_hash="0" * 64,
        method_hash=prepared.method.method_hash, run_id=run_id, event_id=bundle.request.event_id,
        phase=phase, binding=binding, trigger_snapshot_id=trigger.snapshot_id,
        trigger_snapshot_hash=trigger.snapshot_hash, decision_snapshot_id=decision.snapshot_id,
        decision_snapshot_hash=decision.snapshot_hash, reference_at=reference,
        result_content_hash=research_result_content_hash(result),
        result_payload_hash=recipe_hash(result.model_dump(mode="json")), input_set_hash=input_hash,
        checks=checks, all_applicable_blocking_rules_passed=research_policy_registry.blocking_checks_passed(checks), assessed_at=assessed,
        assessment_state="executed", checker_version=binding.checker_version,
        checker_code_hash=binding.checker_code_hash,
    )
    receipt = ResearchPolicyCheckReceipt.model_validate(body)
    identity_hash = policy_receipt_identity_hash(receipt)
    return ResearchPolicyCheckReceipt.model_validate({
        **body, "full_identity_hash": identity_hash, "receipt_id": policy_receipt_id(identity_hash),
    })


def execute_citation_policy_in_session(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, phase: Phase, assessed_at: datetime,
    expected_lease_owner: str | None, expected_artifact_id: str | None = None,
) -> ResearchPolicyCheckReceipt | None:
    """Fresh same-writer assessment and immutable insertion; no commit here."""
    receipt = _assessment(
        session, run_id=run_id, graph_ref=graph_ref, result=result, phase=phase,
        assessed_at=assessed_at, expected_lease_owner=expected_lease_owner,
        expected_artifact_id=expected_artifact_id, writer=True, freeze_trajectory=True,
    )
    return store_policy_check_receipt(session, receipt) if receipt is not None else None


def verify_finalization_policy_in_session(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    expected_lease_owner: str | None, expected_artifact_id: str | None = None,
) -> ResearchPolicyCheckReceipt | None:
    if _has_read_snapshot(session):
        with _policy_materials(session, run_id=run_id, graph_ref=graph_ref,
                               expected_lease_owner=expected_lease_owner,
                               expected_artifact_id=expected_artifact_id, writer=True) as materials:
            if materials.binding is None:
                return None
            expected = _assessment_from_materials(
                materials, run_id=run_id, graph_ref=graph_ref, result=materials.original.result,
                phase="finalization", assessed_at=materials.original.receipt.recorded_at,
            )
            if read_policy_check_receipt(session, expected.receipt_id) != expected:
                raise ValueError("research_policy_finalization_receipt_invalid")
            return expected
    original = read_graph_final_in_session(
        session, run_id=run_id, reference=graph_ref,
        expected_lease_owner=None, require_current_permission=False,
    )
    expected = _assessment(
        session, run_id=run_id, graph_ref=graph_ref, result=original.result,
        phase="finalization", assessed_at=original.receipt.recorded_at,
        expected_lease_owner=expected_lease_owner,
        expected_artifact_id=expected_artifact_id, writer=True,
    )
    if expected is None:
        return None
    if read_policy_check_receipt(session, expected.receipt_id) != expected:
        raise ValueError("research_policy_finalization_receipt_invalid")
    return expected


def policy_phase_reference(session: Session, receipt: ResearchPolicyCheckReceipt) -> dict[str, str]:
    row = session.get(RunRoleOutputRecord, receipt.receipt_id, populate_existing=True)
    if row is None or read_policy_check_receipt(session, receipt.receipt_id) != receipt:
        raise ValueError("research_policy_phase_reference_invalid")
    return {"receipt_id": row.output_id, "payload_hash": row.payload_hash}


def _authenticate_without_snapshot(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, references: object,
    expected_lease_owner: str | None = None, expected_artifact_id: str | None = None,
    writer: bool = False,
) -> list[ResearchPolicyCheckReceipt]:
    """Recompute both phases from originals; read-only use grants no permission."""
    binding = _binding(session, run_id)
    if binding is None:
        if references is not None:
            raise ValueError("research_policy_legacy_phase_upgrade_forbidden")
        return []
    if not isinstance(references, dict) or set(references) != {"finalization", "commit"}:
        raise ValueError("research_policy_publication_phases_missing")
    original = read_graph_final_in_session(
        session, run_id=run_id, reference=graph_ref,
        expected_lease_owner=None, require_current_permission=False,
    )
    receipts = []
    for phase in ("finalization", "commit"):
        ref = references[phase]
        if not isinstance(ref, dict) or set(ref) != {"receipt_id", "payload_hash"}:
            raise ValueError("research_policy_phase_reference_invalid")
        saved = read_policy_check_receipt(session, ref["receipt_id"])
        if saved is None or saved.phase != phase or saved.run_id != run_id:
            raise ValueError("research_policy_phase_reference_invalid")
        if policy_phase_reference(session, saved) != ref:
            raise ValueError("research_policy_phase_reference_invalid")
        expected = _assessment_without_snapshot(
            session, run_id=run_id, graph_ref=graph_ref,
            result=original.result if phase == "finalization" else result,
            phase=phase, assessed_at=saved.assessed_at,
            expected_lease_owner=expected_lease_owner,
            expected_artifact_id=expected_artifact_id, writer=writer,
        )
        if expected != saved or (phase == "finalization" and as_utc(saved.assessed_at) != as_utc(original.receipt.recorded_at)):
            raise ValueError("research_policy_executed_receipt_mismatch")
        receipts.append(saved)
    return receipts


@dataclass(frozen=True)
class _PolicyInputs:
    prepared: Any
    bundle: Any
    bundle_identity: Any
    binding: ResearchPolicyMethodBinding | None
    original: Any
    round_receipt: Any
    rules: Any
    snapshots: tuple[str, str, str, str] | None
    pit: _PitMaterials | None
    trajectory: _TrajectoryMaterials | None = None
    probability: research_probability_binding.ProbabilityMaterials | None = None


@dataclass(frozen=True)
class _PolicyMaterials:
    # Preserve the positional ABI used by the historical original reader.
    prepared: Any
    bundle: Any
    bundle_identity: Any
    binding: ResearchPolicyMethodBinding | None
    original: Any
    round_receipt: Any
    rules: Any
    snapshots: tuple[str, str, str, str] | None
    pit: _PitMaterials | None
    unchanged: Callable[[], bool]
    trajectory: _TrajectoryMaterials | None = None
    probability: research_probability_binding.ProbabilityMaterials | None = None


@dataclass(frozen=True)
class _PreparedCommitPolicy:
    """Private data in Commit's PreparedOriginals, never publication permission."""

    inputs: _PolicyInputs
    finalization: ResearchPolicyCheckReceipt | None


@dataclass(frozen=True)
class _CommitPolicyValues:
    finalization: ResearchPolicyCheckReceipt | None
    commit: ResearchPolicyCheckReceipt | None
    trajectory: _TrajectorySnapshot | None
    probability: research_probability_binding.FrozenProbabilityValues | None


def _has_read_snapshot(session: Session) -> bool:
    native = session.connection().connection.driver_connection
    return bool(isinstance(native, sqlite3.Connection) and native.in_transaction
                and not session.new and not session.dirty and not session.deleted)


@contextmanager
def _policy_materials(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    expected_lease_owner: str | None = None, expected_artifact_id: str | None = None,
    writer: bool = False, read_legacy_graph: bool = False,
) -> Generator[_PolicyMaterials]:
    """Private originals for one physical transaction, never a reusable pass.

    The writer Graph fence runs before the reuse boundary is captured. Any
    subsequent flush/non-SELECT, native transaction/connection/owner change,
    pending UOW or source-byte drift invalidates it. Insertion occurs after
    this scope closes. Final Commit verification creates a separate scope.
    """
    _require_loaded_callables()
    if not _has_read_snapshot(session):
        raise ValueError("research_policy_physical_snapshot_required")
    source = _ReadSourceGuard.capture()
    if source is None:
        raise ValueError("research_policy_checker_current_implementation_unavailable")
    connection = session.connection()
    native = connection.connection.driver_connection
    transaction, nested = session.get_transaction(), session.get_nested_transaction()
    with session.no_autoflush:
        with _local_original_read(session) as binding_boundary:
            prepared, bundle, identity, binding = _binding_originals(session, run_id)
            if not binding_boundary.unchanged():
                raise ValueError("research_policy_snapshot_changed")
        original = None
        if binding is not None or read_legacy_graph:
            original = (verify_graph_citation_membership_in_session(
                session, run_id=run_id, reference=graph_ref,
                expected_lease_owner=expected_lease_owner,
                expected_artifact_id=expected_artifact_id,
            ) if writer else read_graph_final_in_session(
                session, run_id=run_id, reference=graph_ref,
                expected_lease_owner=expected_lease_owner, require_current_permission=False,
            ))
        if (session.connection() is not connection or not native.in_transaction
                or session.get_transaction() is not transaction
                or session.get_nested_transaction() is not nested):
            raise ValueError("research_policy_snapshot_changed")
        with _local_original_read(session) as boundary:
            round_receipt, rules, snapshots, pit, trajectory, probability = None, None, None, None, None, None
            if binding is not None:
                # The fresh full Graph above already authenticated this exact
                # last Round, all its original material and the complete chain.
                # Inside the same physical transaction and no-write boundary,
                # re-read its exact row/body hash/schema/recipe; do not replay
                # that same chain again or accept a caller-supplied Graph pass.
                round_receipt = _read_round_receipt(
                    session, original.receipt.last_admission_ref.row, run_id,
                )
                rules = bundle.compiled.compiled.compiled_rules
                if tuple(rule.definition.rule_id for rule in rules) != research_policy_registry.REGISTERED_RULE_IDS:
                    raise ValueError("research_policy_canonical_rule_set_invalid")
                trigger = session.get(SnapshotRecord, bundle.request.trigger_snapshot_id, populate_existing=True)
                decision = session.get(SnapshotRecord, original.receipt.decision_snapshot_id, populate_existing=True)
                if (trigger is None or decision is None or trigger.event_id != bundle.request.event_id
                        or decision.run_id != run_id or decision.snapshot_hash != original.receipt.decision_snapshot_hash):
                    raise ValueError("research_policy_snapshot_original_mismatch")
                snapshots = (trigger.snapshot_id, trigger.snapshot_hash, decision.snapshot_id, decision.snapshot_hash)
                pit = _pit_originals(session, bundle, original, round_receipt, trigger, decision)
                trajectory = _trajectory_originals(session, bundle, prepared, original, round_receipt, identity)
                probability = research_probability_binding.read_probability_materials_in_session(session, bundle)
            materials = _PolicyMaterials(prepared, bundle, identity, binding, original,
                                         round_receipt, rules, snapshots, pit, boundary.unchanged,
                                         trajectory, probability)
            if not boundary.unchanged():
                raise ValueError("research_policy_snapshot_changed")
            yield materials
            _require_loaded_callables()
            if not boundary.unchanged() or not source.matches():
                raise ValueError("research_policy_snapshot_changed")


def _assessment_from_materials(
    materials: _PolicyMaterials, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, phase: Phase, assessed_at: datetime,
    freeze_trajectory: bool = False,
) -> ResearchPolicyCheckReceipt | None:
    _require_loaded_callables()
    if not materials.unchanged():
        raise ValueError("research_policy_snapshot_changed")
    return _assessment_from_inputs(
        materials, run_id=run_id, graph_ref=graph_ref, result=result, phase=phase,
        assessed_at=assessed_at, freeze_trajectory=freeze_trajectory,
    )


def _assessment_from_inputs(
    materials: _PolicyInputs | _PolicyMaterials, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, phase: Phase, assessed_at: datetime,
    freeze_trajectory: bool = False,
) -> ResearchPolicyCheckReceipt | None:
    """Deterministic calculation; caller owns original/readset authentication."""
    _require_loaded_callables()
    if phase not in {"finalization", "commit"}:
        raise ValueError("research_policy_phase_not_supported")
    binding, original = materials.binding, materials.original
    if binding is None:
        return None
    result = ResearchSessionResult.model_validate_json(result.model_dump_json())
    if (
        phase == "finalization" and result != original.result
        or phase == "commit" and _claim_body(result) != _claim_body(original.result)
    ):
        raise ValueError("research_policy_result_original_mismatch")
    assessed, reference = as_utc(assessed_at), as_utc(original.receipt.decision_cutoff_at)
    if assessed is None or reference is None or assessed < reference:
        raise ValueError("research_policy_assessment_time_invalid")
    if phase == "finalization" and assessed != as_utc(original.receipt.recorded_at):
        raise ValueError("research_policy_finalization_time_mismatch")
    prepared, bundle, bundle_identity = materials.prepared, materials.bundle, materials.bundle_identity
    rules, round_receipt = materials.rules, materials.round_receipt
    deterministic = _deterministic_outcomes(bundle, round_receipt, result, rules, reference)
    trajectory_value = _trajectory_for_phase(materials.trajectory, phase, assessed, allow_freeze=freeze_trajectory)
    trajectory = _trajectory_outcomes(trajectory_value, bundle, result, rules)
    math_values = _probability_phase(materials.probability, bundle, prepared, original, round_receipt,
                                     phase, assessed, allow_freeze=freeze_trajectory)
    probability = _probability_outcome(materials.probability, bundle, prepared, round_receipt, result, rules, reference, math_values)
    trigger_id, trigger_hash, decision_id, decision_hash = materials.snapshots
    pit = materials.pit
    if pit is None:
        raise ValueError("research_policy_pit_originals_unavailable")
    accepted = [e for e in round_receipt.evidence if e.quality == "accepted"]
    membership = citation_membership_v2.check_citation_membership_v2(
        result, accepted_evidence_ids=[e.evidence_id for e in accepted],
        original_input_ids=[e.evidence_id for e in bundle.request.input_evidence],
    )
    refs = [dict(asset_kind="evidence", asset_id=e.evidence_id,
                 payload_hash=recipe_hash(e.model_dump(mode="json"))) for e in accepted]
    if len(refs) > 256:
        raise ValueError("research_policy_evidence_reference_limit")
    input_hash = recipe_hash({
        "graph_ref": graph_ref.model_dump(mode="json"),
        "round_ref": original.receipt.last_admission_ref.model_dump(mode="json"),
        "bundle_identity": bundle_identity.model_dump(mode="json"),
        "binding": binding.model_dump(mode="json"),
        "accepted_evidence": [e.model_dump(mode="json") for e in accepted],
        "original_input_ids": [e.evidence_id for e in bundle.request.input_evidence],
        "membership": membership.model_dump(mode="json"),
        "pit_input_hash": pit.input_hash,
        "deterministic_input_hashes": {name: item.input_hash for name, item in deterministic.items()},
        "trajectory_input_hashes": {name: item.input_hash for name, item in trajectory.items()},
        "probability_input_hash": probability.input_hash,
        "result_payload_hash": recipe_hash(result.model_dump(mode="json")),
    })
    checks = _policy_rule_checks(rules, membership, refs, input_hash, pit, reference, assessed,
                                 deterministic, trajectory, probability)
    body = dict(
        schema_version="research-policy-check.v1", receipt_id="pending", full_identity_hash="0" * 64,
        method_hash=prepared.method.method_hash, run_id=run_id, event_id=bundle.request.event_id,
        phase=phase, binding=binding, trigger_snapshot_id=trigger_id,
        trigger_snapshot_hash=trigger_hash, decision_snapshot_id=decision_id,
        decision_snapshot_hash=decision_hash, reference_at=reference,
        result_content_hash=research_result_content_hash(result),
        result_payload_hash=recipe_hash(result.model_dump(mode="json")), input_set_hash=input_hash,
        checks=checks, all_applicable_blocking_rules_passed=research_policy_registry.blocking_checks_passed(checks), assessed_at=assessed,
        assessment_state="executed", checker_version=binding.checker_version,
        checker_code_hash=binding.checker_code_hash,
    )
    receipt = ResearchPolicyCheckReceipt.model_validate(body)
    identity_hash = policy_receipt_identity_hash(receipt)
    return ResearchPolicyCheckReceipt.model_validate({
        **body, "full_identity_hash": identity_hash, "receipt_id": policy_receipt_id(identity_hash),
    })




def _assessment(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, phase: Phase, assessed_at: datetime,
    expected_lease_owner: str | None, expected_artifact_id: str | None = None,
    writer: bool = False, freeze_trajectory: bool = False,
) -> ResearchPolicyCheckReceipt | None:
    if not _has_read_snapshot(session):
        return _assessment_without_snapshot(
            session, run_id=run_id, graph_ref=graph_ref, result=result, phase=phase,
            assessed_at=assessed_at, expected_lease_owner=expected_lease_owner,
            expected_artifact_id=expected_artifact_id, writer=writer,
        )
    with _policy_materials(session, run_id=run_id, graph_ref=graph_ref,
                           expected_lease_owner=expected_lease_owner,
                           expected_artifact_id=expected_artifact_id, writer=writer) as materials:
        receipt = _assessment_from_materials(materials, run_id=run_id, graph_ref=graph_ref,
            result=result, phase=phase, assessed_at=assessed_at, freeze_trajectory=freeze_trajectory)
        snapshot = _trajectory_for_phase(materials.trajectory, phase, assessed_at,
                                         allow_freeze=freeze_trajectory) if receipt is not None else None
        math_values = _probability_phase(materials.probability, materials.bundle, materials.prepared,
            materials.original, materials.round_receipt, phase, assessed_at, allow_freeze=freeze_trajectory) if receipt is not None else None
    if freeze_trajectory and snapshot is not None:
        _trajectory_store(session, snapshot)
    if freeze_trajectory and math_values is not None:
        research_probability_binding.store_probability_values_in_session(session, math_values)
    return receipt


def _authenticate_from_materials(
    session: Session, materials: _PolicyMaterials, *, run_id: str,
    graph_ref: GraphFinalProjectionRef, result: ResearchSessionResult, references: object,
) -> list[ResearchPolicyCheckReceipt]:
    if materials.binding is None:
        if references is not None:
            raise ValueError("research_policy_legacy_phase_upgrade_forbidden")
        return []
    if not isinstance(references, dict) or set(references) != {"finalization", "commit"}:
        raise ValueError("research_policy_publication_phases_missing")
    receipts = []
    for phase in ("finalization", "commit"):
        ref = references[phase]
        if not isinstance(ref, dict) or set(ref) != {"receipt_id", "payload_hash"}:
            raise ValueError("research_policy_phase_reference_invalid")
        saved = read_policy_check_receipt(session, ref["receipt_id"])
        if saved is None or saved.phase != phase or saved.run_id != run_id:
            raise ValueError("research_policy_phase_reference_invalid")
        if policy_phase_reference(session, saved) != ref:
            raise ValueError("research_policy_phase_reference_invalid")
        expected = _assessment_from_materials(
            materials, run_id=run_id, graph_ref=graph_ref,
            result=materials.original.result if phase == "finalization" else result,
            phase=phase, assessed_at=saved.assessed_at,
        )
        if expected != saved:
            raise ValueError("research_policy_executed_receipt_mismatch")
        receipts.append(saved)
    return receipts


@contextmanager
def _publication_policy_scope(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    expected_lease_owner: str | None,
) -> Generator[tuple[Any, Callable[..., list[ResearchPolicyCheckReceipt]]]]:
    """The publication reader owns the scope; no caller supplies a Graph proof."""
    if not _has_read_snapshot(session):
        original = read_graph_final_in_session(
            session, run_id=run_id, reference=graph_ref,
            expected_lease_owner=expected_lease_owner, require_current_permission=False,
        )
        def authenticate(*, result: ResearchSessionResult, references: object) -> list[ResearchPolicyCheckReceipt]:
            return _authenticate_without_snapshot(session, run_id=run_id, graph_ref=graph_ref,
                                                   result=result, references=references)
        yield original, authenticate
        return
    with _policy_materials(session, run_id=run_id, graph_ref=graph_ref,
                           expected_lease_owner=expected_lease_owner, read_legacy_graph=True) as materials:
        def authenticate(*, result: ResearchSessionResult, references: object) -> list[ResearchPolicyCheckReceipt]:
            return _authenticate_from_materials(session, materials, run_id=run_id,
                                                graph_ref=graph_ref, result=result, references=references)
        yield materials.original, authenticate


def authenticate_publication_policy_in_session(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, references: object,
    expected_lease_owner: str | None = None, expected_artifact_id: str | None = None,
    writer: bool = False,
) -> list[ResearchPolicyCheckReceipt]:
    """Both phases share only fresh originals in this exact physical transaction."""
    if not _has_read_snapshot(session):
        return _authenticate_without_snapshot(
            session, run_id=run_id, graph_ref=graph_ref, result=result, references=references,
            expected_lease_owner=expected_lease_owner, expected_artifact_id=expected_artifact_id, writer=writer,
        )
    with _policy_materials(session, run_id=run_id, graph_ref=graph_ref,
                           expected_lease_owner=expected_lease_owner,
                           expected_artifact_id=expected_artifact_id, writer=writer) as materials:
        return _authenticate_from_materials(session, materials, run_id=run_id,
                                            graph_ref=graph_ref, result=result, references=references)


def _prepare_commit_policy_in_session(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult, assessed_at: datetime, expected_lease_owner: str,
) -> tuple[ResearchPolicyCheckReceipt | None, ResearchPolicyCheckReceipt | None]:
    """Fresh writer scope for both phases; insert only after reuse is closed."""
    with _policy_materials(session, run_id=run_id, graph_ref=graph_ref,
                           expected_lease_owner=expected_lease_owner, writer=True) as materials:
        if materials.binding is None:
            return None, None
        finalization = _assessment_from_materials(
            materials, run_id=run_id, graph_ref=graph_ref, result=materials.original.result,
            phase="finalization", assessed_at=materials.original.receipt.recorded_at,
        )
        if finalization is None or read_policy_check_receipt(session, finalization.receipt_id) != finalization:
            raise ValueError("research_policy_finalization_receipt_invalid")
        commit = _assessment_from_materials(materials, run_id=run_id, graph_ref=graph_ref,
            result=result, phase="commit", assessed_at=assessed_at, freeze_trajectory=True)
        snapshot = _trajectory_for_phase(materials.trajectory, "commit", assessed_at,
                                         allow_freeze=True) if commit is not None else None
        math_values = _probability_phase(materials.probability, materials.bundle, materials.prepared,
            materials.original, materials.round_receipt, "commit", assessed_at, allow_freeze=True) if commit is not None else None
    if snapshot is not None:
        _trajectory_store(session, snapshot)
    if math_values is not None:
        research_probability_binding.store_probability_values_in_session(session, math_values)
    return finalization, store_policy_check_receipt(session, commit) if commit is not None else None


def _prepare_commit_policy_inputs_in_session(
    session: Session, *, run_id: str, graph_ref: GraphFinalProjectionRef,
    expected_lease_owner: str,
) -> _PreparedCommitPolicy:
    """Full authentication in Commit's read-only PreparedOriginals snapshot.

    The private return value is consumed only after that same preparation's
    source/readset proof and current writer owner/link fence succeed. Public
    policy readers continue to authenticate originals independently.
    """
    with _policy_materials(
        session, run_id=run_id, graph_ref=graph_ref, expected_lease_owner=expected_lease_owner
    ) as materials:
        finalization = None
        if materials.binding is not None:
            finalization = _assessment_from_materials(
                materials,
                run_id=run_id,
                graph_ref=graph_ref,
                result=materials.original.result,
                phase="finalization",
                assessed_at=materials.original.receipt.recorded_at,
            )
            if (
                finalization is None
                or read_policy_check_receipt(session, finalization.receipt_id) != finalization
            ):
                raise ValueError("research_policy_finalization_receipt_invalid")
        inputs = _PolicyInputs(
            **{field.name: getattr(materials, field.name) for field in fields(_PolicyInputs)}
        )
        return _PreparedCommitPolicy(inputs, finalization)


def _commit_policy_values(
    prepared: _PreparedCommitPolicy,
    *,
    run_id: str,
    graph_ref: GraphFinalProjectionRef,
    result: ResearchSessionResult,
    assessed_at: datetime,
) -> _CommitPolicyValues:
    """Pure fresh-time phase calculation after the parent readset was verified."""
    materials = prepared.inputs
    commit = _assessment_from_inputs(
        materials,
        run_id=run_id,
        graph_ref=graph_ref,
        result=result,
        phase="commit",
        assessed_at=assessed_at,
        freeze_trajectory=True,
    )
    if commit is None:
        return _CommitPolicyValues(prepared.finalization, None, None, None)
    trajectory = _trajectory_for_phase(
        materials.trajectory, "commit", assessed_at, allow_freeze=True
    )
    probability = _probability_phase(
        materials.probability,
        materials.bundle,
        materials.prepared,
        materials.original,
        materials.round_receipt,
        "commit",
        assessed_at,
        allow_freeze=True,
    )
    return _CommitPolicyValues(prepared.finalization, commit, trajectory, probability)


def _store_commit_policy_values_in_session(session: Session, values: _CommitPolicyValues) -> None:
    _require_loaded_callables()
    if values.trajectory is not None:
        _trajectory_store(session, values.trajectory)
    if values.probability is not None:
        research_probability_binding.store_probability_values_in_session(
            session, values.probability
        )
    if values.commit is not None:
        store_policy_check_receipt(session, values.commit)


def _verify_commit_policy_values_in_session(session: Session, values: _CommitPolicyValues) -> None:
    """Read back this writer's exact outputs; parent rechecks all input originals."""
    _require_loaded_callables()
    for expected in (values.finalization, values.commit):
        if (
            expected is not None
            and read_policy_check_receipt(session, expected.receipt_id) != expected
        ):
            raise ValueError("research_policy_executed_receipt_mismatch")
    value = values.trajectory
    if value is not None and _trajectory_read_saved(
        session,
        run_id=value.run_id,
        graph_ref_json=value.graph_ref_json,
        method_hash=value.method_hash,
        phase=value.phase,
    ) != _trajectory_encoded(value):
        raise ValueError("research_policy_trajectory_original_conflict")
    probability = values.probability
    if probability is not None:
        references = {
            phase: research_probability_binding.probability_phase_reference(
                session,
                run_id=probability.run_id,
                phase=phase,
            )
            for phase in ("finalization", "commit")
        }
        if (
            research_probability_binding.publication_values_in_session(
                session,
                run_id=probability.run_id,
                references=references,
            )
            != probability
        ):
            raise ValueError("research_probability_phase_original_changed")


# Freeze loaded dispatcher objects as well as source bytes. A changed in-memory
# callable cannot reuse the original implementation identity to mint a pass.
_LOADED_MEMBERSHIP = citation_membership_v2.check_citation_membership_v2
_LOADED_MEMBERSHIP_CODE = _LOADED_MEMBERSHIP.__code__
_LOADED_PURE_TARGETS = tuple((module, name, function, function.__code__) for module, name, function in (
    (research_policy_registry, "blocking_checks_passed", research_policy_registry.blocking_checks_passed),
    (kernel_gate, "evaluate_gate", evaluate_gate),
    (horizon_contract, "validate_horizon_contract", validate_horizon_contract),
    (sufficiency, "assess_evidence_sufficiency", assess_evidence_sufficiency),
    (research_evidence, "_yahoo_daily_freshness", _yahoo_daily_freshness),
    (research_evidence, "_source_public_time_unknown_v2", _source_public_time_unknown_v2),
    *((research_probability_binding, name, getattr(research_probability_binding, name)) for name in (
        "_require_loaded", "read_probability_materials_in_session", "probability_outcome",
        "probability_phase", "store_probability_values_in_session",
    )),
    *((research_loop, name, getattr(research_loop, name)) for name in (
        "assess_research_loop", "review_challenge_fingerprint", "_raw_inquiries",
        "_synthesis_problem", "_unhandled_review", "_has_route", "_unique",
    )),
))
_LOADED_EXECUTION = tuple((name, globals()[name], globals()[name].__code__) for name in (
    "_require_loaded_callables", "citation_execution_identity", "_binding", "_binding_originals", "_claim_body",
    "_pit_materials", "_pit_unavailable", "_pit_fingerprinted_row", "_pit_trigger_originals", "_pit_originals",
    "_pit_instant", "_pit_order", "_pit_verdict", "_policy_rule_checks", "_columns", "observation_sha", "as_utc",
    "_trajectory_json", "_trajectory_row", "_trajectory_role", "_trajectory_encoded",
    "_trajectory_read_saved", "_trajectory_with_usage_rows", "_trajectory_store",
    "_trajectory_native_calls", "_trajectory_anchor",
    "read_trace_projection",
    "_trajectory_call", "_trajectory_originals", "_trajectory_monotonic", "_trajectory_for_phase",
    "_gap_trajectory_outcome", "_bounded_stop_trajectory_outcome",
    "_trajectory_outcomes", "assess_research_loop", "read_mapped_observation", "read_transport_observation",
    "_probability_outcome", "_probability_phase",
    "_deterministic_outcome", "_deterministic_unavailable", "_coverage_outcome", "_structure_outcome",
    "_horizon_outcome", "_cap_outcome", "_deterministic_outcomes",
    "evaluate_gate", "validate_horizon_contract", "assess_evidence_sufficiency", "_yahoo_daily_freshness",
    "_source_public_time_unknown_v2",
    "_assessment", "_assessment_without_snapshot", "_assessment_from_materials",
    "_assessment_from_inputs",
    "_prepare_commit_policy_inputs_in_session", "_commit_policy_values",
    "_store_commit_policy_values_in_session", "_verify_commit_policy_values_in_session",
    "_has_read_snapshot", "_policy_materials", "_authenticate_from_materials",
    "_authenticate_without_snapshot", "_publication_policy_scope", "_prepare_commit_policy_in_session",
    "_local_original_read",
    "read_bundle_identity_in_session", "read_prepared_execution_in_session",
    "read_graph_final_in_session", "read_graph_round_in_session", "_read_round_receipt",
    "verify_graph_citation_membership_in_session", "graph_recipe_manifest_v2",
    "recipe_hash", "read_policy_check_receipt", "store_policy_check_receipt",
    "policy_receipt_identity_hash", "policy_receipt_id", "research_result_content_hash",
    "execute_citation_policy_in_session", "verify_finalization_policy_in_session",
    "policy_phase_reference", "authenticate_publication_policy_in_session",
))

_LOADED_WRAPPED = tuple((name, globals()[name].__wrapped__, globals()[name].__wrapped__.__code__)
                        for name in ("_policy_materials", "_publication_policy_scope", "_local_original_read"))
_LOADED_POLICY_TYPES = tuple(
    (name, globals()[name], globals()[name].__init__, globals()[name].__init__.__code__)
    for name in (
        "_PolicyInputs", "_PolicyMaterials", "_PreparedCommitPolicy", "_CommitPolicyValues"
    )
)
