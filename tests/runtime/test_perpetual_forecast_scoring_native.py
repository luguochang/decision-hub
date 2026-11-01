"""Fresh original native synthetic-child Commit -> publisher -> due; never live qualification."""

import asyncio
import json
from datetime import datetime, timedelta
from runpy import run_path
from typing import Any

import httpx
from candidate_perpetual_forecast_support import ROOT

# Match the original TaskB entry's collection-time contract installation. Loading
# after a fixture constructs settings would mix distinct Pydantic class identities.
NATIVE_ENTRY = run_path(str(ROOT / "tests/runtime/test_perpetual_report_completion_commit.py"))

# Fixture implementations are original Main; the called fresh native test pins
# its exact candidate output directory and subprocess boundary itself.
# ruff: noqa: E402
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_fresh_v6_commit_requires_original_action_publication_before_due(
    binding_case: Any,
    tmp_path: Any,
    monkeypatch: Any,
):
    # This expensive native boundary is run only after Root's normal launch ends.
    with monkeypatch.context() as native_patch:
        NATIVE_ENTRY["test_original_native_assessment_commits_v6_without_permission"](
            binding_case, tmp_path, native_patch, True
        )
    assert_committed_v6_publisher_due_and_use(binding_case, tmp_path, monkeypatch)


def assert_committed_v6_publisher_due_and_use(
    binding_case: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    from test_perpetual_forecast_scoring_runtime import runtime_module

    module = runtime_module()
    from packages.kernel.decision_hub_kernel.application.action_publication import (
        ActionPublicationService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_admission_identity_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        GraphFinalProjectionRef,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import (
        RunRecord,
        RunRoleOutputRecord,
        utcnow,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import millis
    from tests.decision_product.test_perpetual_research_binding import AT, OWNER

    case = binding_case
    with case["db"].session() as session:
        run = session.get(RunRecord, case["run_id"])
        artifact, event = run.artifact_id, run.event_id
        assert run.status == "completed"
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], role="research_publication_source")
            .one()
        )
        body = json.loads(source.payload_json)
        assert body["schema_version"] == "research-publication-source.v6"
        assert body["report_delivery"]["status"] == "complete"
        reference = GraphFinalProjectionRef.model_validate_json(json.dumps(body["graph_final_ref"]))
        identity = read_perpetual_admission_identity_in_session(session, case["run_id"])
        assert identity is not None
        admission = identity[0]
        assert (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], schema_version="publication-bound-target.v1")
            .count()
            == 0
        )
    holder, calls = {"at": utcnow() + timedelta(minutes=1)}, []
    end_holder = {}

    def venue(request: Any):
        calls.append(str(request.url))
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        end = end_holder["end"]
        assert request.url.params["after"] == str(millis(end))
        rows = []
        for index in range(241):
            price = "100" if index == 240 else "110"
            rows.append(
                [
                    str(millis(end) - (index + 1) * 60000),
                    price,
                    price,
                    price,
                    price,
                    "7",
                    "0.07",
                    "7",
                    "1",
                ]
            )
        return httpx.Response(200, json={"code": "0", "data": rows})

    runtime = module.PerpetualForecastRuntime(
        case["db"],
        case["source"],
        scoring_version="v2",
        clock=lambda: holder["at"],
        http_transport=httpx.MockTransport(venue),
    )
    assert asyncio.run(runtime.process_due()) == 0 and calls == []
    published = ActionPublicationService(
        case["db"], clock=lambda: AT + timedelta(seconds=40)
    ).publish(
        run_id=case["run_id"],
        event_id=event,
        artifact_id=artifact,
        reference=reference,
        opportunity_id=admission["opportunity_id"],
    )
    assert published.response["status"] == "published"
    with case["db"].session() as session:
        target = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], schema_version="publication-bound-target.v1")
            .one()
        )
        end_holder["end"] = datetime.fromisoformat(
            json.loads(target.payload_json)["window_end"].replace("Z", "+00:00")
        )
    assert asyncio.run(runtime.process_due()) == 1 and len(calls) == 1
    assert asyncio.run(runtime.process_due()) == 0 and len(calls) == 1
    from candidate_perpetual_forecast_support import load

    load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring_view.py")
    query = load(
        "packages/kernel/decision_hub_kernel/application/perpetual_forecast_result_view.py"
    )
    result = query.PerpetualForecastResultQuery(case["db"], now=lambda: holder["at"]).read(
        OWNER, case["run_id"]
    )
    assert (
        result["schema_version"] == "perpetual-forecast-result-view.v2"
        and result["status"] == "observed"
    )
    assert result["result"]["financial_qualified"] is result["result"]["skill_consumed"] is False
    (tmp_path / "native-scoring-result.v2.json").write_text(json.dumps(result), encoding="utf-8")
    with case["db"].session() as session:
        original = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], role="perpetual_forecast_experience_v2")
            .one()
        )
        experience_ref = {"ref": original.output_id, "hash": original.payload_hash}
    from candidate_perpetual_scoring_provider_support import (
        assert_original_next_task_and_provider_use,
    )

    asyncio.run(
        assert_original_next_task_and_provider_use(
            case, tmp_path, monkeypatch, holder, runtime.tick, experience_ref, calls
        )
    )
