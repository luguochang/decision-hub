"""Real Provider service snapshot, advancing HTTP clock, actual registered native Tools."""

import hashlib
import json
import os
import subprocess
from datetime import timedelta
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.hub_api.perpetual_research_routes import perpetual_research_router
from packages.contracts_py.decision_hub_contracts import DshSessionResult
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputSubmit,
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application import (
    provider_input_receipts,
    research_graph_binding,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_perpetual_research_materials_in_session,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
    extract_attested_perpetual_synthesis_captures,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run
from tests.decision_product.perpetual_draft_fixture import draft_from_packet
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_action_publication import lawful_packet_for
from tests.decision_product.test_perpetual_native_graph import ROOT
from tests.decision_product.test_perpetual_research_binding import (
    AT,
    factory_for,
    prepare_execution,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_real_provider_snapshot_precedes_native_call_and_later_http_clock(
    binding_case, tmp_path, monkeypatch
):
    case = binding_case
    db = case["db"]
    factory, binding_service = factory_for(case, tmp_path)
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )

    manifest = yaml.safe_load((factory.pack_root / "pack.yaml").read_text("utf-8"))
    factory = type(factory)(
        db,
        pack_root=factory.pack_root,
        allowed_capabilities=factory.allowed_capabilities,
        execution_mode="live",
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
        perpetual_binding_service=binding_service,
        clock=lambda: AT,
    )
    request = factory.build(case["event_id"], case["run_id"])
    prepared = prepare_execution(case, request)
    with db.session() as session:
        materials = read_perpetual_research_materials_in_session(session, request)
    packet = lawful_packet_for(request, materials)
    packet["requested_at"] = (AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    draft = draft_from_packet(packet, materials["review_originals"])
    clock = [AT + timedelta(milliseconds=500)]
    monkeypatch.setattr(original_preparation, "utcnow", lambda: clock[0])
    monkeypatch.setattr(provider_input_receipts, "utcnow", lambda: clock[0])
    monkeypatch.setattr(research_graph_binding, "utcnow", lambda: clock[0])
    identity = prepared.method.definition.upstream_identity
    params = dict(
        provider="synthetic-clock-test",
        model="synthetic-child-backend",
        reasoning_effort=None,
        temperature=None,
        max_tokens=None,
        stop_sequences_hash=None,
    )
    ModelDispatchService(db, clock=lambda: clock[0]).bind(
        DshModelDispatchBinding(
            schema_version="dsh-model-dispatch-binding.v1",
            run_id=request.run_id,
            dsh_session_id=prepared.link.dsh_session_id,
            execution_session_id=None,
            parent_session_id=None,
            request_hash=research_request_hash(request),
            generation=1,
            plugin_build_hash=identity.plugin_build_hash,
            purpose="conversation",
            **params,
        )
    )
    shared = dict(
        run_id=request.run_id,
        generation=1,
        root_session_id=prepared.link.dsh_session_id,
        execution_session_id=prepared.link.dsh_session_id,
        dispatch_id="live-clock-dispatch",
        request_hash=research_request_hash(request),
        plugin_build_hash=identity.plugin_build_hash,
    )
    encoded = json.dumps(
        dict(
            system="Synthetic actual-provider clock regression.",
            messages=[dict(role="user", content=prepared.prompt.prompt)],
            tools=[dict(name="decision_hub_synthesis_submit")],
        ),
        separators=(",", ":"),
    )
    service = ProviderInputReceiptService(db, clock=lambda: clock[0])
    original = service.record_input(
        ResearchProviderInputSubmit(
            schema_version="research-provider-input-submit.v1",
            **shared,
            parent_session_id=None,
            **params,
            input_json=encoded,
            input_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            previous_input_ref=None,
            host_scope=dict(
                host_instance_id=str(uuid4()),
                process_id=os.getpid(),
                started_at=AT,
                hub_base_url="http://test",
                workspace_id="default",
                workspace_cwd_hash=hashlib.sha256(str(ROOT).encode()).hexdigest(),
            ),
        )
    )

    def emit(call_id, args, index):
        raw = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
        return service.record_emission(
            ResearchProviderToolEmissionSubmit(
                schema_version="research-provider-tool-emission-submit.v1",
                **shared,
                input_ref=original.reference.model_dump(
                    mode="json", include={"output_id", "payload_hash"}
                ),
                call_id=call_id,
                tool_name="decision_hub_synthesis_submit",
                raw_arguments_json=raw,
                raw_arguments_hash=hashlib.sha256(raw.encode()).hexdigest(),
                arguments_hash=hashlib.sha256(tool_arguments_json(args).encode()).hexdigest(),
                block_index=index,
            )
        )

    clock[0] = AT + timedelta(seconds=1)
    args = dict(action="prepare", request_id=request.request_id, draft=draft)
    assert emit("freeze", args, 0).candidate_hash is None
    emit(
        "not-prepare",
        dict(action="finalize", request_id=request.request_id, prepared_call_id="freeze"),
        1,
    )
    app = FastAPI()

    def auth(key):
        if key != "private-clock-test":
            raise HTTPException(401, "unauthorized")

    http_at = AT + timedelta(milliseconds=2500)
    app.include_router(perpetual_research_router(db, auth, now=lambda: http_at))
    client = TestClient(app)
    route = f"/v1/research/runs/{request.run_id}/decision-binding"
    params_query = dict(
        request_hash=research_request_hash(request), generation=1, source_call_id="freeze"
    )
    headers = {"X-Decision-Hub-Bridge-Key": "private-clock-test"}
    response = client.get(route, params=params_query, headers=headers)
    assert response.status_code == 200, response.text
    catalog = response.json()["review_originals"]
    assert catalog["as_of"] == (AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    for bad in ("missing", "not-prepare"):
        assert (
            client.get(
                route, params={**params_query, "source_call_id": bad}, headers=headers
            ).status_code
            == 409
        )
    assert (
        client.get(route, params={**params_query, "generation": 2}, headers=headers).status_code
        == 409
    )
    initial = client.get(
        route,
        params={k: v for k, v in params_query.items() if k != "source_call_id"},
        headers=headers,
    )
    assert (
        initial.status_code == 200
        and initial.json()["review_originals"]["as_of"] <= catalog["as_of"]
    )
    # Same exact server snapshot feeds actual registered prepare/reviewer/finalize.
    input_path = tmp_path / "clock-input.json"
    output_path = tmp_path / "clock-output.json"
    input_path.write_text(
        json.dumps(
            dict(
                request=request.model_dump(mode="json"),
                request_hash=research_request_hash(request),
                review_request=packet,
                review_originals=catalog,
                draft=draft,
            )
        ),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        D2_NATIVE_INPUT=str(input_path),
        D2_NATIVE_OUTPUT=str(output_path),
        D2_WRITE_NATIVE_FIXTURE="1",
    )
    completed = subprocess.run(
        [
            str(ROOT / "apps/decision-desk/node_modules/.bin/vitest.cmd"),
            "run",
            "tests/native-v2.spec.ts",
            "--environment",
            "node",
            "-t",
            "prepares one authored draft",
        ],
        cwd=ROOT / "extensions/dsh/decision-hub",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    transcript = json.loads(output_path.read_text("utf-8"))
    events = [
        dict(method="session.event", payload=dict(sessionId=prepared.link.dsh_session_id, event=e))
        for e in transcript["context"]["events"]
    ]
    encoded_events = json.dumps(events)
    payload = DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id=request.run_id,
        dsh_session_id=prepared.link.dsh_session_id,
        generation=1,
        last_seq=7,
        final_response="",
        finish_reason="completed",
        events_json=encoded_events,
        started_at=AT,
        finished_at=AT + timedelta(seconds=5),
        trace_ref="synthetic://advancing-clock",
        result_hash=hashlib.sha256(("\0" + encoded_events).encode()).hexdigest(),
    )
    captures = extract_attested_perpetual_synthesis_captures(
        _sdk_run(payload).notifications,
        expected_session_id=prepared.link.dsh_session_id,
        request=request,
    )
    assert len(captures) == 1 and captures[0].authorization.verdict == "approved"
    assert transcript["context"]["events"][1]["time"] > int(
        (AT + timedelta(seconds=1)).timestamp() * 1000
    )
    clock[0] = AT + timedelta(milliseconds=3500)
    assert (
        emit(
            "submit",
            dict(action="finalize", request_id=request.request_id, prepared_call_id="freeze"),
            2,
        ).candidate_hash
        is None
    )
