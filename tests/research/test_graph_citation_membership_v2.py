"""Offline Factory/typed MCP/Runtime/Kernel citation regression; no Provider or HTTP.

The negative case has sufficient BTC snapshot hard coverage. Its sole defect is
a top-level causal citation of a related, durably retained search locator whose
actual Kernel quality remains candidate. Main/opposite links and every horizon
cite accepted spot Evidence. This must fail by behavior on the old v1 recipe,
not because a new checker or schema cannot be imported.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    FactEnvelope,
    ObservationCreate,
    ResearchSessionResult,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import project_round
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    read_mapped_observation,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.decision.citation_membership_v2 import (
    check_citation_membership_v2,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    _completed_result_with_repairable_synthesis,  # pyright: ignore[reportPrivateUsage]
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.evolution.test_research_worker import NOW
from tests.orchestration.test_agentic_research_graph import (
    _candidate as _unit_candidate,  # pyright: ignore[reportPrivateUsage]
)
from tests.orchestration.test_agentic_research_graph import (
    _causal_case,  # pyright: ignore[reportPrivateUsage]
    _horizons,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _result,  # pyright: ignore[reportPrivateUsage]
)
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    MaterialHost,
    _active,  # pyright: ignore[reportPrivateUsage]
    _sql,  # pyright: ignore[reportPrivateUsage]
    _write_observation,  # pyright: ignore[reportPrivateUsage]
    continuation_inputs,
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None and continuation_inputs is not None
SPOT_A = "citation-accepted-spot-a"
SPOT_A2 = "citation-accepted-spot-a2"
HISTORY_A = "citation-accepted-history-a"
LOCATOR_B = "citation-candidate-locator-b"


@pytest.fixture
def snapshot_inputs(tmp_path: Path) -> Generator[PreparedInputs]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'citation.sqlite3'}")
    database.create_all()
    try:
        event_id, _, created = AdmissionService(database, clock=lambda: NOW).admit(
            ObservationCreate(
                text="Current BTC spot price and volume snapshot with daily trend context.",
                source_id="citation-controlled-input",
                source_type=SourceType.transcript,
                observed_at=NOW - timedelta(seconds=2),
                published_at=NOW - timedelta(seconds=3),
                language="en",
                event_hint="crypto_market_snapshot",
            )
        )
        assert created
        run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
        assert created
        root, manifest = _prepared_pack(tmp_path)
        # Keep the real current-snapshot evidence policy, both spot venues,
        # history semantic fields and the derivatives-soft execution gap.
        manifest["execution_budget"]["max_evidence_rounds"] = 1
        manifest["execution_budget"]["max_structured_repairs"] = 0
        (root / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        request = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode="replay",
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        ).build(event_id, run_id)
        digest = research_request_hash(request)
        session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, digest)
        submit = DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=digest,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=request.deadline_at,
            max_tool_calls=request.execution_budget.max_tool_calls,
            model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1,
        )
        yield PreparedInputs(
            database,
            request,
            submit,
            DshUpstreamIdentity(
                source_commit="1" * 40,
                source_version="0.1.0",
                package_versions={},
                plugin_build_hash="2" * 64,
            ),
            "Unused; actual Runtime builds the prompt from the Factory originals",
            datetime.now(UTC),
        )
    finally:
        database.engine.dispose()


def _candidate(
    fixture: PreparedInputs,
    payload: DshSessionSubmit,
    *,
    evidence_id: str,
    requirement_id: str,
    source_id: str,
    authority: str = "exchange",
) -> EvidenceCandidate:
    cutoff = fixture.request.pit_cutoff_at
    published = cutoff - timedelta(seconds=2)
    url = f"https://controlled.invalid/{source_id}"
    excerpt = f"Offline controlled material for {requirement_id}: {evidence_id}."
    return EvidenceCandidate.model_validate(
        {
            "evidence_id": evidence_id,
            "requirement_id": requirement_id,
            "kind": "market",
            "authority": authority,
            "source_id": source_id,
            "source_url": url,
            "published_at": published,
            "observed_at": cutoff - timedelta(seconds=1),
            "received_at": cutoff,
            "content_hash": research_evidence_content_hash(
                requirement_id=requirement_id,
                kind="market",
                authority=authority,
                source_id=source_id,
                source_url=url,
                published_at=published,
                excerpt=excerpt,
                structured_payload_ref=None,
            ),
            "excerpt": excerpt,
            "structured_payload_ref": None,
            "tool_call_id": "call-partial-1",
            "research_session_id": payload.deterministic_session_id,
            "round": 1,
            "quality": "candidate",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )


def _fact(
    evidence: EvidenceCandidate,
    *,
    field: str,
    value: float,
    unit: str,
    venue: str,
    history: bool = False,
) -> FactEnvelope:
    semantics: dict[str, Any] = {
        "requirement_id": evidence.requirement_id,
        "metric_family": "crypto.spot.history" if history else "crypto.spot",
        "field": field,
        "instrument": "BTC-USDT",
        "venue": venue,
        "value": value,
        "unit": unit,
        "window_start_at": None,
        "window_end_at": None,
        "event_offset": None,
        "published_at": evidence.published_at,
        "source_id": evidence.source_id,
        "independence_group": evidence.source_id,
        "delay_class": "historical" if history else "realtime",
        "payload_schema_ref": "citation-controlled-market.v1",
        "attributes": {"offline_fixture": True},
    }
    digest = research_fact_payload_hash(**semantics)
    return FactEnvelope.model_validate(
        {
            **semantics,
            "schema_version": "fact-envelope.v1",
            "fact_id": research_fact_instance_id(evidence_id=evidence.evidence_id,
                                                payload_hash=digest),
            "evidence_id": evidence.evidence_id,
            "observed_at": evidence.observed_at,
            "received_at": evidence.received_at,
            "quality": "candidate",
            "payload_hash": digest,
        }
    )


class SnapshotCitationHost(MaterialHost):
    """Paired typed MCP original; Runtime and Kernel admission remain genuine.

    This creates no physical Gateway acquisition receipt or Provider completion.
    The source timestamps, complete hard-policy facts and related locator are
    controlled offline originals, rather than mocked admission/projection checks.
    """

    def __init__(self, fixture: PreparedInputs, *, top_ref: str) -> None:
        super().__init__(fixture)
        self.top_ref = top_ref
        self.transcript_candidates: list[EvidenceCandidate] = []
        self.transcript_synthesis: dict[str, Any] | None = None

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        self.result_calls += 1
        response = _completed_result_with_repairable_synthesis(payload)
        events = json.loads(response.events_json)[:2]
        capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
            "structuredContent"
        ]
        candidates = [
            _candidate(self.fixture, payload, evidence_id=eid,
                       requirement_id="crypto_spot_confirmation", source_id=source)
            for eid, source in ((SPOT_A, "controlled-binance"), (SPOT_A2, "controlled-okx"))
        ]
        candidates.append(_candidate(
            self.fixture, payload, evidence_id=HISTORY_A,
            requirement_id="market_regime_context", source_id="controlled-history",
        ))
        candidates.append(_candidate(
            self.fixture, payload, evidence_id=LOCATOR_B,
            requirement_id="market_narrative", source_id="controlled-search",
            authority="search_derived",
        ))
        facts = [
            _fact(evidence, field=field, value=value, unit=unit, venue=venue)
            for evidence, venue in zip(candidates[:2], ("binance", "okx"), strict=True)
            for field, value, unit in (("price", 60000.0, "usdt"), ("volume", 1000.0, "btc"))
        ]
        history_values = {
            "period_close": (60000.0, "usdt"), "sma_20": (59000.0, "usdt"),
            "sma_60": (58000.0, "usdt"), "return_20_pct": (2.0, "percent"),
            "realized_vol_20_pct": (3.0, "percent"), "volume_ratio_20": (1.2, "ratio"),
            "rolling_high_20": (61000.0, "usdt"), "rolling_low_20": (57000.0, "usdt"),
            "row_count": (60.0, "count"),
        }
        facts.extend(
            _fact(candidates[2], field=field, value=value, unit=unit,
                  venue="binance", history=True)
            for field, (value, unit) in history_values.items()
        )
        # Keep the controlled transcript stable even when SQLite accepted_at
        # values share a clock tick. The unchanged v1 reader orders such rows
        # by evidence_id; the material, facts and sole citation defect are intact.
        capability["evidence_candidates"] = [item.model_dump(mode="json")
                                             for item in sorted(candidates,
                                                                key=lambda item: item.evidence_id)]
        capability["facts"] = [item.model_dump(mode="json") for item in facts]
        capability["request_id"] = self.fixture.request.request_id
        capability["completed_at"] = self.fixture.request.pit_cutoff_at.isoformat()
        causal = _causal_case(thesis="Controlled BTC snapshot supports a daily upward bias.",
                              unresolved_questions=[], evidence_id=SPOT_A)
        causal["evidence_refs"] = [self.top_ref]
        horizons = _horizons(SPOT_A)
        for index, item in enumerate(horizons):
            item["action"] = "long"
            item["next_review_at"] = (
                self.fixture.request.pit_cutoff_at + timedelta(minutes=10 + index)
            ).isoformat()
            item["expires_at"] = (
                self.fixture.request.pit_cutoff_at + timedelta(hours=4 + index)
            ).isoformat()
        synthesis = {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": self.fixture.request.request_id,
            "causal_case": causal,
            "horizons": horizons,
        }
        self.transcript_candidates = candidates
        self.transcript_synthesis = synthesis
        final_response, encoded = json.dumps(synthesis), json.dumps(events)
        return response.model_copy(update={
            "events_json": encoded, "final_response": final_response, "last_seq": len(events),
            "started_at": self.fixture.request.pit_cutoff_at,
            "finished_at": self.fixture.request.pit_cutoff_at + timedelta(seconds=2),
            "result_hash": hashlib.sha256((final_response + "\0" + encoded).encode()).hexdigest(),
        })


async def _snapshot_final(
    fixture: PreparedInputs, path: Path, *, top_ref: str,
) -> ResearchSessionResult:
    request = _active(fixture)
    requirements = {item.requirement_id: item for item in request.evidence_requirements}
    assert {key for key, item in requirements.items() if item.importance == "hard"} == {
        "crypto_spot_confirmation", "market_regime_context",
    }
    assert requirements["derivatives_crowding"].importance == "soft"
    assert requirements["crypto_spot_confirmation"].minimum_independent_sources == 2
    host = SnapshotCitationHost(fixture, top_ref=top_ref)
    runtime = DshWebResearchRuntime(DshSessionLinkService(fixture.database), host,
                                   poll_interval_seconds=0.001)
    graph = build_agentic_research_graph(
        runtime, ResearchEvidenceService(fixture.database), expected_lease_owner=OWNER,
    )
    try:
        try:
            state = await graph.ainvoke(initial_research_state(request))
        except ValueError as error:
            # A prerequisite failure is not a citation behavior RED. Capture
            # the actual saved round and pure v1 replay for a precise diagnosis.
            with fixture.database.session() as session:
                row = session.query(RunRoleOutputRecord).filter_by(
                    run_id=request.run_id, role="research_graph_round:1",
                ).one_or_none()
                diagnostic: dict[str, Any] = {"error": str(error), "top_ref": top_ref}
                if row is not None:
                    diagnostic["saved_round_schema"] = row.schema_version
                    diagnostic["saved_round_raw"] = json.loads(row.payload_json)
                    if row.schema_version == "research-graph-round-admission.v1":
                        receipt = binding.ResearchGraphRoundAdmission.model_validate_json(
                            row.payload_json,
                        )
                        mapped = read_mapped_observation(session, receipt.observation_ref)
                        prepared = read_prepared_execution_in_session(
                            session, request.run_id, receipt.native_context.generation,
                        )
                        actual = type(request).model_validate(
                            prepared.execution.request.model_dump(mode="json"),
                        )
                        baseline = initial_research_state(request)
                        admitted = [item for item in receipt.evidence
                                    if item.evidence_id in receipt.admitted_evidence_ids]
                        replayed = {
                            **baseline,
                            **project_round(baseline, request, actual, mapped.result,
                                            receipt.evidence, receipt.facts, admitted),
                        }
                        saved = receipt.projected_state
                        differences = {
                            key: {"saved": saved.get(key), "replayed": replayed.get(key)}
                            for key in sorted(set(saved) | set(replayed))
                            if saved.get(key) != replayed.get(key)
                        }
                        diagnostic.update(
                            baseline=baseline,
                            prepared_request=actual.model_dump(mode="json"),
                            mapped_original=mapped.model_dump(mode="json"),
                            replayed_state=replayed,
                            top_level_differences=differences,
                            writer_admitted_ids=receipt.admitted_evidence_ids,
                            reader_admitted_order=[item.evidence_id for item in admitted],
                        )
            _write_observation(path, "snapshot-citation-prerequisite-failure.json", diagnostic)
            raise
        final_ref = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
        with fixture.database.session() as session:
            final = binding.read_graph_final_in_session(
                session, run_id=request.run_id, reference=final_ref,
                expected_lease_owner=OWNER, require_current_permission=True,
            )
            actual_evidence = ResearchEvidenceService.list_run_evidence_in_session(
                session, request.run_id,
            )
        assert final_ref.row.schema_version == "research-graph-final-projection.v2"
        assert isinstance(final.receipt, binding.ResearchGraphFinalProjectionV2)
        if top_ref == LOCATOR_B:
            assert final.receipt.latest_citation_disposition == "rejected"
            assert final.receipt.citation_disposition == "not_applicable"
            assert final.receipt.selected_synthesis_observation_ref is None
        else:
            assert final.receipt.citation_disposition == "accepted"
            assert final.receipt.latest_citation_disposition == "accepted"
            assert final.receipt.selected_synthesis_observation_ref is not None
        qualities = {item.evidence_id: item.quality for item in actual_evidence}
        assert len(host.submit_calls) == 1 and host.result_calls == 1
        assert qualities[SPOT_A] == qualities[SPOT_A2] == qualities[HISTORY_A] == "accepted"
        assert qualities[LOCATOR_B] == "candidate"
        assert final.result.final_coverage.status == "sufficient"
        assert final.result.final_coverage.hard_coverage_ratio == 1.0
        assert host.transcript_synthesis is not None
        causal = host.transcript_synthesis["causal_case"]
        assert causal["evidence_refs"] == [top_ref]
        assert all(link["evidence_refs"] == [SPOT_A]
                   for chain in ("main_chain", "opposite_chain") for link in causal[chain])
        assert [item["horizon"] for item in host.transcript_synthesis["horizons"]] == [
            "30m", "24h", "72h",
        ]
        assert all(item["evidence_refs"] == [SPOT_A]
                   for item in host.transcript_synthesis["horizons"])
        _write_observation(path, "snapshot-citation-original.json", {
            "fixture_boundary": "offline_factory_mcp_runtime_kernel_graph",
            "provider_executed": False, "physical_gateway_executed": False,
            "top_ref": top_ref,
            "request": request.model_dump(mode="json"),
            "controlled_mcp_evidence": [item.model_dump(mode="json")
                                        for item in host.transcript_candidates],
            "controlled_synthesis": host.transcript_synthesis,
            "kernel_evidence_qualities": qualities,
            "graph_final_ref": final_ref.model_dump(mode="json"),
            "graph_final_receipt": final.receipt.model_dump(mode="json"),
            "final_result": final.result.model_dump(mode="json"),
            "sql": _sql(fixture),
        })
        return final.result
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_snapshot_accepted_top_level_citation_is_publishable(
    snapshot_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    final = await _snapshot_final(snapshot_inputs, tmp_path, top_ref=SPOT_A)
    assert final.status == "completed" and final.stop_reason.code == "sufficient"
    assert final.causal_case is not None and final.causal_case.evidence_refs == [SPOT_A]
    assert [item.horizon for item in final.horizons] == ["30m", "24h", "72h"]


@pytest.mark.asyncio
async def test_snapshot_top_level_candidate_citation_cannot_be_published(
    snapshot_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    final = await _snapshot_final(snapshot_inputs, tmp_path, top_ref=LOCATOR_B)
    # Preconditions above prove candidate B is a related, real retained locator,
    # and hard coverage/other citations pass. Old v1 fails precisely here by
    # publishing B; the v2 safety contract must retain material without it.
    assert final.causal_case is None, (
        "Kernel must not publish a candidate top-level causal citation"
    )
    assert final.horizons == [], "A first rejected synthesis has no selected directional version"


def _membership_result() -> ResearchSessionResult:
    # Pure checker controls use a typed Result only. The actual trust-source
    # and Factory/Runtime/writer boundaries are exercised by the cases above.
    body = _result(_request(), candidates=[], horizons=_horizons(SPOT_A))
    body["causal_case"] = _causal_case(thesis="Controlled membership case.",
                                       unresolved_questions=[], evidence_id=SPOT_A)
    return ResearchSessionResult.model_validate(body)


@pytest.mark.parametrize("location, expected_path", [
    ("top", "causal_case.evidence_refs[0]"),
    ("main", "causal_case.main_chain[0].evidence_refs[0]"),
    ("opposite", "causal_case.opposite_chain[0].evidence_refs[0]"),
    (0, "horizons[0].evidence_refs[0]"),
    (1, "horizons[1].evidence_refs[0]"),
    (2, "horizons[2].evidence_refs[0]"),
])
def test_every_claim_location_requires_membership_and_keeps_original(
    location: str | int, expected_path: str,
) -> None:
    result = _membership_result()
    body = result.model_dump(mode="json")
    if location == "top":
        body["causal_case"]["evidence_refs"] = [LOCATOR_B]
    elif location == "main":
        body["causal_case"]["main_chain"][0]["evidence_refs"] = [LOCATOR_B]
    elif location == "opposite":
        body["causal_case"]["opposite_chain"][0]["evidence_refs"] = [LOCATOR_B]
    else:
        assert isinstance(location, int)
        body["horizons"][location]["evidence_refs"] = [LOCATOR_B]
    changed = ResearchSessionResult.model_validate(body)
    before = changed.model_dump_json()
    check = check_citation_membership_v2(changed, accepted_evidence_ids=[SPOT_A],
                                        original_input_ids=[])
    assert check.disposition == "rejected" and check.rejected_evidence_ids == [LOCATOR_B]
    assert [(entry.path, entry.evidence_id) for entry in check.entries
            if entry.evidence_id == LOCATOR_B] == [(expected_path, LOCATOR_B)]
    assert len(check.entries) == 6
    assert changed.model_dump_json() == before, "A check must not rewrite the frozen model original"


@pytest.mark.parametrize("declared_quality", ["candidate", "stale", "rejected", "accepted"])
def test_result_material_cannot_self_authorize_a_citation(declared_quality: str) -> None:
    body = _membership_result().model_dump(mode="json")
    body["causal_case"]["evidence_refs"] = [LOCATOR_B]
    body["evidence_candidates"] = [
        {**_unit_candidate(evidence_id=LOCATOR_B), "quality": declared_quality},
    ]
    result = ResearchSessionResult.model_validate(body)
    # Even the model's self-declared accepted label cannot replace the writer's
    # actual accepted set. A retained locator is deliberately present here.
    check = check_citation_membership_v2(result, accepted_evidence_ids=[SPOT_A],
                                        original_input_ids=[])
    assert check.disposition == "rejected" and check.rejected_evidence_ids == [LOCATOR_B]


def test_original_input_and_canonical_sets_are_distinct_from_no_synthesis() -> None:
    body = _membership_result().model_dump(mode="json")
    body["causal_case"]["evidence_refs"] = ["original-input-a"]
    result = ResearchSessionResult.model_validate(body)
    original = result.model_dump_json()
    first = check_citation_membership_v2(
        result, accepted_evidence_ids=[SPOT_A2, SPOT_A, SPOT_A],
        original_input_ids=["original-input-z", "original-input-a", "original-input-z"],
    )
    repeated = check_citation_membership_v2(
        result, accepted_evidence_ids=[SPOT_A, SPOT_A2],
        original_input_ids=["original-input-a", "original-input-z"],
    )
    assert first == repeated and first.disposition == "accepted"
    assert first.accepted_evidence_ids == [SPOT_A, SPOT_A2]
    assert first.original_input_ids == ["original-input-a", "original-input-z"]
    assert result.model_dump_json() == original
    empty = result.model_copy(update={"causal_case": None, "horizons": []})
    empty_check = check_citation_membership_v2(empty, accepted_evidence_ids=[SPOT_A],
                                              original_input_ids=[])
    assert empty_check.disposition == "not_applicable" and empty_check.entries == []
    assert empty_check.result_content_hash != first.result_content_hash


class SucceedThenRejectCitationHost(MaterialHost):
    """A real permitted second round changes only the top-level citation."""

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        response = await super().result(payload)
        events = json.loads(response.events_json)
        capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
            "structuredContent"
        ]
        if payload.generation == 1:
            synthesis = json.loads(response.final_response)
        else:
            synthesis = events[-1]["payload"]["event"]["data"]["result"]
            locator = _candidate(self.fixture, payload, evidence_id=LOCATOR_B,
                                 requirement_id="event_identity", source_id="controlled-search",
                                 authority="search_derived")
            locator = locator.model_copy(update={"round": 2})
            capability["evidence_candidates"].append(locator.model_dump(mode="json"))
            synthesis["causal_case"]["evidence_refs"] = [LOCATOR_B]
        for horizon in synthesis["horizons"]:
            horizon["action"] = "long"
        final_response = json.dumps(synthesis) if payload.generation == 1 else ""
        encoded = json.dumps(events)
        return response.model_copy(update={
            "events_json": encoded, "final_response": final_response,
            "result_hash": hashlib.sha256((final_response + "\0" + encoded).encode()).hexdigest(),
        })


@pytest.mark.asyncio
async def test_permitted_good_then_bad_round_retains_only_prior_selected_original(
    continuation_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    fixture = continuation_inputs
    request = _active(fixture)
    host = SucceedThenRejectCitationHost(fixture)
    runtime = DshWebResearchRuntime(DshSessionLinkService(fixture.database), host,
                                   poll_interval_seconds=0.001)
    graph = build_agentic_research_graph(runtime, ResearchEvidenceService(fixture.database),
                                       expected_lease_owner=OWNER)
    try:
        state = await graph.ainvoke(initial_research_state(request))
        final_ref = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
        with fixture.database.session() as session:
            final = binding.read_graph_final_in_session(
                session, run_id=request.run_id, reference=final_ref,
                expected_lease_owner=OWNER, require_current_permission=True,
            )
            last = binding.read_graph_round_in_session(
                session, run_id=request.run_id, reference=final.receipt.last_admission_ref,
                expected_lease_owner=OWNER, require_current_permission=True,
            )
            assert last.receipt.previous_ref is not None
            previous = binding.read_graph_round_in_session(
                session, run_id=request.run_id, reference=last.receipt.previous_ref,
                expected_lease_owner=OWNER, require_current_permission=True,
            )
            current = read_mapped_observation(session, last.receipt.observation_ref)
            assert previous.receipt.selected_synthesis_observation_ref is not None
            selected = read_mapped_observation(
                session, previous.receipt.selected_synthesis_observation_ref,
            )
            evidence = ResearchEvidenceService.list_run_evidence_in_session(session, request.run_id)
        assert [item.generation for item in host.submit_calls] == [1, 2]
        assert isinstance(previous.receipt, binding.ResearchGraphRoundAdmissionV2)
        assert isinstance(last.receipt, binding.ResearchGraphRoundAdmissionV2)
        assert isinstance(final.receipt, binding.ResearchGraphFinalProjectionV2)
        assert previous.receipt.citation_disposition == "accepted"
        assert previous.state["loop_should_continue"] is True
        assert last.receipt.citation_disposition == "rejected"
        assert last.receipt.citation_check is not None
        assert last.receipt.citation_check.rejected_evidence_ids == [LOCATOR_B]
        assert last.receipt.selected_synthesis_observation_ref == (
            previous.receipt.selected_synthesis_observation_ref
        )
        assert last.receipt.observation_ref != last.receipt.selected_synthesis_observation_ref
        assert last.receipt.latest_observation_ref == current.latest_transport_ref
        assert current.result.causal_case is not None
        assert current.result.causal_case.evidence_refs == [LOCATOR_B]
        assert final.result.status == "degraded"
        assert final.receipt.latest_citation_disposition == "rejected"
        assert final.receipt.citation_disposition == "accepted"
        assert final.result.causal_case == selected.result.causal_case
        assert all(LOCATOR_B not in item.evidence_refs for item in final.result.horizons)
        assert final.result.horizons
        assert {item.evidence_id: item.quality for item in evidence}[LOCATOR_B] == "candidate"
        _write_observation(tmp_path, "good-then-rejected-citation-original.json", {
            "fixture_boundary": "offline_factory_mcp_runtime_kernel_graph",
            "provider_executed": False, "physical_gateway_executed": False,
            "same_run_after_rejection_retry_authorized": False,
            "host_generations": [item.generation for item in host.submit_calls],
            "previous": previous.receipt.model_dump(mode="json"),
            "latest": last.receipt.model_dump(mode="json"),
            "final": final.receipt.model_dump(mode="json"),
            "latest_model_original": current.model_dump(mode="json"),
            "selected_model_original": selected.model_dump(mode="json"),
            "sql": _sql(fixture),
        })
    finally:
        await runtime.close()
