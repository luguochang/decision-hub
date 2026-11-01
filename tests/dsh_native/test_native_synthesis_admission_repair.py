"""Native Factory/SQLite repair uses accepted IDs and never repeats acquisition."""
import hashlib
import json
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionSubmit,
    EvidenceRequirement,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs, inputs
from tests.dsh_native.test_runtime_execution_v2 import (
    PreparedHost,
    _sql_repairable_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


@pytest.mark.parametrize("invalid_repair", [False, True])
async def test_native_candidate_ref_enters_one_synthesis_only_repair(
    inputs: PreparedInputs, tmp_path: Path, invalid_repair: bool,
) -> None:
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, inputs.request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    # Select one originally declared soft requirement, as the real Graph can;
    # no caller invents a new permission or changes the frozen Task.
    request = inputs.request.model_copy(deep=True)
    request.evidence_requirements = [next(
        EvidenceRequirement.model_validate(item.model_dump(mode="json"))
        for item in bundle.task.request.evidence_requirements if item.importance == "soft"
    )]
    request.execution_budget.max_structured_repairs = 1
    captured_results: list[DshSessionResult] = []
    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            self.result_calls += 1
            original = _sql_repairable_result(inputs, payload)
            events = json.loads(original.events_json)
            capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
                "structuredContent"]
            accepted = capability["evidence_candidates"][0]
            candidate = {**accepted, "evidence_id": "ev-unaccepted-context", "quality": "candidate"}
            capability["evidence_candidates"].append(candidate)
            if payload.generation == 1:
                synthesis = json.loads(original.final_response)
                synthesis["horizons"][0]["evidence_refs"] = [candidate["evidence_id"]]
                final_response = json.dumps(synthesis)
            else:
                if invalid_repair:
                    repaired = events[-1]["payload"]["event"]["data"]["message"]["content"][0][
                        "structuredContent"]
                    repaired["horizons"][0]["evidence_refs"] = [candidate["evidence_id"]]
                final_response = original.final_response
            encoded = json.dumps(events)
            value = original.model_copy(update={"events_json": encoded,
                "final_response": final_response, "result_hash": hashlib.sha256(
                    (final_response + "\0" + encoded).encode()).hexdigest()})
            captured_results.append(value)
            return value

    host = Host(inputs)
    links = DshSessionLinkService(inputs.database)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=.001)
    try:
        result = await runtime.execute(request)
        assert [submission.generation for submission in host.submit_calls] == [1, 2]
        assert host.result_calls == 2
        newer_calls = [
            block["toolName"]
            for notification in json.loads(captured_results[1].events_json)
            if notification["payload"]["event"]["seq"] > captured_results[0].last_seq
            and notification["payload"]["event"]["type"] == "tool/call"
            for block in notification["payload"]["event"]["data"]["message"]["content"]
            if block["type"] == "tool-call"
        ]
        assert newer_calls == ["decision_hub_synthesis_submit"]
        prompt = links.get_prompt(inputs.request.run_id, 2)
        assert prompt is not None
        assert "ev-unaccepted-context is not an allowed Evidence ID" in prompt.prompt
        whitelist = prompt.prompt.split(
            "Allowed Evidence ID whitelist (copy exact strings; use no other identifier):\n", 1
        )[1].split("\n", 1)[0]
        assert "ev-unaccepted-context" not in json.loads(whitelist)
        assert "ev-partial-provider-failure" in json.loads(whitelist)
        assert len(result.evidence_candidates) == 2
        assert result.evidence_candidates[1].quality == "candidate"
        if invalid_repair:
            assert result.horizons == [] and result.causal_case is None
            assert result.synthesis_failure_code == "dsh_evidence_unattested"
        else:
            assert result.synthesis_failure_code is None
            assert result.horizons[0].evidence_refs == ["ev-partial-provider-failure"]
        # Generation two contains only the captured synthesis call; the retained
        # acquisition events are the originals at seq 1/2, not a new execution.
        (tmp_path / "repair-observation.json").write_text(json.dumps({
            "submitted_generations": [p.generation for p in host.submit_calls],
            "actual_host_result_calls": host.result_calls,
            "invalid_repair": invalid_repair,
            "synthesis_failure": result.synthesis_failure_code,
            "horizon_refs": [h.evidence_refs for h in result.horizons],
            "retained_quality": [e.quality for e in result.evidence_candidates],
            "whitelist": json.loads(whitelist), "external_connections": 0,
            "new_generation_tool_calls": newer_calls,
        }, indent=2), encoding="utf-8")
    finally:
        await runtime.close()
