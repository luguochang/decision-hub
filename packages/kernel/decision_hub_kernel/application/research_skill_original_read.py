"""Read admitted same-Run spot originals; never issue or replay a Gateway call."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityResult

from ..persistence.db import (
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    RunRoleOutputRecord,
    as_utc,
)
from ..ports.market_path import aware_utc, canonical_hash, spot_scope
from . import research_skill_compiler as compiler
from .fact_store import _fact_model
from .market_component_functions import FeatureComponentInput
from .provider_input_receipts import row_reference, strict_json
from .research_evidence import _evidence_model
from .research_policy_checker import _trajectory_call
from .research_skill_gateway import MATCH_FIELDS, SkillGatewayAttemptFailure, _prepared_gateway
from .research_skill_market_input import SkillMarketInput
from .research_skill_package import decoded, encoded, sha
from .spot_evidence_captures import spot_evidence_captures_in_session

PROTOCOL = "same-run-spot-original-read.v1"


@dataclass(frozen=True)
class FrozenSpotOriginal:
    manifest_json: bytes
    workflow_input_json: bytes
    input_json: bytes
    output_json: bytes
    market_refs: tuple[tuple[str, str], ...]


def _matches(args, node, expected):
    return (
        args.get("capability_id") == node["capability_id"]
        and all(args.get(key) == expected[key] for key in MATCH_FIELDS)
        and all(
            args.get(key) in (None, [])
            for key in (
                "event_id",
                "event_at",
                "window_start_at",
                "window_end_at",
                "requested_event_offsets",
                "evidence_ref",
                "content_ref",
            )
        )
    )


def read_spot_original_in_session(
    use,
    market_store,
    session,
    *,
    selection,
    source_generation,
    cutoff_at,
    prepared_reader=_prepared_gateway,
    expected_manifest=None,
):
    """Authenticate source data independently of any old skill stage.

    Ordering is the server-recorded native emission order; same-dispatch ties
    use its original block index, and ambiguous cross-dispatch ties fail closed.
    No price/outcome-dependent choice and no old loaded receipt requirement.
    Earlier failed attempts remain explicit when a later authentic success exists.
    """
    cutoff = aware_utc(cutoff_at)
    run_id = selection["run_id"]
    prepared, anchor = prepared_reader(session, run_id, source_generation)
    request = prepared.execution.request
    if (
        prepared.execution.generation != source_generation
        or prepared.link.dsh_session_id is None
        or request.run_id != run_id
        or source_generation < selection["generation"]
    ):
        raise ValueError("skill_original_source_identity_invalid")
    raw_plan = use.packages.archive.read(selection["compiled_plan_ref"])
    if sha(raw_plan) != selection["compiled_plan_hash"]:
        raise ValueError("skill_original_plan_changed")
    compiled = compiler.CompiledWorkflow(raw_plan.decode(), selection["compiled_plan_hash"])
    node = next(
        (item for item in compiled.read()["workflow"]["nodes"] if item["id"] == "spot_evidence"),
        None,
    )
    if (
        node is None
        or node["op"] != "evidence.acquire"
        or node["capability_id"] != "spot_market_facts"
    ):
        raise ValueError("skill_original_node_not_supported")
    # Gateway intent for this node is constant. Bind using the original selection
    # cutoff only to resolve declared inputs, not to select fresh market data.
    if node["depends_on"] or any(
        binding["from"] != "literal" for binding in node["inputs"].values()
    ):
        raise ValueError("skill_original_query_requires_unavailable_parent")
    expected = {key: binding["value"] for key, binding in node["inputs"].items()}
    use.packages.contracts.check("gateway_intent_v1", expected)
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.schema_version == "research-provider-tool-emission.v1",
        )
        .order_by(RunRoleOutputRecord.created_at, RunRoleOutputRecord.output_id)
        .limit(513)
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) > 512:
        raise ValueError("skill_original_emission_census_limit")
    candidates = []
    for row in rows:
        meta = strict_json(row.payload_json).get("submit")
        if (
            not isinstance(meta, dict)
            or meta.get("tool_name") != "decision_hub_research"
            or meta.get("generation") != source_generation
            or meta.get("root_session_id") != prepared.link.dsh_session_id
            or meta.get("execution_session_id") != prepared.link.dsh_session_id
        ):
            continue
        pair = use.emission_reader(
            session,
            run_id=run_id,
            generation=source_generation,
            root_session_id=prepared.link.dsh_session_id,
            execution_session_id=prepared.link.dsh_session_id,
            call_id=meta["call_id"],
        )
        if pair is None:
            raise ValueError("skill_original_emission_missing")
        emission, reference = pair
        args = strict_json(emission.submit.raw_arguments_json)
        if not _matches(args, node, expected):
            continue
        known = aware_utc(emission.known_at)
        if known > cutoff:
            continue
        reservation = session.get(
            ResearchToolCallReservationRecord, (run_id, meta["call_id"]), populate_existing=True
        )
        candidates.append(
            (
                known,
                emission.submit.dispatch_id,
                getattr(emission.submit, "block_index", 0),
                emission,
                reference,
                args,
                reservation,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    if not candidates:
        raise ValueError("skill_original_source_missing")
    if any(
        left[0] == right[0] and (left[1] != right[1] or left[2] == right[2])
        for left, right in zip(candidates, candidates[1:], strict=False)
    ):
        raise ValueError("skill_original_source_order_ambiguous")
    attempts, chosen = [], None
    for item in candidates:
        reservation = item[6]
        finished = as_utc(reservation.completed_at) if reservation else None
        historical = finished is not None and finished <= cutoff
        attempt = dict(
            call_id=item[3].submit.call_id,
            provider_emission_ref=item[4].model_dump(mode="json"),
            status=reservation.status if historical else "not_completed_by_cutoff",
            error_json=reservation.error_json if historical else None,
        )
        attempts.append(attempt)
        if not historical:
            attempt.update(eligibility="unavailable", reason="source_not_completed_by_cutoff")
            continue
        valid, reason = _read_candidate(session, item, prepared, anchor, selection, cutoff)
        market = None
        if valid is not None:
            try:
                market = read_source_market_in_session(
                    market_store,
                    session,
                    run_id=run_id,
                    scope_id=selection["scope_id"],
                    result=valid[0],
                    completed=valid[3],
                )
            except ValueError as error:
                if str(error) != "research_spot_capture_window_unavailable":
                    raise
                valid, reason = None, str(error)
        attempt["eligibility"] = "eligible" if valid else "unavailable"
        attempt["reason"] = reason
        if valid is not None:
            chosen = (item, valid, market)
            break
    if chosen is None:
        raise SkillGatewayAttemptFailure("skill_original_no_eligible_source", attempts)
    item, valid, market = chosen
    result, originals, call, completed, source_known = valid
    _, _, _, emission, emission_ref, _, reservation = item
    call_id = emission.submit.call_id
    assert market is not None
    workflow_input = decoded(market.workflow_input_json)
    bound = compiler.bind_node_inputs(compiled, "spot_evidence", workflow_input, {})
    if decoded(bound.input_json) != expected:
        raise ValueError("skill_original_query_depends_on_new_input")
    output = result.model_dump(mode="json")
    compiler.validate_node_output(compiled, "spot_evidence", output)
    adapter = next(row for row in originals if row.output_id == call.adapter_output_id)
    adapter_body = decoded(adapter.payload_json)
    manifest = dict(
        protocol=PROTOCOL,
        run_id=run_id,
        source_generation=source_generation,
        source_session_id=prepared.link.dsh_session_id,
        call_id=call_id,
        provider_emission_ref=emission_ref.model_dump(mode="json"),
        adapter_ref=row_reference(adapter).model_dump(mode="json"),
        query_hash=reservation.query_hash,
        adapter_result_hash=adapter_body["adapter_result_hash"],
        output_hash=canonical_hash(output),
        source_completed_at=completed.isoformat(),
        source_known_through=max(source_known, market.known_through).isoformat(),
        acquisition_cutoff_at=cutoff.isoformat(),
        workflow_input_hash=sha(market.workflow_input_json),
        market_originals=[list(pair) for pair in market.originals],
        attempts=attempts,
    )
    if expected_manifest is not None and manifest != expected_manifest:
        raise ValueError("skill_original_frozen_source_changed")
    return FrozenSpotOriginal(
        encoded(manifest),
        market.workflow_input_json,
        bound.input_json.encode(),
        encoded(output),
        market.originals,
    )


def read_source_market_in_session(store, session, *, run_id, scope_id, result, completed):
    """Exact originally-used path, including its one-minute feature endpoint."""
    accepted = [item for item in result.evidence_candidates if item.quality == "accepted"]
    if len(accepted) != 1:
        raise ValueError("skill_original_source_evidence_ambiguous")
    row = session.get(ResearchEvidenceRecord, accepted[0].evidence_id, populate_existing=True)
    captures = spot_evidence_captures_in_session(session, run_id=run_id, evidence=row, store=store)
    if not captures:
        raise ValueError("skill_original_source_path_required")
    summary = strict_json(row.excerpt)
    scope = spot_scope(summary["instrument"])
    known = aware_utc(datetime.fromisoformat(summary["source_known_at"]))
    start, end = (aware_utc(datetime.fromisoformat(value)) for value in summary["requested_window"])
    if scope.scope_id != scope_id or known > completed:
        raise ValueError("skill_original_source_path_identity_invalid")
    originals = []
    bars = store.load_in_session(
        session,
        scope,
        start_at=start - timedelta(minutes=1),
        end_at=min(end, known.replace(second=0, microsecond=0)),
        as_of=known,
        on_original=lambda ref, digest: originals.append((ref, digest)),
    )
    # The preceding existing lineage reader authenticated the same full window
    # hashes and latest receipt; these bars are neither latest-at-now nor picked
    # from another Run's subsequent revisions.
    payload = FeatureComponentInput(
        scope=scope, candles=bars, as_of=completed, stale_after_seconds=180
    )
    return SkillMarketInput(
        encoded({"feature_input": payload.model_dump(mode="json")}),
        tuple(sorted(set(originals))),
        known,
    )


def _read_candidate(session, item, prepared, anchor, selection, cutoff):
    run_id = selection["run_id"]
    known, _, _, emission, emission_ref, args, reservation = item
    if reservation is None or reservation.status != "completed":
        return None, "source_" + (reservation.status if reservation else "no_reservation")
    call_id = emission.submit.call_id
    # These hash arguments annotate the trajectory result only. Authority comes
    # from its actual timing/admission/adapter originals, not a fabricated native
    # ToolResult proof. Current delivery is verified by the consumed reader.
    call, originals, problem = _trajectory_call(
        session,
        reservation,
        call_id,
        args,
        sha(emission.submit.raw_arguments_json.encode()),
        None,
        prepared,
        anchor,
    )
    if problem or call is None or call.adapter_output_id is None:
        raise ValueError("skill_original_admission_unavailable:" + str(problem))
    completed = as_utc(reservation.completed_at)
    if completed is None or not known <= completed <= cutoff:
        return None, "source_completion_outside_cutoff"
    result = ResearchCapabilityResult.model_validate_json(reservation.result_json)
    if result.request_id != call_id:
        raise ValueError("skill_original_result_identity_changed")
    accepted, times = set(), []
    for item in result.evidence_candidates:
        row = session.get(ResearchEvidenceRecord, item.evidence_id, populate_existing=True)
        if row is None or row.run_id != run_id or _evidence_model(row) != item:
            raise ValueError("skill_original_evidence_changed")
        if item.quality == "accepted":
            admitted_at = as_utc(row.accepted_at)
            if admitted_at is None or admitted_at > completed:
                raise ValueError("skill_original_admission_clock_invalid")
            if (
                item.research_session_id != prepared.link.dsh_session_id
                or item.round != prepared.execution.generation
                or item.tool_call_id != call_id
            ):
                raise ValueError("skill_original_evidence_identity_changed")
            accepted.add(item.evidence_id)
            times.append(aware_utc(item.received_at))
            times.append(admitted_at)
    facts = []
    for item in result.facts or ():
        row = session.get(ResearchFactRecord, item.fact_id, populate_existing=True)
        if row is None or row.run_id != run_id or _fact_model(row) != item:
            raise ValueError("skill_original_fact_changed")
        if item.quality == "accepted":
            admitted_at = as_utc(row.accepted_at)
            if admitted_at is None or admitted_at > completed:
                raise ValueError("skill_original_admission_clock_invalid")
            if (
                item.evidence_id not in accepted
                or item.instrument != selection["scope_id"].split(":")[3]
                or item.venue != "okx"
            ):
                raise ValueError("skill_original_fact_scope_changed")
            facts.append(item.fact_id)
            times.extend([aware_utc(item.received_at), aware_utc(item.observed_at)])
            times.append(admitted_at)
    if not accepted or not facts or max(times) > completed:
        return None, "source_accepted_facts_unavailable"
    return (result, originals, call, completed, max(times)), None
