"""Actual native synthetic-child pipeline; every writable locator stays in D."""

import json
from datetime import timedelta
from pathlib import Path
from runpy import run_path
from typing import Any

import pytest
from pydantic import AnyUrl

# Candidate contracts must load before Main fixture consumers.
# ruff: noqa: E402

SUPPORT = run_path(str(Path(__file__).with_name("candidate_perpetual_review_support.py")))
SUPPORT["load_review_candidates"]()
LOAD = SUPPORT["load_candidate_module"]
for NAME in (
    "perpetual_report_delivery",
    "perpetual_research_report",
    "commit",
    "perpetual_saved_report",
):
    MODULE = LOAD(
        "packages.kernel.decision_hub_kernel.application." + NAME,
        "packages/kernel/decision_hub_kernel/application/" + NAME + ".py",
    )

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


@pytest.mark.parametrize("complete", [False, True])
def test_original_native_assessment_commits_v6_without_permission(
    binding_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete: bool,
) -> None:
    import importlib

    from tests.decision_product.test_perpetual_action_publication import (
        authenticate_provider,
        lawful_packet_for,
    )

    from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
    from packages.kernel.decision_hub_kernel.persistence.db import (
        ExecutionPermissionRecord,
        RunRecord,
        RunRoleOutputRecord,
    )
    from tests.decision_product import test_perpetual_native_graph as native

    candidate = Path(__file__).resolve().parents[2]
    original_run = native.subprocess.run

    def run_in_candidate(args: list[str], **kwargs: Any) -> Any:
        assert "tests/native-v2.spec.ts" in args
        kwargs["cwd"] = candidate / "extensions/dsh/decision-hub"
        kwargs["env"]["D2_REVISION_OUTPUT"] = str(tmp_path / "native-revision.json")
        assert Path(kwargs["env"]["D2_NATIVE_INPUT"]).is_relative_to(tmp_path)
        assert Path(kwargs["env"]["D2_NATIVE_OUTPUT"]).is_relative_to(tmp_path)
        if complete:
            path = Path(kwargs["env"]["D2_NATIVE_INPUT"])
            data = json.loads(path.read_bytes())
            refs = [
                row["original_ref"]
                for row in data["review_originals"]["originals"]
                if row["role"].startswith("completion-source-")
            ]
            assert len(refs) == 4
            data["report_assessment"] = {
                name: dict(
                    status="passed",
                    reason="Explicit synthetic child answer based on four retained unit sources.",
                    evidence_refs=refs,
                )
                for name in (
                    "frozen_information",
                    "probability_completeness",
                    "evidence_balance",
                    "cost_protection_and_conditions",
                )
            }
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return original_run(args, **kwargs)

    monkeypatch.setattr(native.subprocess, "run", run_in_candidate)

    def request_setup(case: dict[str, Any], factory: Any, _service: Any) -> tuple[Any, Any, Any]:
        request = factory.build(case["event_id"], case["run_id"])
        context, _ = native.prepare(case, request)
        from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
            read_perpetual_research_materials_in_session,
        )

        from packages.contracts_py.decision_hub_contracts import EvidenceCandidate
        from packages.kernel.decision_hub_kernel.application import research_evidence

        at = AT - timedelta(seconds=1)
        with monkeypatch.context() as patch:
            patch.setattr(research_evidence, "utcnow", lambda: at)
            for index in range(4):
                item = EvidenceCandidate(
                    evidence_id=f"completion-source-{index}",
                    requirement_id=request.evidence_requirements[0].requirement_id,
                    kind="official",
                    authority="official",
                    source_id=f"synthetic-completion-{index}",
                    source_url=AnyUrl("https://example.com/synthetic-completion"),
                    published_at=at,
                    observed_at=at,
                    received_at=at,
                    content_hash=native.observation_sha(dict(synthetic_completion=index)),
                    excerpt="Synthetic business fixture; never real market evidence.",
                    structured_payload_ref=None,
                    tool_call_id=None,
                    research_session_id="synthetic-completion",
                    round=1,
                    quality="candidate",
                    freshness_status="unknown",
                    conflict_group=None,
                )
                admitted = research_evidence.ResearchEvidenceService(case["db"]).accept_candidates(
                    run_id=request.run_id,
                    capability_id=request.allowed_capabilities[0],
                    candidates=[item],
                    requirements={
                        request.evidence_requirements[
                            0
                        ].requirement_id: request.evidence_requirements[0]
                    },
                    cutoff_at=at,
                )
                assert admitted[0].quality == "accepted"
        with case["db"].session() as session:
            materials = read_perpetual_research_materials_in_session(session, request)
        return request, context, materials

    def authored(request: Any, materials: dict[str, Any]) -> dict[str, Any]:
        packet = lawful_packet_for(request, materials)
        refs = [
            row["original_ref"]
            for row in materials["review_originals"]["originals"]
            if row["role"].startswith("completion-source-")
        ]
        assert len(refs) == 4
        packet["synthesis"]["reasoning_refs"] = refs[:3]
        packet["synthesis"]["analysis"]["main_chain"][0]["evidence_refs"] = refs[:3]
        packet["synthesis"]["analysis"]["opposite_chain"][0]["evidence_refs"] = refs[3:]
        packet["synthesis_ref"] = {
            **packet["synthesis_ref"],
            "hash": native.observation_sha(packet["synthesis"]),
        }
        return packet

    def authenticate(prepared: Any) -> None:
        authenticate_provider(prepared, monkeypatch)

    value = native.build_perpetual_native_graph_case(
        binding_case,
        tmp_path,
        monkeypatch,
        packet_builder=authored if complete else lawful_packet_for,
        request_setup=request_setup if complete else None,
        before_graph=authenticate,
        phased=True,
    )
    commit = importlib.import_module("packages.kernel.decision_hub_kernel.application.commit")
    monkeypatch.setattr(commit, "utcnow", lambda: AT + timedelta(seconds=30))
    artifact_id, gate = CommitDecisionService(
        value["db"], clock=lambda: AT + timedelta(seconds=30)
    ).commit_research_result(
        value["request"].run_id,
        value["request"].event_id,
        value["final"].result,
        expected_lease_owner=value["lease_owner"],
        graph_final_ref=value["graph_final_ref"],
    )
    with value["db"].session() as session:
        run = session.get(RunRecord, value["request"].run_id)
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=run.run_id, role="research_publication_source")
            .one()
        )
        assert run.status == ("completed" if complete else "degraded") and run.lease_owner is None
        assert source.schema_version == "research-publication-source.v6"
        saved = json.loads(source.payload_json)
        assert saved["report_delivery"]["status"] == ("complete" if complete else "incomplete")
        assert saved["report_delivery"]["decision_at"] == saved["recorded_at"]
        if not complete:
            assert (
                "review_check_incomplete:cost_protection_and_conditions"
                in saved["report_delivery"]["reasons"]
            )
        assert session.get(ExecutionPermissionRecord, artifact_id).allowed is False
        assert gate.status.value == "research_only"
        (tmp_path / "committed-source.v6.json").write_text(source.payload_json, encoding="utf-8")
    from tests.decision_product.test_perpetual_research_binding import OWNER

    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
        PerpetualSavedReportQuery,
    )

    body = PerpetualSavedReportQuery(value["db"], now=lambda: AT + timedelta(seconds=40)).read(
        OWNER, value["request"].run_id
    )
    assert body["financial_qualified"] is body["execution_authorized"] is False
    source_json = body["source_json"]
    assert isinstance(source_json, str)
    assert json.loads(source_json)["report_delivery"]["status"] == (
        "complete" if complete else "incomplete"
    )
    (tmp_path / "saved-report.v6.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8"
    )
    assert_source_assessment_tamper_rejected(value["db"], OWNER, value["request"].run_id)


def assert_source_assessment_tamper_rejected(database: Any, owner_id: str, run_id: str) -> None:
    """Outer valid SHA cannot authorize a changed, self-resealed completeness fact."""
    import hashlib

    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        observation_json,
    )

    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
        PerpetualSavedReportQuery,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord

    with database.session() as session:
        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=run_id, role="research_publication_source")
            .one()
        )
        original = row.payload_json, row.payload_hash, row.output_id
        forged = json.loads(row.payload_json)
        forged["report_delivery"]["input_hashes"]["findings"] = "f" * 64
        row.payload_json = observation_json(forged)
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
        row.output_id = "publication_" + row.payload_hash[:40]
    try:
        with pytest.raises(ValueError, match="perpetual_report_committed_original_mismatch"):
            PerpetualSavedReportQuery(database, now=lambda: AT + timedelta(seconds=40)).read(
                owner_id, run_id
            )
    finally:
        with database.session() as session:
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=run_id, role="research_publication_source")
                .one()
            )
            row.payload_json, row.payload_hash, row.output_id = original
