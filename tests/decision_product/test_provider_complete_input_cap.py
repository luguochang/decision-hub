"""Complete input budget only; real SQLite originals, no model or Graph execution."""

import hashlib
import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputSubmit,
)
from packages.kernel.decision_hub_kernel.application import (
    provider_input_receipts,
    research_graph_binding,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import (
    AT,
    factory_for,
    prepare_execution,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_real_sqlite_records_and_reopens_complete_input_above_one_megabyte(binding_case, tmp_path, monkeypatch):
    case = binding_case
    factory, service = factory_for(case, tmp_path)
    manifest = yaml.safe_load((factory.pack_root / "pack.yaml").read_text("utf-8"))
    factory = type(factory)(
        case["db"], pack_root=factory.pack_root, execution_mode="live",
        allowed_capabilities=factory.allowed_capabilities,
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"], rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ), perpetual_binding_service=service, clock=lambda: AT,
    )
    request = factory.build(case["event_id"], case["run_id"])
    execution = prepare_execution(case, request)
    now = lambda: AT + timedelta(seconds=1)
    monkeypatch.setattr(original_preparation, "utcnow", now)
    monkeypatch.setattr(provider_input_receipts, "utcnow", now)
    monkeypatch.setattr(research_graph_binding, "utcnow", now)
    params = dict(provider="synthetic-cap-test", model="no-model", reasoning_effort=None,
                  temperature=None, max_tokens=None, stop_sequences_hash=None)
    ModelDispatchService(case["db"], clock=lambda: AT + timedelta(milliseconds=500)).bind(
        DshModelDispatchBinding(
            schema_version="dsh-model-dispatch-binding.v1", run_id=request.run_id,
            dsh_session_id=execution.link.dsh_session_id, execution_session_id=None,
            parent_session_id=None, request_hash=execution.method.request_hash, generation=1,
            plugin_build_hash=execution.method.definition.upstream_identity.plugin_build_hash,
            purpose="conversation", **params,
        )
    )
    body = dict(system="x" * 1_100_000, messages=[dict(role="user", content=execution.prompt.prompt)], tools=[])
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    payload = ResearchProviderInputSubmit(
        schema_version="research-provider-input-submit.v1", run_id=request.run_id, generation=1,
        root_session_id=execution.link.dsh_session_id, execution_session_id=execution.link.dsh_session_id,
        parent_session_id=None, dispatch_id="synthetic-complete-cap", request_hash=execution.method.request_hash,
        plugin_build_hash=execution.method.definition.upstream_identity.plugin_build_hash,
        **params, input_json=encoded, input_hash=hashlib.sha256(encoded.encode()).hexdigest(), previous_input_ref=None,
        host_scope=dict(host_instance_id=str(uuid4()), process_id=os.getpid(), started_at=AT,
                        hub_base_url="http://test", workspace_id="default", workspace_cwd_hash="a" * 64),
    )
    recorded = provider_input_receipts.ProviderInputReceiptService(case["db"], clock=now).record_input(payload)
    with case["db"].session() as session:
        session.connection().exec_driver_sql("BEGIN")
        reopened, _ = provider_input_receipts.read_provider_input_in_session(session, recorded.reference)
        assert reopened.submit.input_json == encoded
        assert reopened.submit.input_hash == hashlib.sha256(encoded.encode()).hexdigest()
        assert json.loads(reopened.submit.input_json) == body


def test_complete_input_utf8_ceiling_does_not_expand_generic_tool_and_finish_json():
    # DTO character bounds alone cannot enforce a UTF-8 byte budget.
    body = json.dumps(dict(messages=[], system="é" * (4 * 1024 * 1024), tools=[]), ensure_ascii=False)
    with pytest.raises(ValueError, match="research_provider_bytes_exceeded"):
        provider_input_receipts._validate_input(type("Input", (), {"input_json": body})())
    with pytest.raises(ValueError, match="research_provider_bytes_exceeded"):
        provider_input_receipts.strict_json(json.dumps({"argument": "x" * 1_000_001}))
    # The canonical input field also refuses strings longer than the dedicated cap.
    field = ResearchProviderInputSubmit.model_fields["input_json"]
    from pydantic import TypeAdapter

    with pytest.raises(ValidationError):
        TypeAdapter(field.rebuild_annotation()).validate_python("x" * (8 * 1024 * 1024 + 1))
